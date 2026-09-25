"""Fiber capabilities for a bound task, five separate rows, derived from the imported science contracts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA_FILE = "FIBER_SCHEMA_CONTRACT.json"
WEIGHTS_FILE = "FIBER_WEIGHTS_INTAKE_CONTRACT.json"
ORDER = ("presence", "hv_class", "direction", "adjacency", "tracing")
ADJACENCY_DECLARING = ("NONE_DECLARED", "LINKS_PRESENT")


def own_kind(capability: str) -> str:
    """The one kind of output that can satisfy a capability's gate."""
    return "adjacency_declaration" if capability == "adjacency" else "fiber_" + capability


def satisfies(capability: str, artifact_kind: str, *, adjacency_kind: str | None = None) -> bool:
    """A gate is satisfied only by its own output kind."""
    if capability not in ORDER or artifact_kind != own_kind(capability):
        return False
    if capability == "adjacency":
        return adjacency_kind in ADJACENCY_DECLARING
    return True


def _read(directory: Path, name: str, receipt: dict) -> dict | None:
    p = directory / name
    if not p.is_file():
        return None
    raw = p.read_bytes()
    entry = next((f for f in receipt.get("imported_files", []) if f.get("stored_as") == name), None)
    if entry is None or entry.get("sha256") != hashlib.sha256(raw).hexdigest():
        return None
    body = json.loads(raw.decode("utf-8"))
    body["_sha256"] = entry["sha256"]
    return body


def _not_imported(why: str) -> list[dict]:
    return [{"capability": c, "state": "CONTRACT_NOT_IMPORTED", "reason": why, "satisfied_only_by": own_kind(c), "never_satisfied_by": [own_kind(o) for o in ORDER if o != c]}
            for c in ORDER]


def matrix(directory: Path | None = None) -> dict:
    from argus.core import workbench_task_binding as TB

    d = Path(directory) if directory else TB.import_dir()
    receipt_p = d / "IMPORT_RECEIPT.json"
    if not receipt_p.is_file():
        return {"rows": _not_imported("the science contracts are not imported into this checkout"), "rule": None, "contracts": []}
    receipt = json.loads(receipt_p.read_text(encoding="utf-8"))
    schema, weights = _read(d, SCHEMA_FILE, receipt), _read(d, WEIGHTS_FILE, receipt)
    if schema is None or weights is None:
        return {"rows": _not_imported("a fiber contract is missing or does not match its recorded import hash"), "rule": None, "contracts": []}

    caps = schema.get("capabilities_four_separate", {})
    upstream = schema.get("fiber_hz_vt_capabilities", {})
    census = ((schema.get("census_over_457_official_fibers_adjacency_kind") or {}).get("total")) or {}
    adjacency_semantics = schema.get("adjacency_kind_semantics", {})
    not_published = any(str(a.get("state", "")).upper().startswith("NOT PUBLISHED") for a in weights.get("promised_artifacts", []))
    rows = []
    for c in ORDER:
        row = {"capability": c, "satisfied_only_by": own_kind(c), "never_satisfied_by": [own_kind(o) for o in ORDER if o != c]}
        if c == "adjacency":
            declares = int(census.get("NONE_DECLARED", 0)) + int(census.get("LINKS_PRESENT", 0))
            row.update({"description": schema.get("topology_linking"),
                        "state": "UNKNOWN_LEGACY" if declares == 0 else "UNAVAILABLE_FOR_TASK",
                        "reason": (adjacency_semantics.get("UNKNOWN_LEGACY") if declares == 0 else "adjacency declarations exist in some official fibers, but none is bound to this task"),
                        "upstream_capability": None, "evidence_kind_required": "adjacency declaration of NONE_DECLARED or LINKS_PRESENT; UNKNOWN_LEGACY is never evidence"})
        elif c == "direction":
            status = weights.get("status")
            row.update({"description": caps.get(c),
                        "state": status if (status and not_published) else "UNAVAILABLE_FOR_TASK",
                        "reason": "; ".join("%s: %s" % (a.get("id"), a.get("state")) for a in weights.get("promised_artifacts", [])) or "no directional artifact is bound to this task",
                        "upstream_capability": bool(upstream.get(c))})
        else:
            row.update({"description": caps.get(c), "state": "UNAVAILABLE_FOR_TASK",
                        "reason": "no %s artifact is bound to this task's volume and region" % c, "upstream_capability": bool(upstream.get(c))})
        rows.append(row)
    return {"rows": rows, "rule": caps.get("rule"),
            "contracts": [{"file": SCHEMA_FILE, "sha256": schema["_sha256"]}, {"file": WEIGHTS_FILE, "sha256": weights["_sha256"]}]}
