"""Lane status and next actions: not derived in the public ARGUS release.

The operator's build derives lane states from a receipt history that is not part of the public
release. Every state below therefore reports UNAVAILABLE_IN_PUBLIC_BUILD, and no lane, verdict,
score or next action is invented. A rendered candidate is not a reading.
"""
from __future__ import annotations

UNAVAILABLE = "UNAVAILABLE_IN_PUBLIC_BUILD"
_WHY = "the receipt history this state is derived from is not part of the public release"


def _unavailable(item: str, **extra) -> dict:
    return dict({"item": item, "state": UNAVAILABLE, "why": _WHY}, **extra)


def route_classification_state() -> dict:
    return _unavailable("route_classification")


def validation_provenance_state() -> dict:
    return _unavailable("validation_provenance")


def stage1_state() -> dict:
    return _unavailable("stage1")


def detector_state() -> dict:
    return {"item": "detector qualification", "state": "UNRECORDED",
            "may_claim_discovery": False, "may_claim_ink_found": False, "held_until": None,
            "standing_refusals": [],
            "why": "no detector qualification is recorded in this build, so none is claimed"}


def next_science_action() -> dict:
    return _unavailable("next_science_action")


def lanes() -> dict:
    return {"active": [], "blocked": [], "frozen_not_launched": [], "closed": []}


def next_actions() -> list:
    return []


def derive() -> dict:
    return {
        "derived_from_receipts": False,
        "why_this_is_not_prose": _WHY,
        "headline": detector_state(),
        "stage1": stage1_state(),
        "route_classification": route_classification_state(),
        "validation_provenance": validation_provenance_state(),
        "next_science_action": next_science_action(),
        "lanes": lanes(),
        "next_actions": next_actions(),
    }


def semantic_freshness() -> dict:
    return {"semantically_fresh": None, "checks": [], "failed": [], "basis": "unavailable in public build"}
