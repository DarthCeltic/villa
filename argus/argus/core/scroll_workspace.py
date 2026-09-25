"""The compact, selected-scroll product snapshot shared by people and agents."""
from __future__ import annotations

import time

from argus.core import scroll_truth


SCHEMA = "argus-scroll-workspace-v1"


def build(scroll: str, *, inventory: dict | None = None) -> dict:
    truth = scroll_truth.for_scroll(scroll, inventory=inventory)
    route = truth["route"]
    questions = route.get("questions") or {}
    progress = route.get("progress_summary") or {}
    next_action = route.get("next_action") or {}
    steps = route.get("steps") or []
    local = truth.get("local_material") or {}
    human = [row for row in steps if row.get("state") == "HUMAN_GATED"]
    machine = [
        row for row in steps
        if row.get("state") == "AVAILABLE" and row.get("id") != next_action.get("step")
    ]
    acquisition = questions.get("acquisition") or {}
    return {
        "schema": SCHEMA,
        "read_only": True,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requested": scroll,
        "physical_scroll": truth.get("physical_scroll"),
        "status": route.get("status"),
        "identity": {
            "display": route.get("display") or scroll,
            "physical_scroll": truth.get("physical_scroll"),
            "acquisition": acquisition.get("answer"),
            "identity_state": "RESOLVED" if truth.get("physical_scroll") else "REFUSED",
        },
        "progress": progress,
        "evidence": {
            "route_attempts": progress.get("evidence_attempted", 0),
            "receipt_chain": progress.get("evidence_chain_state"),
            "verified_outputs": progress.get("verified_outputs", 0),
            "local_counts": local.get("counts") or {},
        },
        "claim": {
            "ceiling": route.get("claim_ceiling"),
            "accepted_reading": bool(route.get("accepted_reading")),
        },
        "next_action": next_action,
        "blocker": route.get("blocker"),
        "work": {
            "queue": route.get("work_queue") or [],
            "machine_available": machine,
            "human_decisions": human,
        },
        "route": route,
        "local_material": local,
        "source_precedence": truth.get("source_precedence") or [],
        "claim_boundary": truth.get("claim_boundary"),
    }
