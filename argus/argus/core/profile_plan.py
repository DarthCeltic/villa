"""The Library-facing domain-profile handoff."""
from __future__ import annotations

import json
from pathlib import Path

from argus.core import paths

SCHEMA = "argus-profile-plan-v1"
REQUIRED_GROUPS = (
    "acquisition", "voxel_stats", "depth_structure", "fiber_structure",
    "geometry", "render_quality", "profiler_version", "inputs_sha256",
)


def _under_artifact_root(value: str | Path) -> Path:
    candidate = Path(value).resolve()
    roots = [Path(root).resolve() for root in paths.artifact_roots()]
    if not any(candidate == root or root in candidate.parents for root in roots):
        raise ValueError("profile receipt must be under a declared ARGUS artifact root")
    return candidate


def _read_receipt(value: str | Path) -> dict:
    path = _under_artifact_root(value)
    if not path.is_file():
        raise ValueError("profile receipt does not exist: %s" % path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("profile receipt is not valid JSON: %s" % path) from exc
    if not isinstance(doc, dict):
        raise ValueError("profile receipt must contain an object")
    cards = doc.get("cards")
    if isinstance(cards, list):
        return {"_catalog": doc, "_cards": cards}
    return doc


def _select(doc: dict, scroll: str, segment: str | None) -> dict | None:
    cards = doc.get("_cards")
    if cards is not None:
        matches = [c for c in cards if isinstance(c, dict)
                   and c.get("scroll") == scroll
                   and (segment is None or c.get("segment") == segment)]
        return matches[0] if len(matches) == 1 else None
    if doc.get("scroll") not in (None, scroll):
        return None
    if segment is not None and doc.get("segment") not in (None, segment):
        return None
    return doc


def _card(value: dict) -> ScrollDomainCard:
    from argus.core.adaptation import ScrollDomainCard

    missing = [key for key in REQUIRED_GROUPS if key not in value]
    if missing:
        raise ValueError("profile receipt is missing measured fields: %s" % ", ".join(missing))
    return ScrollDomainCard(
        scroll=str(value.get("scroll", "")), segment=str(value.get("segment", "")),
        acquisition=dict(value["acquisition"]), voxel_stats=dict(value["voxel_stats"]),
        depth_structure=dict(value["depth_structure"]),
        fiber_structure=dict(value["fiber_structure"]), geometry=dict(value["geometry"]),
        render_quality=dict(value["render_quality"]),
        profiler_version=str(value["profiler_version"]),
        inputs_sha256=str(value["inputs_sha256"]),
    )


def build(*, scroll: str, segment: str | None = None,
          receipt_path: str | None = None) -> dict:
    """Build the exact next action for one physical scroll."""
    scroll = str(scroll or "").strip()
    if not scroll:
        return {"schema": SCHEMA, "state": "INPUT_REQUIRED", "scroll": scroll,
                "required": ["physical scroll identity"],
                "why": "the profile must be bound to one physical scroll"}
    out = {
        "schema": SCHEMA, "state": "INPUT_REQUIRED", "scroll": scroll,
        "segment": segment, "read_only": True, "receipt": None,
        "required": list(REQUIRED_GROUPS),
        "why": ("run the deterministic profiler on the identity-bound volume and surface; "
                "the Library will not derive a recipe from a filename or partial metadata"),
        "matching": None,
    }
    if not receipt_path:
        return out
    try:
        doc = _read_receipt(receipt_path)
        selected = _select(doc, scroll, segment)
        if selected is None:
            out.update(state="REFUSED", receipt=receipt_path,
                       why="no exact profile receipt matches this physical scroll and segment; "
                           "nothing was substituted")
            return out
        card = _card(selected)
    except ValueError as exc:
        out.update(state="REFUSED", receipt=receipt_path, why=str(exc))
        return out
    out["receipt"] = receipt_path
    out["profile_fingerprint"] = card.fingerprint()
    from argus.core.profiler import RecipeRegistry

    matching = RecipeRegistry().recommend(card, {})
    out["matching"] = matching
    out["state"] = "NO_MATCH" if matching["verdict"] == "NO_MATCH" else "RECOMMEND"
    out["why"] = (matching.get("why") or matching.get("note") or
                   "profile measured; recipe recommendation is bounded by qualification")
    out["next"] = ("open a qualification lane for this domain; no settings were selected"
                   if out["state"] == "NO_MATCH" else
                   "review the recommendation and run the scroll-specific worst-fold gate")
    return out
