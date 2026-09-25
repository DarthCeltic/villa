"""Science-chain status view: not part of the public ARGUS release.

The operator's build shows the progress of its own private experiment chain on this route. Those
experiments, their runs, their holdouts and their results are not shipped, so every function here
returns a neutral, empty answer in the same shape the service and the UI already read: no roots, no
workers, no scores, nothing in flight. Nothing is inferred and nothing is fabricated.
"""
from __future__ import annotations

import time

UNKNOWN = "progress unknown"

NOT_PUBLIC = "the science-chain status view is not part of the public release"

STAGES: tuple = ()

SCORE_FIELDS: tuple = ()


def worker_progress(log_path=None) -> dict:
    """No worker log is read in the public build."""
    return {"state": UNKNOWN, "why": NOT_PUBLIC}


def live_workers() -> list:
    """No private science worker is looked for in the public build."""
    return []


def arm_roots() -> list:
    return []


def describe_root(root, log_paths: dict | None = None) -> dict:
    return {"root": str(root), "name": getattr(root, "name", str(root)), "arm": None,
            "stage": "EMPTY", "stage_source": NOT_PUBLIC, "superseded": False,
            "superseded_note": None, "artifacts": [], "sealed": False, "precision": None,
            "plan_written_before_labels": None, "frozen_membership": {}, "scores": None,
            "score_note": NOT_PUBLIC, "progress": worker_progress()}


def frag2_state() -> dict:
    """No holdout custody record exists in the public build."""
    return {"state": "NOT_PART_OF_PUBLIC_RELEASE", "staged": False,
            "custody_statement": NOT_PUBLIC, "files": [], "preview": None,
            "why_no_preview": NOT_PUBLIC, "spend_condition": None}


def chain(log_paths: dict | None = None) -> dict:
    """The chain payload with no chain in it."""
    return {
        "schema": "argus-chain-status-v1",
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "state": "NOT_PART_OF_PUBLIC_RELEASE",
        "why": NOT_PUBLIC,
        "arms_expected": [],
        "roots": [],
        "history": [],
        "history_means": NOT_PUBLIC,
        "live_workers": [],
        "single_gpu_owner": True,
        "single_gpu_owner_means": "no science worker is tracked in the public build",
        "arms_live": [],
        "current_arm": None,
        "current_stage": None,
        "precision_decision": None,
        "frag2": frag2_state(),
        "guarantees": [
            "progress is shown only when a worker emitted it; otherwise " + UNKNOWN,
            "no score is shown before its artifact exists",
            "this adapter writes nothing and signals nothing",
        ],
        "links": {"observatory": "/observatory", "workbench": "/workbench",
                  "evidence": "/evidence"},
    }


def resources() -> dict:
    """No headroom floors are declared for a science chain in the public build."""
    return {"floors": {"T_gib": 0.0, "C_gib": 0.0, "ram_gib": 0.0},
            "C_free_gib": None, "T_free_gib": None, "ram_free_gib": None,
            "below_floor": [], "why": NOT_PUBLIC}


def lane1() -> dict:
    return {"state": "NOT_STARTED", "surfaces_completed": [], "candidate_count": None,
            "why": NOT_PUBLIC}


def memory_pressure() -> dict:
    return {"state": UNKNOWN, "why": NOT_PUBLIC}


def feature_cache_upload() -> dict:
    return {"state": "NOT_STARTED", "why": NOT_PUBLIC}


def lofo() -> dict:
    return {"state": "NOT_STARTED", "why": NOT_PUBLIC, "worst_fold": None,
            "fold_sealed": [], "held_out_results": {},
            "progress": {"state": UNKNOWN, "why": NOT_PUBLIC}}


def selftest() -> bool:
    c = chain()
    ok = (c["roots"] == [] and c["live_workers"] == [] and lofo()["state"] == "NOT_STARTED"
          and resources()["below_floor"] == [] and lane1()["candidate_count"] is None)
    print("selftest: %s" % ("1/1 passed" if ok else "0/1 passed"))
    return ok
