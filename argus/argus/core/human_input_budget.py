"""An honest accounting of real human-input time against Villa's Grand Prize budget."""
from __future__ import annotations

import calendar
import json
import time
from pathlib import Path

from argus.core import correction_store, paths, process_contract, review_store
from argus.core import reading_board, translation_plan, upstream_promotion_ladder
from argus.core.review_lab import HUMAN_CLASSES

SCHEMA = "argus-human-input-budget-v1"

BUDGET_HOURS = 8.0
BUDGET_SOURCE = ("Villa need/prize constraint: \"Grand Prize tolerates at most eight "
                 "documented human-input hours.\"")

LAUNCH_AUTH_CONTRACT = "argus-launch-authorization-v1"
BELISARIUS_SCHEMA = "argus-belisarius-final-disposition-v1"
FULL_SCROLL_RUN_PLAN_CONTRACT = "argus-full-scroll-run-plan-v1"

_LAUNCH_AUTH_NAME_PATTERNS = ("AUTH_*.json", "*_LAUNCH.json", "*LAUNCH_AUTH*.json")
_PROMOTION_LADDER_NAME_PATTERNS = ("*ladder*.json", "*LADDER*.json", "*promotion*.json", "*PROMOTION*.json")
_TRANSCRIPTION_NAME_PATTERNS = ("*TRANSCRIPTION*.json", "*transcription*.json",
                                 "*READING_BOARD*.json", "*reading_board*.json", "*board*.json")
_TRANSLATION_NAME_PATTERNS = ("*translation*.json", "*TRANSLATION*.json")
_FULL_SCROLL_PLAN_NAME_PATTERNS = ("*_PLAN.json", "*PLAN*.json")

_AI_CLASS = "AI_AGENT"


