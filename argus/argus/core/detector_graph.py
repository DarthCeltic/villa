"""Detector lineage: not part of the public ARGUS release.

The operator's build keeps its detector research lineage here: every architecture, split, result and
correction, and the gate that refuses a second, contradictory lineage. That lineage is private research
history and is not shipped. This module keeps the same interface so the surfaces that read it answer
plainly: there is no detector lineage in this build, and no detector is qualified.
"""
from __future__ import annotations

CONTRACT = "argus-detector-graph-v1"

EXEC_COMPLETE = "COMPLETE"
EXEC_FAILED_GATE = "FAILED_ITS_GATE"
EXEC_FROZEN_NOT_LAUNCHED = "FROZEN_NOT_LAUNCHED"
EXEC_OPEN_PENDING = "OPEN_PENDING_PRECONDITIONS"
EXEC_RERUN_PENDING = "CORRECTED_RERUN_PENDING"
EXEC_REFUSED = "REFUSED"
EXEC_HISTORICAL = "HISTORICAL"
EXEC_ACTIVE = "ACTIVE"
LIVE_STATES = (EXEC_ACTIVE, EXEC_FROZEN_NOT_LAUNCHED, EXEC_OPEN_PENDING, EXEC_RERUN_PENDING)

SCI_TERMINAL = "TERMINAL"
SCI_SUSPENDED = "INTERPRETATION_SUSPENDED"
SCI_NOT_CERTIFIED = "NOT_CERTIFIED"
SCI_UNRESOLVED = "UNRESOLVED"
SCI_INFRASTRUCTURE = "INFRASTRUCTURE"

NOT_SHIPPED = "NOT_IN_PUBLIC_RELEASE"
WHY = ("the detector research lineage is private to the operator's build and is not part of the public "
       "release; this build ships no detector and certifies none")

NODES: tuple = ()
SEPARATE: tuple = ()
NON_JOINS: tuple = ()
DISPOSITION = {"question": None, "verdict": NOT_SHIPPED, "traced_from": [], "why_not_superseded": None,
               "unique_question_it_answers": None, "honest_caveat": WHY, "cost": None, "authorised": False}


class GraphRefusal(RuntimeError):
    """Kept for interface compatibility; the public build never raises it."""


def all_nodes() -> tuple:
    return ()


def resolve(contract_key: str) -> list:
    return []


def integrity() -> dict:
    return {"pass": True, "problems": [], "nodes": 0, "live_contracts": [], "why": WHY}


def require_integrity() -> dict:
    return integrity()


def head() -> dict:
    return {
      "current_detector_head": NOT_SHIPPED,
      "execution_state": NOT_SHIPPED,
      "scientific_state": SCI_NOT_CERTIFIED,
      "last_terminal_lineage_node": None,
      "last_terminal_state": NOT_SHIPPED,
      "pending_reruns": [],
      "refused_lanes": [],
      "open_pending": [],
      "training_authorised": False,
      "successor_launch_authorised": False,
      "successor_prerequisites": None,
      "qualification_status": "NO_QUALIFIED_DETECTOR",
      "next_machine_action": "none: " + WHY,
    }


def as_record() -> dict:
    return {"contract": CONTRACT, "lineage": [], "separate": [], "non_joins": [],
            "disposition": DISPOSITION, "integrity": integrity(), "head": head()}


def selftest() -> bool:
    h = head()
    return (integrity()["pass"] and h["current_detector_head"] == NOT_SHIPPED
            and not h["training_authorised"] and not NODES)


def main(argv=None) -> int:
    import json
    print(json.dumps(as_record(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
