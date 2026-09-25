"""Per-scroll status, kept as independent facts -- never collapsed into one badge."""
from __future__ import annotations

import json
from pathlib import Path

from argus.core import paths

SCHEMA = "argus-surface-status-v1"

TIER_RANK = {
    "science_handoff": 0,
    "science_handoff_pending": 1,
    "integration_receipt": 2,
    "historical_receipt": 3,
}


def _status_dir() -> Path:
    return paths.artifact_write_root() / "surface_status"


def _status_path(scroll: str) -> Path:
    from argus.core.safe_names import safe_name
    return _status_dir() / ("%s.json" % safe_name(scroll, "scroll id"))


def load(scroll: str) -> dict | None:
    """The raw recorded receipt for one scroll, or None if nothing has ever been recorded."""
    p = _status_path(scroll)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def known_scrolls() -> list:
    d = _status_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def resolved(scroll: str) -> dict | None:
    """One winning value per field, chosen by tier (never by raw recency across tiers), and by the LATEST timestamp within that tier (recency only breaks ties inside one tier -- see the module docstring)..."""
    rec = load(scroll)
    if rec is None:
        return None
    facts = rec.get("facts", [])
    by_field: dict = {}
    for f in facts:
        field = f["field"]
        by_field.setdefault(field, []).append(f)
    out_fields = {}
    for field, versions in by_field.items():
        best_tier_rank = min(TIER_RANK.get(f.get("tier"), 99) for f in versions)
        contenders = [f for f in versions if TIER_RANK.get(f.get("tier"), 99) == best_tier_rank]
        winner = max(contenders, key=lambda f: f.get("timestamp") or "")
        superseded = [f for f in versions if f is not winner]
        out_fields[field] = {**winner, "superseded": superseded}
    return {
        "scroll": rec.get("scroll", scroll),
        "as_of": rec.get("as_of"),
        "fields": out_fields,
    }


def all_resolved() -> list:
    return [resolved(s) for s in known_scrolls()]