def _parse_utc(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_MAX_SCAN_DEPTH = 3


def _bounded_glob(root: Path, filename: str, max_depth: int = _MAX_SCAN_DEPTH) -> list:
    """Every file named exactly `filename`, from `root` itself down through `max_depth - 1` subdirectory levels -- never a recursive `**`."""
    root = Path(root)
    if not root.is_dir():
        return []
    out, prefix = [], ""
    for _ in range(max_depth):
        out.extend(root.glob(prefix + filename))
        prefix += "*/"
    return out


def _candidates(roots: list, patterns: tuple) -> list:
    """Every file whose NAME matches one of `patterns` (each run through `_bounded_glob`), deduplicated and sorted."""
    out = set()
    for root in roots:
        for pattern in patterns:
            out.update(_bounded_glob(Path(root), pattern))
    return sorted(out)



def scan_review(roots: list | None = None) -> dict:
    """Sum `duration_s` across every real, human-reviewer-class Answer recorded anywhere under the given artifact roots."""
    roots = roots if roots is not None else paths.artifact_roots()
    entries, sources = [], []
    for tasks_file in sorted({p for root in roots for p in _bounded_glob(Path(root), "REVIEW_TASKS.json")}):
        target_dir = tasks_file.parent
        sources.append(str(tasks_file))
        answers_by_task = review_store.load_answers(target_dir)
        for task_id, records in answers_by_task.items():
            for rec in records:
                if rec.get("reviewer_class") not in HUMAN_CLASSES:
                    continue
                duration = rec.get("duration_s")
                if not isinstance(duration, (int, float)) or duration < 0:
                    continue
                entries.append({
                    "target_dir": str(target_dir), "task_id": task_id,
                    "reviewer_id": rec.get("reviewer_id"), "reviewer_class": rec.get("reviewer_class"),
                    "duration_s": float(duration), "utc": rec.get("utc"),
                    "source": str(review_store.answers_path(target_dir)),
                })
    return {
        "touchpoint": "review", "method": "self_reported_duration",
        "sources_scanned": sources, "answer_count": len(entries),
        "total_seconds": sum(e["duration_s"] for e in entries),
        "entries": entries,
    }



def scan_geometry_corrections(roots: list | None = None) -> dict:
    """Per proposal, the wall-clock span between the first and last recorded human correction/decision event."""
    roots = roots if roots is not None else paths.artifact_roots()
    entries, sources = [], []
    for corr_file in sorted({p for root in roots for p in _bounded_glob(Path(root), "CORRECTIONS.jsonl")}):
        mesh_dir = corr_file.parent
        sources.append(str(corr_file))
        events = correction_store.load_events(mesh_dir)
        by_proposal: dict = {}
        for e in events:
            if e.get("kind") not in ("correction", "decision"):
                continue
            if e.get("reviewer_class") == _AI_CLASS:
                continue
            ts = _parse_utc(e.get("utc"))
            if ts is None:
                continue
            pid = e.get("proposal_id")
            row = by_proposal.setdefault(pid, {"first": ts, "last": ts, "event_count": 0})
            row["first"] = min(row["first"], ts)
            row["last"] = max(row["last"], ts)
            row["event_count"] += 1
        for pid, row in by_proposal.items():
            entries.append({
                "mesh_dir": str(mesh_dir), "proposal_id": pid,
                "event_count": row["event_count"],
                "elapsed_seconds": row["last"] - row["first"],
                "note": ("wall-clock span across %d recorded human event(s); an upper bound on "
                          "attention between the first and last click, not a measurement of "
                          "active correction work" % row["event_count"]),
            })
    return {
        "touchpoint": "geometry_correction", "method": "wall_clock_span_proxy",
        "sources_scanned": sources, "proposal_count": len(entries),
        "total_seconds": sum(e["elapsed_seconds"] for e in entries),
        "entries": entries,
    }



def _belisarius_default_roots() -> list:
    """`argus.core.belisarius_gate.output_path` is `state_root/\"belisarius\"/RUN_FILENAME`, and the only real caller (`argus.core.action_registry`) passes `argus.core.actions.STATE` (`$ARGUS_HOME/state`)..."""
    roots = list(paths.artifact_roots())
    try:
        from argus.core import actions
        roots.append(actions.STATE)
    except ImportError:
        pass
    return roots


def scan_belisarius(roots: list | None = None) -> dict:
    roots = roots if roots is not None else _belisarius_default_roots()
    entries, sources = [], []
    for f in sorted({p for root in roots for p in _bounded_glob(Path(root), "belriouse_run.json")}):
        doc = _read_json(f)
        if not isinstance(doc, dict) or doc.get("schema") != BELISARIUS_SCHEMA:
            continue
        sources.append(str(f))
        entries.append({
            "file": str(f), "decision": doc.get("decision"),
            "actor": (doc.get("authority") or {}).get("actor"), "utc": doc.get("utc"),
        })
    return {
        "touchpoint": "final_authority", "method": "instant_only",
        "sources_scanned": sources, "decision_count": len(entries),
        "total_seconds": None,
        "reason_no_duration": ("the final-disposition record holds one utc per decision and no paired "
                                "'ready for decision' timestamp exists in it or in the VIGILES "
                                "receipt it wraps; an instant is real evidence, a duration is "
                                "not computable from it"),
        "reason_history_limited": ("the final-disposition record is a current-pointer file, "
                                    "overwritten on each new decision; a superseded "
                                    "decision's timestamp is not "
                                    "recoverable from disk once replaced"),
        "entries": entries,
    }



def scan_launch_authorizations(roots: list | None = None) -> dict:
    roots = roots if roots is not None else paths.artifact_roots()
    entries = []
    for f in _candidates(roots, _LAUNCH_AUTH_NAME_PATTERNS):
        doc = _read_json(f)
        if not isinstance(doc, dict) or doc.get("contract") != LAUNCH_AUTH_CONTRACT:
            continue
        issued = doc.get("issued_utc")
        consumed = doc.get("consumed_utc")
        issued_ts, consumed_ts = _parse_utc(issued), _parse_utc(consumed)
        elapsed = (consumed_ts - issued_ts) if (issued_ts is not None and consumed_ts is not None) else None
        entries.append({
            "file": str(f), "authorization_id": doc.get("authorization_id"),
            "authorised_by": doc.get("authorised_by"),
            "issued_utc": issued, "consumed_utc": consumed,
            "elapsed_seconds": elapsed,
            "note": ("elapsed time from authorisation issuance to consumption; a calendar span, "
                     "not a labor-hours figure -- the human need not have been present for any "
                     "of it" if elapsed is not None else
                     "issued only; no consumed_utc recorded on this receipt, so no span is "
                     "computable"),
        })
    measured = [e for e in entries if e["elapsed_seconds"] is not None]
    return {
        "touchpoint": "operator_execution", "method": "issuance_to_consumption_span",
        "sources_scanned": [e["file"] for e in entries], "authorization_count": len(entries),
        "total_seconds": sum(e["elapsed_seconds"] for e in measured) if measured else None,
        "entries": entries,
    }



def scan_provider_promotion(roots: list | None = None) -> dict:
    roots = roots if roots is not None else paths.artifact_roots()
    entries = []
    for f in _candidates(roots, _PROMOTION_LADDER_NAME_PATTERNS):
        doc = _read_json(f)
        if isinstance(doc, dict) and doc.get("schema") == upstream_promotion_ladder.CONTRACT:
            entries.append({"file": str(f), "finding_id": doc.get("finding_id"),
                              "started_utc": doc.get("started_utc")})
    return {
        "touchpoint": "provider_promotion", "method": "none_persisted",
        "receipt_count": len(entries), "entries": entries,
        "reason_unmeasured": (
            None if entries else
            "argus.core.upstream_promotion_ladder is pure state-machine bookkeeping over a "
            "caller-owned dict with no filesystem write of its own (its own docstring says so "
            "directly); no caller in this repository persists a ladder record -- confirmed by "
            "scanning every artifact root for its schema (%s) and finding none" %
            upstream_promotion_ladder.CONTRACT),
    }


def scan_transcription(roots: list | None = None) -> dict:
    roots = roots if roots is not None else paths.artifact_roots()
    entries = []
    for f in _candidates(roots, _TRANSCRIPTION_NAME_PATTERNS):
        doc = _read_json(f)
        if (isinstance(doc, dict) and doc.get("contract") == reading_board.CONTRACT_ID
                and doc.get("class") == "TRANSCRIPTION"):
            entries.append({"file": str(f), "promoted_by": doc.get("promoted_by")})
    return {
        "touchpoint": "transcription", "method": "none_persisted",
        "receipt_count": len(entries), "entries": entries,
        "reason_unmeasured": (
            None if entries else
            "argus.core.reading_board.claim_transcription is a pure function; the human_review "
            "evidence it validates is caller-supplied and never written to disk by any "
            "production code path (only by its own unit test) -- confirmed by scanning every "
            "artifact root for a promoted TRANSCRIPTION-class board and finding none; no scroll "
            "has ever had a transcription claimed through this system"),
    }


def scan_translation(roots: list | None = None) -> dict:
    roots = roots if roots is not None else paths.artifact_roots()
    entries = []
    for f in _candidates(roots, _TRANSLATION_NAME_PATTERNS):
        doc = _read_json(f)
        if isinstance(doc, dict) and doc.get("schema") == translation_plan.SCHEMA:
            entries.append({"file": str(f), "state": doc.get("state")})
    return {
        "touchpoint": "translation", "method": "none_persisted",
        "receipt_count": len(entries), "entries": entries,
        "reason_unmeasured": (
            None if entries else
            "argus.core.translation_plan.build is stateless -- it returns a worksheet and "
            "records nothing, and no downstream module persists a human-drafted translation -- "
            "confirmed by scanning every artifact root for its schema and finding none; no "
            "scroll has ever had a translation drafted through this system"),
    }



_COMPLETE_RUN_STATUSES = frozenset({"COMPLETE", "RUN_COMPLETE", "FINISHED"})


def full_scroll_run_status(roots: list | None = None) -> dict:
    roots = roots if roots is not None else paths.artifact_roots()
    plans = []
    for f in _candidates(roots, _FULL_SCROLL_PLAN_NAME_PATTERNS):
        doc = _read_json(f)
        if isinstance(doc, dict) and doc.get("contract") == FULL_SCROLL_RUN_PLAN_CONTRACT:
            plans.append({"file": str(f), "status": doc.get("status"),
                           "authorises": doc.get("authorises")})
    complete = [p for p in plans if p["status"] in _COMPLETE_RUN_STATUSES]
    plan_summary = "; ".join("%s (status=%r, authorises=%r)" % (p["file"], p["status"], p["authorises"])
                              for p in plans)
    return {
        "plans_found": plans, "complete_run_exists": bool(complete),
        "verdict": (
            "a completed prospective full-scroll run exists: %s" % plan_summary if complete else
            ("a route plan toward a full scroll was found but has not run: %s" % plan_summary
             if plans else
             "no route plan toward a prospective full-scroll run was found anywhere in the "
             "scanned artifact roots")),
    }



def report(scroll: str | None = None, roots: list | None = None) -> dict:
    """Stage-by-stage honest accounting: MEASURED (a real number and its receipt source) or UNMEASURED (the specific reason), cross-referenced against the live process contract."""
    roots = roots if roots is not None else paths.artifact_roots()
    contract = process_contract.derive(scroll)
    stage_state = {s["id"]: s["state"] for s in contract["stages"]}
    final_authority_state = contract["final_authority"]["state"]

    review = scan_review(roots)
    geometry = scan_geometry_corrections(roots)
    belisarius = scan_belisarius(roots)
    launch = scan_launch_authorizations(roots)
    promotion = scan_provider_promotion(roots)
    transcription = scan_transcription(roots)
    translation = scan_translation(roots)
    full_run = full_scroll_run_status(roots)

    stages = [
        {
            "touchpoint": "review", "process_contract_stage": "review",
            "process_contract_state": stage_state.get("review"),
            "measured": review["answer_count"] > 0,
            "hours": (review["total_seconds"] / 3600.0) if review["answer_count"] else None,
            "seconds": review["total_seconds"] if review["answer_count"] else None,
            "method": review["method"], "sources": review["sources_scanned"],
            "reason_if_unmeasured": (None if review["answer_count"] else
                "no REVIEW_ANSWERS.jsonl entry from a human reviewer class exists anywhere in "
                "the scanned artifact roots; the Review Lab has generated proposed tasks "
                "(REVIEW_TASKS.json) but no real human answer has ever been recorded through "
                "this system"),
        },
        {
            "touchpoint": "geometry_correction", "process_contract_stage": "topology_repair",
            "process_contract_state": stage_state.get("topology_repair"),
            "measured": geometry["proposal_count"] > 0,
            "hours": (geometry["total_seconds"] / 3600.0) if geometry["proposal_count"] else None,
            "seconds": geometry["total_seconds"] if geometry["proposal_count"] else None,
            "method": geometry["method"], "sources": geometry["sources_scanned"],
            "reason_if_unmeasured": (None if geometry["proposal_count"] else
                "no CORRECTIONS.jsonl file exists anywhere in the scanned artifact roots; the "
                "correction store has never recorded a real geometry correction event"),
        },
        {
            "touchpoint": "final_authority", "process_contract_stage": "final_authority",
            "process_contract_state": final_authority_state,
            "measured": False, "hours": None, "seconds": None,
            "method": belisarius["method"], "sources": belisarius["sources_scanned"],
            "reason_if_unmeasured": (
                belisarius["reason_no_duration"] if belisarius["decision_count"] else
                "no final-disposition record exists anywhere in the scanned artifact roots; no final "
                "disposition has ever been recorded through this system, and even if "
                "one had been, only an instant is recordable (see reason_no_duration)"),
            "decisions_on_disk": belisarius["decision_count"],
        },
        {
            "touchpoint": "operator_execution", "process_contract_stage": None,
            "process_contract_state": None,
            "measured": launch["total_seconds"] is not None,
            "hours": (launch["total_seconds"] / 3600.0) if launch["total_seconds"] is not None else None,
            "seconds": launch["total_seconds"],
            "method": launch["method"], "sources": launch["sources_scanned"],
            "reason_if_unmeasured": (None if launch["total_seconds"] is not None else (
                "%d real launch-authorization receipt(s) were found (operator issuance is a "
                "real, dated human act), but none has a paired consumed_utc, so no "
                "issuance-to-consumption span is computable from what exists on disk"
                % launch["authorization_count"] if launch["authorization_count"] else
                "no argus-launch-authorization-v1 receipt exists anywhere in the scanned "
                "artifact roots")),
            "authorizations_on_disk": launch["authorization_count"],
        },
        {
            "touchpoint": "provider_promotion", "process_contract_stage": None,
            "process_contract_state": None,
            "measured": False, "hours": None, "seconds": None,
            "method": promotion["method"], "sources": [e["file"] for e in promotion["entries"]],
            "reason_if_unmeasured": promotion["reason_unmeasured"] or "receipts exist but carry no duration field",
        },
        {
            "touchpoint": "transcription", "process_contract_stage": "transcription",
            "process_contract_state": stage_state.get("transcription"),
            "measured": False, "hours": None, "seconds": None,
            "method": transcription["method"], "sources": [e["file"] for e in transcription["entries"]],
            "reason_if_unmeasured": transcription["reason_unmeasured"] or "receipts exist but carry no duration field",
        },
        {
            "touchpoint": "translation", "process_contract_stage": "translation",
            "process_contract_state": stage_state.get("translation"),
            "measured": False, "hours": None, "seconds": None,
            "method": translation["method"], "sources": [e["file"] for e in translation["entries"]],
            "reason_if_unmeasured": translation["reason_unmeasured"] or "receipts exist but carry no duration field",
        },
    ]

    measured_stages = [s for s in stages if s["measured"]]
    total_hours = sum(s["hours"] for s in measured_stages) if measured_stages else 0.0

    return {
        "schema": SCHEMA,
        "scroll": scroll,
        "budget_hours": BUDGET_HOURS,
        "budget_source": BUDGET_SOURCE,
        "stages": stages,
        "measured_stage_count": len(measured_stages),
        "unmeasured_stage_count": len(stages) - len(measured_stages),
        "total_measured_hours": total_hours,
        "total_measured_hours_note": (
            "sum of whatever isolated receipts exist today across the %d measured touchpoint(s) "
            "listed above; this is NOT a full-scroll figure -- see full_scroll_run" % len(measured_stages)),
        "full_scroll_run": full_run,
        "budget_assessment": _assess(measured_stages, stages, full_run),
    }


def _assess(measured_stages: list, all_stages: list, full_run: dict) -> str:
    if full_run["complete_run_exists"]:
        total = sum(s["hours"] for s in measured_stages) if measured_stages else 0.0
        verdict = "UNDER" if total <= BUDGET_HOURS else "OVER"
        return ("%s budget: %.2f measured human-input hours across a completed prospective "
                 "full-scroll run, against an %.1f hour ceiling." % (verdict, total, BUDGET_HOURS))
    unmeasured = [s["touchpoint"] for s in all_stages if not s["measured"]]
    return (
        "CANNOT BE ASSESSED against the %.1f hour ceiling: no prospective full-scroll run has "
        "completed in this repository's artifact tree (%s), and %d of %d human-gated "
        "touchpoint(s) have never recorded any real human-input time at all (%s). The %.2f "
        "hour(s) this module can currently sum are real, but they are not a full-scroll figure "
        "and must not be reported as one."
        % (BUDGET_HOURS, full_run["verdict"], len(unmeasured), len(all_stages),
           ", ".join(unmeasured),
           sum(s["hours"] for s in measured_stages) if measured_stages else 0.0)
    )
