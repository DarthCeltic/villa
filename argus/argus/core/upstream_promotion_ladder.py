"""The promotion ladder for adopting an upstream (Villa) change into ARGUS, as actual, invokable code -- not a design document."""
from __future__ import annotations

import time
from typing import Any

CONTRACT = "argus-upstream-promotion-ladder-v1"

RUNGS = ("INTAKE", "ADAPTER", "CONTROLS", "PROMOTION", "WORKBENCH", "EVIDENCE")

STAGE_STATUSES = ("PASS", "FAIL", "NOT_YET_ATTEMPTED")

RUNG_DEFINITIONS = {
    "INTAKE": "immutable source revision, weights/data hashes, licence, exposure, hardware "
             "and I/O contract.",
    "ADAPTER": "plan-only request, explicit approval, isolated execution, immutable "
              "artifacts and receipt.",
    "CONTROLS": "synthetic shape/frame failures plus real same-material positive and "
               "negative controls.",
    "PROMOTION": "capability ledger state changes only from a machine-checkable decision "
                "packet.",
    "WORKBENCH": "registered layers with producer, revision, source volume, transform, "
                "units and scientific class.",
    "EVIDENCE": "a result enters review or packaging only through VIGILES; attractive "
               "output is not a pass.",
}

REQUIRED_EVIDENCE_KEYS = {
    "INTAKE": ("source_revision", "hashes", "licence", "exposure", "hardware", "io_contract"),
    "ADAPTER": ("plan", "approval", "execution", "artifacts", "receipt"),
    "CONTROLS": ("synthetic_controls", "positive_control", "negative_control"),
    "PROMOTION": ("decision_packet",),
    "WORKBENCH": ("producer", "revision", "source_volume", "transform", "units",
                 "scientific_class"),
    "EVIDENCE": ("vigiles_review",),
}

_STARTABLE_DECISION = "ADOPT_CANDIDATE"


class LadderRefusal(ValueError):
    """Raised only when the ladder's own order or evidence shape is violated -- never on a legitimate FAIL or NOT_YET_ATTEMPTED, which are honest, permitted states."""


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def start_ladder(finding: dict) -> dict:
    """Begin a promotion ladder record for one upstream-differential finding row."""
    if not isinstance(finding, dict):
        raise LadderRefusal("finding must be a dict (an upstream-differential row)")
    finding_id = finding.get("id")
    if not finding_id:
        raise LadderRefusal("finding has no 'id'")
    decision = ((finding.get("adoption_decision") or {}) or {}).get("value")
    if decision != _STARTABLE_DECISION:
        raise LadderRefusal(
            "only a %s finding may start a promotion ladder; %r's adoption_decision.value is "
            "%r" % (_STARTABLE_DECISION, finding_id, decision))
    return {
        "schema": CONTRACT,
        "finding_id": finding_id,
        "finding_summary": {
            "commit_or_pr": (finding.get("commit_or_pr") or {}).get("value"),
            "adoption_reason": (finding.get("adoption_decision") or {}).get("reason"),
        },
        "started_utc": _utcnow(),
        "current_rung": None,
        "stages": {
            rung: {"status": "NOT_YET_ATTEMPTED", "evidence": None, "recorded_utc": None}
            for rung in RUNGS
        },
    }


def _highest_contiguous_pass(stages: dict) -> str | None:
    """The furthest rung reached WITHOUT a gap -- a PASS at WORKBENCH after CONTROLS was later flipped back to FAIL must not still read as \"reached WORKBENCH\"."""
    highest = None
    for rung in RUNGS:
        if stages[rung]["status"] == "PASS":
            highest = rung
        else:
            break
    return highest


