"""Current official Villa problems, mapped onto the ARGUS user journey."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from argus.core import paths
from argus.core import update_store

CHECKED_IN = Path(__file__).resolve().parents[2] / "config" / "villa_open_issues_snapshot.json"

GROUPS = (
    {
        "id": "acquisition_integrity", "label": "Acquire and verify CT",
        "route_steps": ["acquisition", "ct_inspection"],
        "pattern": r"zarr|chunk|dataset|volume|download|fetch|cache|pyramid|tiff|s3|store|bounds|truncat",
        "argus_state": "GUARDED",
        "argus_answer": "Bounded plans, byte ceilings, identity-bound stores, resumable transfer and fail-closed integrity checks.",
        "remaining": "Upstream stores and tools can still be incomplete or malformed; the guard refuses them rather than repairing every publisher.",
    },
    {
        "id": "identity_coordinates", "label": "Identity, coordinates and transforms",
        "route_steps": ["exact_identity", "normal_orientation"],
        "pattern": r"transform|landmark|bbox|coordinate|indices|index|orientation|normal.grid|scale|depth",
        "argus_state": "GUARDED",
        "argus_answer": "Physical-scroll identity, acquisition binding, coordinate handshakes, sampled chunk identities and orientation evidence stay explicit.",
        "remaining": "A guard catches contradictions; it does not manufacture the missing authoritative transform or normal field.",
    },
    {
        "id": "surface_unwrapping", "label": "Segment and unwrap the sheet",
        "route_steps": ["surface_prediction", "tracing", "topology", "flatten_render"],
        "pattern": r"segment|surface|mesh|spiral|winding|flatten|render|trac|wrap|topolog|tifxyz|grow|fibre|fiber",
        "argus_state": "PARTIAL",
        "argus_answer": "ARGUS wires prediction, tracing, topology controls, flattening and rendering behind one selected-scroll route and retains every intermediate.",
        "remaining": "Compressed and fused sheets still need better automatic tracing, topology repair and reproducible flattening on real scrolls.",
    },
    {
        "id": "ink_generalization", "label": "Detect ink that generalizes",
        "route_steps": ["ink_inference", "evidence_comparison", "candidate_review"],
        "pattern": r"ink|auc|checkpoint|inference|label|training|prediction|model|detector|loss|target",
        "argus_state": "EXTERNAL_PROOF_REQUIRED",
        "argus_answer": "Models, exposure, held-out controls and candidate evidence are inventoried and gated; unqualified detector output remains visible but cannot become a reading.",
        "remaining": "A detector that generalizes to genuinely unread scroll material at the release threshold is still unproven.",
    },
    {
        "id": "reliability_delivery", "label": "Run, resume, install and reproduce",
        "route_steps": ["export_packet"],
        "pattern": r"windows|mps|crash|segfault|hang|thread|retry|reproduc|install|memory|vram|performance|cli|readme|error|abort",
        "argus_state": "GUARDED",
        "argus_answer": "Pinned providers, explicit runtime identity, resumable jobs, receipts, resource floors and governed updates make failures inspectable and recoverable.",
        "remaining": "Platform-specific upstream failures remain open until their code lands and passes ARGUS adapter and real-data controls.",
    },
)


def _live_path() -> Path:
    return update_store.root() / "villa_open_issues.json"


def _read(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("issues"), list) else None


def _atomic_write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _normalise(raw: list, observed_utc: str, source: str) -> dict:
    issues = []
    for row in raw:
        try:
            number = int(row["number"])
        except (KeyError, TypeError, ValueError):
            continue
        issues.append({
            "number": number,
            "title": str(row.get("title") or "Untitled issue")[:500],
            "url": str(row.get("url") or f"https://github.com/ScrollPrize/villa/issues/{number}"),
            "updated_at": row.get("updatedAt") or row.get("updated_at"),
            "labels": [str(x.get("name") if isinstance(x, dict) else x) for x in (row.get("labels") or [])],
        })
    issues.sort(key=lambda x: x["number"], reverse=True)
    return {
        "schema": "argus-villa-open-issues-snapshot-v1", "repo": "ScrollPrize/villa",
        "observed_utc": observed_utc, "source": source, "open_count": len(issues), "issues": issues,
    }


def refresh(*, runner) -> dict:
    """Refresh after provider-update consent has already been checked by the caller."""
    argv = ["gh", "issue", "list", "-R", "ScrollPrize/villa", "--state", "open",
            "--limit", "200", "--json", "number,title,url,updatedAt,labels"]
    rc, out, err = runner(argv)
    if rc:
        return {"ok": False, "error": (err or out or "issue observation failed")[:300]}
    try:
        raw = json.loads(out)
    except ValueError as exc:
        return {"ok": False, "error": "issue observation returned invalid JSON: %s" % exc}
    if not isinstance(raw, list):
        return {"ok": False, "error": "issue observation returned no list"}
    doc = _normalise(raw, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "LIVE_UPDATE_CHECK")
    _atomic_write(_live_path(), doc)
    return {"ok": True, "open_count": doc["open_count"], "observed_utc": doc["observed_utc"]}


def read() -> dict:
    live, checked = _read(_live_path()), _read(CHECKED_IN)
    doc = live or checked or {"issues": [], "open_count": 0, "observed_utc": None, "source": "UNAVAILABLE"}
    issues = doc.get("issues", [])
    grouped = {g["id"]: [] for g in GROUPS}
    grouped["other"] = []
    for issue in issues:
        title = str(issue.get("title") or "")
        match = next((g for g in GROUPS if re.search(g["pattern"], title, re.I)), None)
        grouped[match["id"] if match else "other"].append(issue)
    groups = [{k: v for k, v in g.items() if k != "pattern"} | {
        "count": len(grouped[g["id"]]), "issues": grouped[g["id"]]
    } for g in GROUPS]
    if grouped["other"]:
        groups.append({
            "id": "other", "label": "Other Villa tooling and documentation", "route_steps": [],
            "argus_state": "TRACKED", "argus_answer": "Tracked by the same update surface; not silently classified as a pipeline claim.",
            "remaining": "These issues need individual triage before ARGUS can say which user journey they affect.",
            "count": len(grouped["other"]), "issues": grouped["other"],
        })
    return {
        "schema": "argus-open-problems-map-v1", "read_only": True,
        "official_challenge": {
            "url": "https://scrollprize.org/", "tracks": [
                {"id": "virtual_unwrapping", "label": "Virtual Unwrapping", "meaning": "segment, mesh and flatten compressed sheets"},
                {"id": "ink_detection", "label": "Ink Detection", "meaning": "detect ink robustly across scrolls"},
            ],
        },
        "villa": {"repo": "ScrollPrize/villa", "issues_url": "https://github.com/ScrollPrize/villa/issues",
                  "open_count": len(issues), "observed_utc": doc.get("observed_utc"),
                  "snapshot_source": doc.get("source"), "live": bool(live), "groups": groups},
        "claim_boundary": ("ARGUS aims to provide one governed route across the whole problem. "
                           "GUARDED means it detects or refuses a known failure; PARTIAL means real "
                           "capability exists but the open problem is not solved; EXTERNAL_PROOF_REQUIRED "
                           "means no release claim is allowed without new real-scroll evidence."),
        "update_rule": ("The provider update policy refreshes this issue snapshot together with upstream "
                        "metadata. Merely opening this page never contacts GitHub or installs code."),
    }
