"""Bridge: real, settled `argus.core.review_lab` tasks -> real `argus.core.reading_board` regions."""
from __future__ import annotations

import hashlib
import json

from argus.core import review_lab as RL

CONTRACT_ID = "argus-reviewed-region-bridge-v1"

MIN_INDEPENDENT_HUMANS = RL.MIN_INDEPENDENT_ANSWERS

_SETTLED_STATE_MAP = {
    "CONSENSUS_REACHED": "ACCEPTED",
    "EXPERT_OR_CONTROL_VALIDATED": "EXPERT_VALIDATED",
    "TRAINING_ELIGIBLE": "TRAINING_ELIGIBLE",
    "FROZEN_IN_DATASET_VERSION": "TRAINING_ELIGIBLE",
}

_AFFIRMATIVE_ANSWER = {
    "JUDGE_INK_CANDIDATE": "INK",
}


class RegionBridgeError(ValueError):
    """A task reached a settled, affirmative consensus but cannot prove its own traceability."""


def region_from_task(record: dict) -> dict | None:
    """One `reading_board.assemble()`-shaped region from one real, merged Task record (the exact shape `review_lab.Task.as_record()` / `review_store.merge_answers()` already produces), or `None` if this..."""
    from argus.core import sealed_review_queue as SQ
    unreviewed = SQ.unpromoted(record.get("task_id"))
    if unreviewed:
        raise RegionBridgeError(
            "task %r is a sealed review task that has not been promoted (%s). %s"
            % (record.get("task_id"), ", ".join(next(iter(unreviewed.values()))), SQ.GATE_STATEMENT))

    task_type = record.get("task_type")
    affirmative = _AFFIRMATIVE_ANSWER.get(task_type)
    if affirmative is None:
        return None

    mapped_state = _SETTLED_STATE_MAP.get(record.get("state"))
    if mapped_state is None:
        return None

    tally = record.get("tally") or {}
    stats = human_stats(record)
    if tally.get("top") != affirmative and stats["human_top"] != affirmative:
        return None

    binding = record.get("binding") or {}
    coords = binding.get("physical_coordinates") or {}
    extent = coords.get("extent")
    if not (isinstance(extent, (list, tuple)) and len(extent) == 4):
        raise RegionBridgeError(
            "task %r reached %s with consensus %r but its binding names no real 4-value "
            "extent (physical_coordinates.extent) -- a region that cannot say where it is "
            "cannot be traced back, and traceability is the whole point of the board."
            % (record.get("task_id"), record.get("state"), affirmative))

    answers = record.get("answers") or []
    if stats["independent_humans"] < MIN_INDEPENDENT_HUMANS:
        raise RegionBridgeError(
            "task %r reached %s but only %d independent human reviewer(s) answered it (%d model "
            "proposal(s) ignored). A region goes on the board only on the agreement of at least "
            "%d people; a model's answer is a proposal, not a second reviewer."
            % (record.get("task_id"), record.get("state"), stats["independent_humans"],
               stats["ai_answers"], MIN_INDEPENDENT_HUMANS))
    if stats["human_top"] != affirmative or stats["human_agreement"] < RL.MIN_CONSENSUS_AGREEMENT:
        return None
    return {
        "task_id": record.get("task_id"),
        "state": mapped_state,
        "extent": list(extent),
        "orientation": (binding.get("camera_and_orientation") or {}).get("orientation"),
        "answers": [
            {"reviewer_id": a.get("reviewer_id"), "reviewer_class": a.get("reviewer_class"),
             "value": a.get("value"), "confidence": a.get("confidence"), "utc": a.get("utc"),
             "evidence_role": a.get("evidence_role") or _role_of(a.get("reviewer_class"))}
            for a in answers
        ],
        "reviewer_classes": sorted({a.get("reviewer_class") for a in answers
                                    if a.get("reviewer_class")}),
        "validators": [v for v in (record.get("validations") or []) if v.get("applied")],
        "evidence": {"source_hashes": binding.get("source_hashes"),
                     "displayed_assets": binding.get("displayed_assets"),
                     "producing_model_or_tool": binding.get("producing_model_or_tool")},
        "ct_reference": binding.get("source_volume"),
        "review_lab_task_type": task_type,
        "review_lab_consensus": tally,
    }