def record_stage(record: dict, rung: str, *, status: str, evidence: Any) -> dict:
    """Record one rung's outcome on `record` (mutated in place, and returned for chaining)."""
    if not isinstance(record, dict) or record.get("schema") != CONTRACT:
        raise LadderRefusal("record must be a dict produced by start_ladder")
    if rung not in RUNGS:
        raise LadderRefusal("unknown rung %r; must be one of %s" % (rung, RUNGS))
    if status not in STAGE_STATUSES:
        raise LadderRefusal("status must be one of %s, got %r" % (STAGE_STATUSES, status))

    if status == "PASS":
        idx = RUNGS.index(rung)
        if idx > 0:
            prev_rung = RUNGS[idx - 1]
            prev_status = record["stages"][prev_rung]["status"]
            if prev_status != "PASS":
                raise LadderRefusal(
                    "cannot record %s=PASS before %s has a recorded PASS on this ladder "
                    "(currently %s) -- the ladder enforces its own order and %s is stage %d "
                    "of %d" % (rung, prev_rung, prev_status, rung, idx + 1, len(RUNGS)))
        required = REQUIRED_EVIDENCE_KEYS[rung]
        if not isinstance(evidence, dict):
            raise LadderRefusal(
                "%s=PASS requires evidence as a dict with keys %s (this rung's own definition: "
                "%r); got %r" % (rung, required, RUNG_DEFINITIONS[rung], type(evidence).__name__))
        missing = [k for k in required if not evidence.get(k)]
        if missing:
            raise LadderRefusal(
                "%s=PASS evidence is missing required field(s) %s (this rung's own definition: "
                "%r)" % (rung, missing, RUNG_DEFINITIONS[rung]))

    record["stages"][rung] = {"status": status, "evidence": evidence, "recorded_utc": _utcnow()}
    record["current_rung"] = _highest_contiguous_pass(record["stages"])
    return record


def record_intake(record: dict, *, status: str, evidence: Any) -> dict:
    return record_stage(record, "INTAKE", status=status, evidence=evidence)


def record_adapter(record: dict, *, status: str, evidence: Any) -> dict:
    return record_stage(record, "ADAPTER", status=status, evidence=evidence)


def record_controls(record: dict, *, status: str, evidence: Any) -> dict:
    return record_stage(record, "CONTROLS", status=status, evidence=evidence)


def record_promotion(record: dict, *, status: str, evidence: Any) -> dict:
    return record_stage(record, "PROMOTION", status=status, evidence=evidence)


def record_workbench(record: dict, *, status: str, evidence: Any) -> dict:
    return record_stage(record, "WORKBENCH", status=status, evidence=evidence)


def record_evidence(record: dict, *, status: str, evidence: Any) -> dict:
    return record_stage(record, "EVIDENCE", status=status, evidence=evidence)


def is_promoted(record: dict) -> bool:
    """True only once every rung through PROMOTION is PASS -- WORKBENCH/EVIDENCE may still be open; \"promoted\" tracks the capability-ledger-changing rung specifically, matching the doc's own PROMOTION..."""
    idx = RUNGS.index("PROMOTION")
    return all(record["stages"][r]["status"] == "PASS" for r in RUNGS[: idx + 1])


def is_fully_closed(record: dict) -> bool:
    """True only once every rung, including EVIDENCE, is PASS."""
    return all(record["stages"][r]["status"] == "PASS" for r in RUNGS)


def summary(record: dict) -> dict:
    """A short, honest status line per rung plus what is blocking further progress -- the same spirit as a promotion receipt's own `rung_before`/`rung_after`/`rung_basis` fields."""
    stages = record["stages"]
    current = record.get("current_rung")
    next_idx = 0 if current is None else RUNGS.index(current) + 1
    next_rung = RUNGS[next_idx] if next_idx < len(RUNGS) else None
    return {
        "finding_id": record.get("finding_id"),
        "current_rung": current,
        "next_rung": next_rung,
        "next_rung_requires": (
            "%s=PASS with evidence keys %s (%s)"
            % (next_rung, REQUIRED_EVIDENCE_KEYS[next_rung], RUNG_DEFINITIONS[next_rung])
            if next_rung else None
        ),
        "promoted": is_promoted(record),
        "fully_closed": is_fully_closed(record),
        "stage_statuses": {r: stages[r]["status"] for r in RUNGS},
    }
