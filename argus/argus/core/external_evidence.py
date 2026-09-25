"""External, read-only evidence: declared, never silently absent."""
from __future__ import annotations

import json
import os
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "external_evidence_roots.json"
INVENTORY = REPO / "artifacts" / "external_evidence" / "EXTERNAL_EVIDENCE_INVENTORY.json"
STATES = ("AVAILABLE_LOCALLY", "NOT_MOUNTED", "RECOVERABLE", "MISSING")


def load_config(path: Path | None = None) -> dict:
    return json.loads((path or CONFIG).read_text(encoding="utf-8"))


def _mounts() -> dict:
    try:
        return dict(json.loads(os.environ.get("ARGUS_EXTERNAL_MOUNTS", "") or "{}"))
    except ValueError:
        return {}


def root_path(root: dict, mounts: dict | None = None) -> str:
    return (mounts if mounts is not None else _mounts()).get(root["id"]) or root["path"]


def _inventory() -> dict:
    try:
        return json.loads(INVENTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def status(cfg: dict | None = None, *, exists=os.path.exists, mounts: dict | None = None, inventory: dict | None = None) -> dict:
    cfg = cfg or load_config()
    inv = _inventory() if inventory is None else inventory
    inv_items = inv.get("items", {})
    roots = {r["id"]: r for r in cfg["roots"]}
    root_rows = []
    visible = {}
    for r in cfg["roots"]:
        p = root_path(r, mounts)
        visible[r["id"]] = bool(exists(p))
        root_rows.append({"id": r["id"], "path": p, "class": r["class"], "visible": visible[r["id"]], "note": r.get("note"),
                          "written_by_argus": False})
    rows = []
    for it in cfg["items"]:
        r = roots[it["root"]]
        p = root_path(r, mounts).rstrip("/") + "/" + it["rel"]
        rec = it.get("recovery") or {}
        if exists(p):
            state = "AVAILABLE_LOCALLY"
        elif not visible[it["root"]]:
            state = "NOT_MOUNTED"
        elif rec.get("kind") in ("PUBLIC_BUCKET", "REBUILD"):
            state = "RECOVERABLE"
        else:
            state = "MISSING"
        inv_row = inv_items.get(it.get("inventory_key") or "", {})
        rows.append({
            "id": it["id"], "label": it["label"], "root": it["root"], "path": p, "state": state,
            "preservation_class": it["preservation_class"], "identity": it.get("identity"),
            "recoverable": rec.get("kind") in ("PUBLIC_BUCKET", "REBUILD"), "recovery": rec,
            "inventory": ({"files": inv_row.get("files"), "bytes": inv_row.get("bytes"), "scan_complete": inv_row.get("scan_complete"),
                           "as_of_utc": inv.get("utc")} if inv_row else None),
            "read_only": r["class"] == "EXTERNAL_READ_ONLY",
        })
    return {"schema": "argus-external-evidence-status-v1", "read_only": True, "roots": root_rows, "items": rows,
            "rule": "an item that is not visible from here is NOT_MOUNTED, never absent; ARGUS never writes to an external root"}