def _role_of(reviewer_class) -> str:
    return RL.MODEL_ROLE if reviewer_class == "AI_AGENT" else RL.HUMAN_ROLE


def human_stats(record: dict) -> dict:
    """What PEOPLE said on one merged task record, ignoring every model answer."""
    answers = record.get("answers") or []
    humans = [a for a in answers if a.get("reviewer_class") in RL.HUMAN_CLASSES]
    ids = {RL.norm_id(a.get("reviewer_id") or "") for a in humans}
    counts: dict = {}
    for a in humans:
        counts[a.get("value")] = counts.get(a.get("value"), 0) + 1
    top, n = (max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0))
    return {"independent_humans": len(ids - {""}), "human_answers": len(humans),
            "ai_answers": sum(1 for a in answers if a.get("reviewer_class") == "AI_AGENT"),
            "human_top": top, "human_agreement": (n / len(humans)) if humans else 0.0,
            "human_counts": counts,
            "validated_by": [v for v in (record.get("validations") or []) if v.get("applied")]}


def regions_from_tasks_payload(tasks_payload: dict) -> tuple[list, list]:
    """Every eligible region from one real, merged REVIEW_TASKS.json payload (already merged with its stored answers, e.g."""
    regions, refused = [], []
    for record in tasks_payload.get("tasks", []):
        try:
            region = region_from_task(record)
        except RegionBridgeError as exc:
            refused.append({"task_id": record.get("task_id"), "reason": str(exc)})
            continue
        if region is not None:
            regions.append(region)
    return regions, refused


_VALIDATED_STATES = ("EXPERT_OR_CONTROL_VALIDATED", "TRAINING_ELIGIBLE", "FROZEN_IN_DATASET_VERSION")


def human_review_summary(records: list, *, cells_covered: int, affirmative: dict | None = None) -> dict:
    """The `human_review` a transcription claim rests on, COMPUTED from merged task records."""
    affirmative = affirmative or {}
    per, validators, ai_ignored = [], [], 0
    humans, agreement, validated = [], [], []
    for r in records:
        st = human_stats(r)
        want = affirmative.get(r.get("task_id"))
        share = (st["human_counts"].get(want, 0) / st["human_answers"]
                 if (want is not None and st["human_answers"]) else st["human_agreement"])
        vs = [v for v in st["validated_by"] if v.get("reviewer_class") in RL.EXPERT_CLASSES
              and str(v.get("reviewer_id") or "").strip()]
        ok = r.get("state") in _VALIDATED_STATES and bool(vs)
        humans.append(st["independent_humans"])
        agreement.append(share)
        validated.append(ok)
        ai_ignored += st["ai_answers"]
        for v in vs:
            validators.append({"task_id": r.get("task_id"), "reviewer_id": v["reviewer_id"],
                               "reviewer_class": v["reviewer_class"], "utc": v.get("utc"),
                               "role": "ADJUDICATOR", "evidence_role": RL.HUMAN_ROLE})
        per.append({"task_id": r.get("task_id"), "task_type": r.get("task_type"),
                    "state": r.get("state"), "independent_humans": st["independent_humans"],
                    "human_agreement": round(share, 4), "human_top": st["human_top"],
                    "expert_validated": ok, "ai_answers_ignored": st["ai_answers"]})
    body = {
        "independent_human_answers": min(humans) if humans else 0,
        "expert_validated": bool(validated) and all(validated),
        "agreement": min(agreement) if agreement else 0.0,
        "expert_validators": validators,
        "computed_from": "merged_review_tally",
        "cells_covered": int(cells_covered),
        "records": len(per),
        "ai_answers_ignored": ai_ignored,
        "per_task": per,
        "rule": "the weakest record governs: the fewest independent humans, the lowest agreement, "
                "validated only if every record was validated by an attributed expert. Model "
                "answers are never counted.",
    }
    body["tally_sha256"] = hashlib.sha256(
        json.dumps(per, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return body
