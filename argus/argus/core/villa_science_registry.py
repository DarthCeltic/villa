"""Live, read-only map of science merged into Villa's tracked ``origin/main``."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from argus.core import paths, upstream_status

SCHEMA = "argus-villa-science-registry-v1"

COMPONENTS = (
    ("vesuvius", "vesuvius", ("catalog", "raw_ct", "surface_prediction", "inference")),
    ("foundation", "foundation", ("model_runtime",)),
    ("volume_cartographer_vc3d", "volume-cartographer",
     ("mesh_tracing", "topology_repair", "flatten", "render", "inspection", "annotation", "fiber_tracing")),
    ("lasagna", "lasagna", ("surface_prediction", "multi_sheet_fit", "flatten")),
    ("spiral_fitting", "spiral-fitting", ("surface_prediction", "mesh_tracing")),
    ("segmentation", "segmentation", ("surface_prediction", "training")),
    ("dinovol", "dinovol", ("representation", "ink_3d")),
    ("ink_detection", "ink-detection", ("ink_2d", "ink_3d", "metrics")),
)


def _git(root: Path, *args: str) -> tuple[int | None, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", str(exc)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _ledger() -> list[dict]:
    p = Path(__file__).resolve().parents[2] / "argus" / "capabilities.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("capabilities") if isinstance(doc, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def _component(root: Path, stable: str, candidate: str, component_id: str,
               component_path: str, stages: tuple[str, ...], ledger: list[dict]) -> dict:
    rc, latest, err = _git(root, "log", "-1", "--format=%H%x09%cI%x09%s", candidate,
                           "--", component_path)
    cr, count, count_err = _git(root, "rev-list", "--count", f"{stable}..{candidate}",
                                "--", component_path)
    dr, changed, diff_err = _git(root, "diff", "--name-only", f"{stable}..{candidate}",
                                 "--", component_path)
    lr, log, log_err = _git(root, "log", "--format=%H%x09%cI%x09%s",
                            f"{stable}..{candidate}", "--", component_path)
    matching = [row for row in ledger
                if str(row.get("upstream_path") or "").replace("\\", "/").startswith(component_path + "/")]
    adapted = [row for row in matching if row.get("argus_adapter")]
    controlled = [row for row in matching if row.get("regression_test")]
    merged = []
    for line in log.splitlines():
        bits = line.split("\t", 2)
        if len(bits) != 3:
            continue
        pr = re.search(r"\(#(\d+)\)\s*$", bits[2])
        merged.append({"commit": bits[0], "committed": bits[1], "subject": bits[2],
                       "pull_request": int(pr.group(1)) if pr else None})
    files = [line for line in changed.splitlines() if line.strip()]
    if rc not in (0,) or cr not in (0,) or dr not in (0,) or lr not in (0,):
        state = "UNKNOWN"
    elif not files:
        state = "STABLE_UNCHANGED"
    elif matching and len(controlled) == len(matching):
        state = "CANDIDATE_REQUIRES_DIFFERENTIAL"
    elif adapted:
        state = "CANDIDATE_PARTIALLY_ADAPTED"
    else:
        state = "CANDIDATE_NOT_ADAPTED"
    latest_bits = latest.split("\t", 2)
    return {
        "id": component_id,
        "path": component_path,
        "stages": list(stages),
        "state": state,
        "commits_since_stable": int(count) if count.isdigit() else None,
        "changed_files": files[:200],
        "changed_files_truncated": len(files) > 200,
        "latest": ({"commit": latest_bits[0], "committed": latest_bits[1],
                    "subject": latest_bits[2]} if len(latest_bits) == 3 else None),
        "merged_changes": merged,
        "argus": {
            "declared_capabilities": [row.get("capability_id") for row in matching],
            "adapted_capabilities": [row.get("capability_id") for row in adapted],
            "regression_locked_capabilities": [row.get("capability_id") for row in controlled],
        },
        "errors": [e for e in (err, count_err, diff_err, log_err) if e] or [],
    }


def inventory() -> dict:
    """Describe every Villa science component at stable and tracked-main revisions."""
    status = upstream_status.read()
    root = paths.villa()
    stable = str(((status.get("pin") or {}).get("declared") or "")).strip()
    candidate = str(((status.get("remote_tracking") or {}).get("sha") or "")).strip()
    if not root.is_dir() or not stable or not candidate:
        return {
            "schema": SCHEMA, "read_only": True, "state": "UNKNOWN",
            "stable": stable or None, "candidate": candidate or None, "components": [],
            "why": "the Villa checkout, stable pin, or locally-fetched origin/main is unavailable",
            "automatic": {"discover": True, "fetch_owner": "argus.core.upstream_ingest.refresh",
                          "promote": False},
        }
    rows = [_component(root, stable, candidate, *spec, _ledger()) for spec in COMPONENTS]
    changed = [row for row in rows if row["state"] != "STABLE_UNCHANGED"]
    return {
        "schema": SCHEMA,
        "read_only": True,
        "state": "CANDIDATE_DRIFT" if candidate != stable else "STABLE_CURRENT",
        "stable": stable,
        "candidate": candidate,
        "candidate_subject": (status.get("remote_tracking") or {}).get("subject"),
        "components": rows,
        "counts": {
            "components": len(rows),
            "changed": len(changed),
            "not_adapted": sum(row["state"] == "CANDIDATE_NOT_ADAPTED" for row in rows),
            "partially_adapted": sum(row["state"] == "CANDIDATE_PARTIALLY_ADAPTED" for row in rows),
        },
        "automatic": {
            "discover": True,
            "fetch_owner": "argus.core.upstream_ingest.refresh",
            "stage_and_test": True,
            "promote": False,
            "why": "merged main is a candidate until its differential and controls pass; existing receipts retain stable bytes",
        },
        "claim_ceiling": "source discovery and adapter coverage only; merged code is not a qualified scientific result",
    }
