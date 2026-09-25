"""The interpretation tail, reachable and honest: candidate review -> reading board -> letters -> transcription claim -> translation proposals -> a packet that can be reopened and verified."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from argus.core import actions as A
from argus.core import board_promotion_store as BPS
from argus.core import paths
from argus.core import publication_packet as PP
from argus.core import reading_board as RB
from argus.core import review_lab as RL
from argus.core import review_store as RS
from argus.core import reviewed_region_bridge as BR
from argus.core import sealed_review_queue as SQ
from argus.core import text_assembly as TA
from argus.core import translation_plan as TP
from argus.core import translation_store as TS

SCHEMA = "argus-interpretation-state-v1"
LANGUAGE_FILENAME = "LANGUAGE.json"

_SETTLED = ("CONSENSUS_REACHED", "EXPERT_OR_CONTROL_VALIDATED", "TRAINING_ELIGIBLE",
            "FROZEN_IN_DATASET_VERSION")

HTR_SLOT = {
    "state": "PROVIDER_NOT_INSTALLED",
    "why": "no OCR/HTR model is installed or wired into ARGUS, so nothing here can read a glyph. "
           "This slot only RECORDS what an external source proposed.",
    "accepts": "a proposal from an external HTR/OCR source, with its source id and version",
    "recorded_as": "an AI_AGENT-class answer, evidence role PREDICTION_DISCOVERY_EVIDENCE",
    "never": ["counted as a person agreeing", "a second reviewer", "able to advance a claim by "
              "itself", "presented as a reading"],
    "action": "htr.propose",
}



def _settled(rec) -> bool:
    return bool(rec) and rec.get("state") in _SETTLED


def _glyph_view(rec: dict | None) -> dict | None:
    if rec is None:
        return None
    st = BR.human_stats(rec)
    settled = (_settled(rec) and st["independent_humans"] >= BR.MIN_INDEPENDENT_HUMANS
               and st["human_agreement"] >= RL.MIN_CONSENSUS_AGREEMENT)
    top = st["human_top"]
    accepted_by = sorted({a["reviewer_id"] for a in rec.get("answers") or []
                          if a.get("reviewer_class") in RL.HUMAN_CLASSES and a.get("value") == top}
                         ) if settled else []
    validated = rec.get("state") in _SETTLED[1:] and bool(st["validated_by"])
    return {"task_id": rec["task_id"], "state": rec["state"], "settled": settled,
            "human_top": top, "independent_humans": st["independent_humans"],
            "human_agreement": round(st["human_agreement"], 4),
            "char": top if settled and top not in RL.GLYPH_NON_LETTERS else None,
            "accepted_by": accepted_by, "expert_validated": validated,
            "ai_answers": st["ai_answers"], "alphabet": (rec.get("binding") or {}).get("alphabet")}


def _context(target_dir: Path) -> dict:
    payload = RS.load_payload(target_dir)
    ctx = {"payload": payload, "regions": [], "refused": [], "board": None, "why": None,
           "ink": {}, "glyph": {}}
    if payload is None:
        ctx["state"] = "NO_TASKS"
        ctx["why"] = ("this target has no review tasks yet. Open candidates first (candidate.open) "
                      "so a person has something to judge; an empty board is not an answered one.")
        return ctx
    for rec in payload.get("tasks", []):
        if rec.get("task_type") == "ANNOTATE_GLYPH":
            ctx["glyph"][rec["task_id"]] = rec
        else:
            ctx["ink"][rec["task_id"]] = rec
    regions, refused = BR.regions_from_tasks_payload(payload)
    ctx["regions"], ctx["refused"] = regions, refused
    if not regions:
        ctx["state"] = "NO_BOARD"
        ctx["why"] = ("no region has been accepted as ink by at least %d independent people. "
                      "Ranked candidates are not on the board, and neither is anything a model "
                      "liked." % BR.MIN_INDEPENDENT_HUMANS)
        return ctx
    orientations = {r["orientation"] for r in regions}
    if len(orientations) != 1 or None in orientations:
        ctx["state"] = "NO_BOARD"
        ctx["why"] = ("accepted regions do not agree on one declared orientation (%s); regions "
                      "from different depth orders are never laid out as one board"
                      % sorted(str(o) for o in orientations))
        return ctx
    try:
        board = RB.assemble(regions, orientation=orientations.pop())
    except RB.BoardRefusal as exc:
        ctx["state"] = "NO_BOARD"
        ctx["why"] = str(exc)
        return ctx
    for cell in board["cells"]:
        tid = (cell.get("review") or {}).get("task_id")
        cell["glyph"] = _glyph_view(ctx["glyph"].get(RS.glyph_task_id(tid)))
    ctx["board"], ctx["state"] = board, "BOARD"
    return ctx


def _claim_check(ctx: dict) -> dict:
    """Would the claim gate pass RIGHT NOW, and if not, exactly why not."""
    board = ctx.get("board")
    if board is None:
        return {"ready": False, "blockers": [ctx.get("why") or "there is no board"],
                "human_review": None}
    blockers, records, aff, covered = [], [], {}, 0
    for cell in board["cells"]:
        tid = cell["review"]["task_id"]
        ink = ctx["ink"].get(tid)
        g = ctx["glyph"].get(RS.glyph_task_id(tid))
        if not (cell.get("glyph") or {}).get("settled") or g is None:
            blockers.append("cell %s (task %s) has no letter judgment settled by two independent "
                            "people" % (cell["cell_id"], tid))
            continue
        covered += 1
        records.extend([ink, g])
        aff[tid] = "INK"
    hr = BR.human_review_summary(records, cells_covered=covered, affirmative=aff)
    if not blockers:
        try:
            RB.claim_transcription(board, human_review=hr, derived_from_review=True)
        except RB.BoardRefusal as exc:
            blockers.append(str(exc))
    return {"ready": not blockers, "blockers": blockers, "human_review": hr}


def _light_claim(rec: dict | None) -> dict | None:
    if not rec:
        return None
    return {"class": rec.get("class"), "claimed_by": rec.get("claimed_by"),
            "claimed_by_class": rec.get("claimed_by_class"), "utc": rec.get("utc"),
            "board_sha256": rec.get("board_sha256"), "human_review": rec.get("human_review"),
            "evidence_role": rec.get("evidence_role"), "not": rec.get("not")}


def _reading(ctx: dict) -> dict | None:
    board = ctx.get("board")
    if board is None:
        return None
    cells = []
    for c in board["cells"]:
        g = c.get("glyph") or {}
        cells.append({"cell_id": c["cell_id"], "glyph": (
            {"char": g.get("char"), "task_id": g.get("task_id"), "accepted_by": g.get("accepted_by")}
            if g.get("settled") and g.get("char") else None)})
    return TA.from_glyph_annotations(cells, region_id="board-" + BPS.board_sha256(board)[:16])


def read_language(target_dir: Path) -> dict | None:
    p = Path(target_dir) / LANGUAGE_FILENAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def board_state(target_dir: Path, ctx: dict | None = None) -> dict:
    """The board, the claim on it and the proposed reading -- the shape `publication_packet.build` takes, and the core of what the UI shows."""
    ctx = ctx or _context(target_dir)
    board = ctx.get("board")
    sha = BPS.board_sha256(board) if board else None
    claim = BPS.active_claim(target_dir, sha)
    check = _claim_check(ctx)
    return {"board_state": ctx["state"], "board": board, "why": ctx.get("why"),
            "board_sha256": sha, "refused_regions": ctx.get("refused") or [],
            "claim": {"state": claim["state"], "claim": _light_claim(claim.get("claim")),
                      "why": claim.get("why"), "ready": check["ready"],
                      "blockers": check["blockers"]},
            "human_review_preview": check["human_review"], "proposed_reading": _reading(ctx)}


def _accepted_transcription(target_dir: Path, ctx: dict) -> tuple:
    """(claim board or None, language, why-not)."""
    board = ctx.get("board")
    claim = BPS.active_claim(target_dir, BPS.board_sha256(board) if board else None)
    if claim["state"] != "ACTIVE":
        return None, claim["state"], claim.get("why") or "no transcription claim exists for this board"
    return claim["claim"]["board"], "ACTIVE", None


def translation_state(target_dir: Path, *, language: str | None = None,
                      ctx: dict | None = None) -> dict:
    ctx = ctx or _context(target_dir)
    declared = read_language(target_dir)
    lang = language or (declared or {}).get("language")
    claim_board, claim_state, why = _accepted_transcription(target_dir, ctx)
    transcription = TP.from_reading_board(claim_board) if claim_board else (
        TP.from_reading_board(ctx.get("board")) if ctx.get("board") else None)
    plan = TP.build(transcription=transcription, language=lang)
    if claim_board is None and plan.get("state") == "REFUSED":
        plan["why"] = "%s; %s" % (plan.get("why"), why or "the board is not a claimed transcription")
    return {"schema": "argus-translation-state-v1", "target": None, "read_only": True,
            "plan": plan, "language": declared, "claim_state": claim_state,
            "candidates": TS.load_candidates(target_dir), "label": TS.LABEL,
            "no_machine_translation": "there is no machine translation of Ancient Greek or Latin "
                                      "in this system; every candidate is a person's proposal",
            "what_a_candidate_is": TS.NOT_A_READING}


def reviewers(ctx: dict) -> list:
    rows: dict = {}
    for rec in list(ctx.get("ink", {}).values()) + list(ctx.get("glyph", {}).values()):
        for a in rec.get("answers") or []:
            key = (a.get("reviewer_id"), a.get("reviewer_class"))
            row = rows.setdefault(key, {
                "reviewer_id": key[0], "reviewer_class": key[1],
                "evidence_role": a.get("evidence_role"),
                "identity_attestation": a.get("identity_attestation"), "answers": 0,
                "source": a.get("source")})
            row["answers"] += 1
    return sorted(rows.values(), key=lambda r: str(r["reviewer_id"]))


def interpretation_state(target: str, target_dir: Path) -> dict:
    ctx = _context(target_dir)
    bs = board_state(target_dir, ctx)
    ts = translation_state(target_dir, ctx=ctx)
    ts["target"] = target
    tasks = ctx.get("payload", {}) or {}
    return {
        "schema": SCHEMA, "target": target, "read_only": True,
        "review_tasks": {"present": ctx.get("payload") is not None,
                         "ink": len(ctx["ink"]), "glyph": len(ctx["glyph"]),
                         "total": len(tasks.get("tasks", []))},
        **bs,
        "regions_accepted": len(ctx["regions"]),
        "reviewers": reviewers(ctx),
        "language": read_language(target_dir),
        "translation": ts,
        "htr_slot": dict(HTR_SLOT),
        "reviewer_rules": {
            "identity": "a reviewer supplies their own name and class. It is self-declared, not "
                        "authenticated; independence is attested by distinct names and a distinct "
                        "browser session.",
            "one_person_twice": "a second answer from the same name, or from the same session "
                                "under another name, is refused",
            "expert_validation": "an attributed OPERATOR or SPECIALIST who did not answer the task",
            "min_independent_humans": BR.MIN_INDEPENDENT_HUMANS,
            "min_agreement": RL.MIN_CONSENSUS_AGREEMENT,
            "model_answers": "recorded as PREDICTION_DISCOVERY_EVIDENCE and never counted",
        },
        "claim_limits": PP.claim_limits(),
        "limitations": PP.limitations_block()["limitations"],
    }



def _hash(action: str, body: dict) -> str:
    b = {k: v for k, v in body.items() if k != "plan_sha256"}
    return hashlib.sha256(json.dumps({"action": action, "body": b}, sort_keys=True,
                                     default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _refusal_code(exc) -> str:
    from argus.core.paths import PathContractViolation
    if isinstance(exc, RS.ReviewStoreError):
        return "NO_SUCH_TASK"
    if isinstance(exc, RL.ReviewRefusal):
        return "REVIEW_REFUSED"
    if isinstance(exc, (RB.BoardRefusal, BPS.BoardClaimError)):
        return "CLAIM_REFUSED"
    if isinstance(exc, TS.TranslationStoreError):
        return "TRANSLATION_REFUSED"
    if isinstance(exc, PP.PacketError):
        return "PACKET_REFUSED"
    if isinstance(exc, PathContractViolation):
        return "USER_DATA_ROOT"
    if type(exc).__name__ == "CandidateRefusal":
        return "CANDIDATE_REFUSED"
    return "REFUSED"


_CAUGHT = (A.Refused, RS.ReviewStoreError, RL.ReviewRefusal, RB.BoardRefusal, BPS.BoardClaimError,
           TS.TranslationStoreError, PP.PacketError)


def _meta(changes, may_refuse, *, reversible=False, read_only=False):
    return {"changes": list(changes), "cost": {"bytes": "< 1 MB", "gpu": "none", "seconds": "< 2"},
            "leases": [], "may_refuse": list(may_refuse), "reversible": reversible,
            "read_only": read_only}


def _make_planner(action, meta, impl):
    def plan(p: dict) -> dict:
        out = dict(meta)
        out["changes"] = list(meta["changes"])
        try:
            SQ.assert_promoted(p or {})
            out.update(impl(p or {}))
        except SQ.UnreviewedTaskRefused as exc:
            out.update(would_be_refused=True, refusal={"code": "TASK_NOT_PROMOTED", "why": str(exc)})
        except _CAUGHT + (paths.PathContractViolation,) as exc:
            code, why = (exc.code, exc.why) if isinstance(exc, A.Refused) else (
                _refusal_code(exc), str(exc))
            out.update(would_be_refused=True, refusal={"code": code, "why": why})
        except Exception as exc:
            if type(exc).__name__ != "CandidateRefusal":
                raise
            out.update(would_be_refused=True,
                       refusal={"code": "CANDIDATE_REFUSED", "why": str(exc)})
        if out.get("refusal"):
            out["would_be_refused"] = True
        out["plan_sha256"] = _hash(action, out)
        return out
    return plan


def _make_doer(action, planner, executor, *, human=False, needs_approval=True):
    def do(spec: A.ActionSpec) -> dict:
        if human and not str(spec.actor).startswith("human:"):
            raise A.Refused(
                "HUMAN_ACTOR_REQUIRED",
                "%s records a person's judgment and can only be requested by a human actor; %r is "
                "not one. A script cannot become a reviewer." % (action, spec.actor))
        plan = planner(spec.params)
        if plan.get("refusal"):
            raise A.Refused(plan["refusal"]["code"], plan["refusal"]["why"])
        if needs_approval and spec.params.get("approved_plan_sha256") != plan["plan_sha256"]:
            raise A.Refused(
                "PLAN_NOT_APPROVED",
                "approved_plan_sha256 must equal the plan hash returned by /plan for %s" % action,
                expected=plan["plan_sha256"])
        try:
            return executor(spec, plan)
        except _CAUGHT + (paths.PathContractViolation,) as exc:
            if isinstance(exc, A.Refused):
                raise
            raise A.Refused(_refusal_code(exc), str(exc)) from exc
    return do


def _need(p: dict, key: str, kind=str):
    v = p.get(key)
    if v is None or v == "" or not isinstance(v, kind) or isinstance(v, bool) and kind is not bool:
        raise A.Refused("MISSING_PARAMETER", "params.%s is required (%s)" % (key, kind.__name__))
    return v


def _target(p: dict) -> tuple:
    target = _need(p, "target")
    d = paths.resolve_ui_target_dir(target)
    if d is None:
        raise A.Refused("UNKNOWN_TARGET", "%r is not an exported target in TARGETS.json" % target)
    return target, Path(d)


def _human(p: dict, name_key: str, class_key: str) -> tuple:
    name = RS.clean_reviewer_name(p.get(name_key))
    cls = p.get(class_key)
    if cls not in RL.HUMAN_CLASSES:
        raise A.Refused("HUMAN_CLASS_REQUIRED", "%s must be one of %s"
                        % (class_key, ", ".join(sorted(RL.HUMAN_CLASSES))))
    return name, cls



def _rank_for(p: dict):
    from argus.core import candidates as C
    from argus.core import orientation_semantics as OS
    from argus.core import sealed_blocks as SB
    region = p.get("region")
    if not (isinstance(region, (list, tuple)) and len(region) == 4):
        raise A.Refused("MISSING_PARAMETER", "params.region is (y0, y1, x0, x1)")
    segment = _need(p, "segment")
    try:
        run = SB.default_run()
        return C.rank(segment=segment, region=tuple(int(v) for v in region),
                      orientations=OS.REQUIRED_DEPTH_ORIENTATIONS, run=run,
                      limit=int(p.get("limit") or 20) * 2, model=p.get("model"))
    except (SB.SealRecordRefusal, FileNotFoundError, OSError, KeyError) as exc:
        raise A.Refused("DATA_UNAVAILABLE",
                        "the sealed probability planes this ranks are not available here (%s). "
                        "Nothing is invented to fill the gap." % str(exc)[:200]) from exc


def _impl_candidate_open(p: dict) -> dict:
    from argus.core import candidates as C
    target, d = _target(p)
    ranked = _rank_for(p)
    limit = int(p.get("limit") or 20)
    plan = C.plan_review_tasks(ranked, d, limit=limit)
    return {"target": target, "segment": p.get("segment"), "region": list(p.get("region")),
            "limit": limit,
            "changes": ["appends %d review task(s) to REVIEW_TASKS.json for %s; existing tasks "
                        "and every recorded answer are kept" % (plan["tasks_to_add"], target)],
            "review_tasks": plan}


def _do_candidate_open(spec, plan):
    from argus.core import candidates as C
    target, d = _target(spec.params)
    ranked = _rank_for(spec.params)
    path = C.export_review_tasks(ranked, d, limit=plan["limit"], merge_existing=True)
    return {"status": "OK", "written": str(path), "tasks_added": plan["review_tasks"]["tasks_to_add"],
            "note": "candidates are places to look, not findings; review them in Review"}



def _impl_glyph_annotate(p: dict) -> dict:
    target, d = _target(p)
    task_id = _need(p, "task_id")
    answer = _need(p, "answer")
    alphabet = _need(p, "alphabet")
    name, cls = _human(p, "reviewer_id", "reviewer_class")
    ctx = _context(d)
    ink = ctx["ink"].get(task_id)
    cell = next((c for c in (ctx.get("board") or {}).get("cells", [])
                 if c["review"]["task_id"] == task_id), None)
    if ink is None or cell is None:
        raise A.Refused("NOT_AN_ACCEPTED_REGION",
                        "%s is not a region on the reading board. Only a region two independent "
                        "people accepted as ink can be given a letter." % task_id)
    existing = ctx["glyph"].get(RS.glyph_task_id(task_id))
    task = RS._task_from_record(existing) if existing else RS.build_glyph_task(ink, alphabet)
    if existing and (existing["binding"].get("alphabet") != alphabet):
        raise A.Refused("ALPHABET_MISMATCH", "this region's glyph task uses the %s alphabet"
                        % existing["binding"].get("alphabet"))
    ans = RL.Answer(reviewer_id=name, reviewer_class=cls, value=answer,
                    confidence=float(p.get("confidence", 0.6)),
                    duration_s=float(p.get("duration_s", 0.0)), utc="", prompt_version="p",
                    session_binding=p.get("reviewer_session") or None)
    task.record(ans)
    return {"target": target, "task_id": task_id, "glyph_task_id": task.task_id,
            "answer": answer, "alphabet": alphabet, "reviewer": name, "reviewer_class": cls,
            "evidence_role": RL.HUMAN_ROLE, "identity_attestation": "SELF_DECLARED",
            "changes": ["records %s's letter judgment for region %s (HUMAN_JUDGMENT, attributed "
                        "to them)" % (name, task_id),
                        "creates the glyph task for this region on first use"]}


def _do_glyph_annotate(spec, plan):
    p = spec.params
    target, d = _target(p)
    raw = RS.load_raw_payload(d)
    ink = _context(d)["ink"][p["task_id"]]
    RS.ensure_glyph_task(d, ink, p["alphabet"])
    raw = RS.load_raw_payload(d)
    name, cls = _human(p, "reviewer_id", "reviewer_class")
    rec = RS.record_answer(target_dir=d, tasks_payload=raw, task_id=plan["glyph_task_id"],
                           value=p["answer"], confidence=float(p.get("confidence", 0.6)),
                           duration_s=float(p.get("duration_s", 0.0)), notes=p.get("notes"),
                           reviewer_id=name, reviewer_class=cls,
                           session_binding=p.get("reviewer_session") or None)
    return {"status": "OK", "task": rec["task_id"], "state": rec["state"], "tally": rec["tally"]}



def _impl_htr_propose(p: dict) -> dict:
    if p.get("run_provider"):
        raise A.Refused(
            "PROVIDER_NOT_INSTALLED",
            "no OCR/HTR provider is installed, so %r cannot be run. This slot records proposals "
            "from an external source; it does not read anything. %s"
            % (p.get("run_provider"), HTR_SLOT["why"]))
    target, d = _target(p)
    task_id = _need(p, "task_id")
    answer = _need(p, "answer")
    sid, ver = _need(p, "source_id"), _need(p, "source_version")
    raw = RS.load_raw_payload(d)
    rec = next((r for r in (raw or {}).get("tasks", []) if r["task_id"] == task_id), None)
    if rec is None:
        raise A.Refused("NO_SUCH_TASK", "no task %r for this target" % task_id)
    if answer not in rec["permitted_answers"]:
        raise A.Refused("REVIEW_REFUSED", "%r is not a permitted answer for %s"
                        % (answer, rec["task_type"]))
    return {"target": target, "task_id": task_id, "answer": answer, "source_id": sid,
            "source_version": ver, "evidence_role": RL.MODEL_ROLE, "reviewer_class": "AI_AGENT",
            "counts_as_human": False,
            "changes": ["records an AI_AGENT proposal from %s@%s on %s. It is stamped "
                        "PREDICTION_DISCOVERY_EVIDENCE, is never a vote, and cannot advance any "
                        "claim" % (sid, ver, task_id)]}


def _do_htr_propose(spec, plan):
    p = spec.params
    target, d = _target(p)
    rec = RS.record_ai_proposal(
        target_dir=d, tasks_payload=RS.load_raw_payload(d), task_id=p["task_id"],
        value=p["answer"], source_id=p["source_id"], source_version=p["source_version"],
        confidence=float(p.get("confidence", 0.0)), notes=p.get("notes"))
    return {"status": "OK", "task": rec["task_id"], "state": rec["state"],
            "counts_as_human": False, "evidence_role": RL.MODEL_ROLE, "tally": rec["tally"]}



def _impl_transcription_claim(p: dict) -> dict:
    target, d = _target(p)
    name, cls = _human(p, "claimed_by", "claimed_by_class")
    ctx = _context(d)
    check = _claim_check(ctx)
    board = ctx.get("board")
    out = {"target": target, "claimed_by": name, "claimed_by_class": cls,
           "board_sha256": BPS.board_sha256(board) if board else None,
           "ready": check["ready"], "blockers": check["blockers"],
           "human_review": check["human_review"],
           "human_review_is": "computed from the merged review tally; never typed",
           "changes": ["appends one attributed transcription claim to BOARD_CLAIMS.jsonl. It says "
                       "people agreed on what these accepted regions show; it says nothing about "
                       "an unread scroll, a detector or a prize"]}
    if not check["ready"]:
        out["refusal"] = {"code": "CLAIM_REFUSED", "why": "; ".join(check["blockers"])}
    return out


def _do_transcription_claim(spec, plan):
    p = spec.params
    target, d = _target(p)
    ctx = _context(d)
    check = _claim_check(ctx)
    board = ctx["board"]
    rec = BPS.record_review_claim(
        d, board=board, human_review=check["human_review"], claimed_by=plan["claimed_by"],
        claimed_by_class=plan["claimed_by_class"], why=str(p.get("why") or ""),
        transcription=_reading(ctx))
    return {"status": "OK", "class": rec["class"], "board_sha256": rec["board_sha256"],
            "claimed_by": rec["claimed_by"], "human_review": rec["human_review"]}



_LANG = re.compile(r"^[^\x00-\x1f]{1,64}$")


def _impl_language_write(p: dict) -> dict:
    target, d = _target(p)
    lang = " ".join(str(_need(p, "language")).split())
    if not _LANG.match(lang):
        raise A.Refused("BAD_LANGUAGE", "a language is a short name, e.g. 'Ancient Greek'")
    name = RS.clean_reviewer_name(p.get("declared_by"))
    prev = read_language(d)
    if prev and prev.get("language") == lang:
        raise A.Refused("NO_CHANGE", "%s is already declared as %r" % (target, lang))
    return {"target": target, "language": lang, "declared_by": name,
            "previous": (prev or {}).get("language"),
            "changes": ["writes LANGUAGE.json (%r, declared by %s). A declaration is a person's "
                        "statement, not a detection" % (lang, name)]}


def _do_language_write(spec, plan):
    p = spec.params
    target, d = _target(p)
    prev = read_language(d)
    rec = {"schema": "argus-language-declaration-v1", "language": plan["language"],
           "declared_by": plan["declared_by"], "basis": str(p.get("basis") or ""),
           "evidence_role": RL.HUMAN_ROLE, "previous": prev,
           "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = d / (LANGUAGE_FILENAME + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, d / LANGUAGE_FILENAME)
    return {"status": "OK", "language": rec["language"], "declared_by": rec["declared_by"]}



def _translation_inputs(p: dict, d: Path) -> tuple:
    ctx = _context(d)
    claim_board, cstate, why = _accepted_transcription(d, ctx)
    if claim_board is None:
        raise A.Refused("NO_TRANSCRIPTION_CLAIM",
                        "translation only follows an ACTIVE transcription claim on the current "
                        "board (%s: %s)" % (cstate, why))
    lang = (read_language(d) or {}).get("language")
    plan = TP.build(transcription=TP.from_reading_board(claim_board), language=lang)
    if plan.get("state") != "READY_FOR_REVIEW":
        raise A.Refused("TRANSLATION_NOT_READY", "%s: %s" % (plan.get("state"), plan.get("why")))
    return plan, BPS.board_sha256(ctx["board"])


def _impl_translation_propose(p: dict) -> dict:
    target, d = _target(p)
    name, cls = _human(p, "proposed_by", "proposed_by_class")
    plan, sha = _translation_inputs(p, d)
    cand = TP.propose_candidate(plan, source_token_ids=list(p.get("source_token_ids") or []),
                                text=str(p.get("text") or ""),
                                alternatives=list(p.get("alternatives") or []))
    if cand.get("state") != "PROPOSED":
        raise A.Refused("TRANSLATION_REFUSED", cand.get("why") or "refused")
    return {"target": target, "label": TS.LABEL, "proposed_by": name, "proposed_by_class": cls,
            "language": plan.get("language"), "text": cand["text"],
            "source_token_ids": cand["source_token_ids"], "board_sha256": sha,
            "alternatives": cand["alternatives"], "evidence_role": RL.HUMAN_ROLE,
            "changes": ["appends one translation PROPOSAL to TRANSLATION_CANDIDATES.jsonl, citing "
                        "%d accepted token(s). It is a proposal by %s, never a reading of an "
                        "unread scroll" % (len(cand["source_token_ids"]), name)]}


def _do_translation_propose(spec, plan):
    p = spec.params
    target, d = _target(p)
    name, cls = _human(p, "proposed_by", "proposed_by_class")
    tplan, sha = _translation_inputs(p, d)
    rec = TS.record_candidate(d, plan=tplan, source_token_ids=list(p.get("source_token_ids") or []),
                              text=str(p.get("text") or ""),
                              alternatives=list(p.get("alternatives") or []),
                              proposed_by=name, proposed_by_class=cls, board_sha256=sha,
                              supersedes=p.get("supersedes") or None)
    return {"status": "OK", "id": rec["id"], "label": rec["label"],
            "is_a_reading_of_an_unread_scroll": False}



def _packet_inputs(p: dict, d: Path, target: str) -> dict:
    ctx = _context(d)
    bs = board_state(d, ctx)
    roots = paths.serve_roots()
    receipts = []
    for r in p.get("receipts") or []:
        resolved = paths.resolve_served_path(str(r), roots)
        if not any(paths.within_root(resolved, root) for root in roots):
            raise A.Refused("RECEIPT_OUTSIDE_ROOTS",
                            "%r is outside the declared artifact roots; a packet cites receipts "
                            "that live where ARGUS keeps evidence" % r)
        if str(resolved) not in receipts:
            receipts.append(str(resolved))
    for rec in ctx.get("ink", {}).values():
        seal = ((rec.get("binding") or {}).get("source_hashes") or {}).get("seal_record")
        if isinstance(seal, str) and Path(seal).is_file():
            resolved = paths.resolve_served_path(seal, roots)
            if any(paths.within_root(resolved, root) for root in roots) and str(resolved) not in receipts:
                receipts.append(str(resolved))
    scroll = _scroll_of(ctx, d)
    return {"scroll": scroll, "target": target, "board_state": bs,
            "review_payload": ctx.get("payload"),
            "translation_candidates": TS.load_candidates(d),
            "receipt_paths": sorted(receipts)}


def _scroll_of(ctx: dict, d: Path) -> str:
    for rec in ctx.get("ink", {}).values():
        vol = (rec.get("binding") or {}).get("source_volume")
        if vol:
            return str(vol)
    return d.name


def _impl_packet_export(p: dict) -> dict:
    target, d = _target(p)
    inputs = _packet_inputs(p, d, target)
    plan = PP.build(inputs.pop("scroll"), out_root=PP.packets_root(target), dry_run=True, **inputs)
    out = {"target": target, "packet_id": plan["packet_id"], "files": plan["files"],
           "content_sha256": plan["content_sha256"], "out_dir": plan["out_dir"],
           "citations": len(inputs["receipt_paths"]),
           "changes": ["writes %d file(s) under %s (each listed with its sha256 below)"
                       % (len(plan["files"]) + 1, plan["out_dir"]),
                       "publishes, uploads and submits nothing"],
           "never_published": True}
    if Path(plan["out_dir"]).exists():
        out["refusal"] = {"code": "PACKET_EXISTS",
                          "why": "this exact state is already exported at %s; the same state "
                                 "exports to the same packet" % plan["out_dir"]}
    return out


def _do_packet_export(spec, plan):
    p = spec.params
    target, d = _target(p)
    inputs = _packet_inputs(p, d, target)
    res = PP.build(inputs.pop("scroll"), out_root=PP.packets_root(target), dry_run=False, **inputs)
    if res["content_sha256"] != plan["content_sha256"]:
        raise A.Refused("PLAN_NOT_APPROVED", "the state changed between the plan and the write")
    return {"status": "OK", "packet_id": res["packet_id"], "out_dir": res["out_dir"],
            "files": res["files"], "manifest_sha256": res["manifest_sha256"],
            "never_published": True}


_PKT_ID = re.compile(r"^PKT-[0-9a-f]{16}$")


def resolve_packet(target: str, ref: str) -> Path:
    root = PP.packets_root(target)
    if _PKT_ID.fullmatch(ref or ""):
        return root / ref
    base = Path(paths.user_data_root("interpretation_packets")).resolve()
    if paths.is_remote_path(ref) or not paths.plain_local_path(ref):
        raise A.Refused("OUTSIDE_PACKETS_ROOT", "a packet is named by its id, or by a path inside %s" % base)
    p = Path(ref).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise A.Refused("OUTSIDE_PACKETS_ROOT",
                        "a packet is named by its id, or by a path inside %s" % base) from None
    return p


def _impl_packet_verify(p: dict) -> dict:
    target = _need(p, "target")
    ref = _need(p, "packet")
    return {"target": target, "packet": ref,
            "changes": [], "read_only": True,
            "note": "reads the packet and re-hashes every file and every cited receipt; nothing "
                    "is written"}


def _do_packet_verify(spec, plan):
    p = spec.params
    res = PP.verify(resolve_packet(p["target"], p["packet"]))
    return {"status": "OK", "verification": res}



_M = {
    "candidate.open": _meta(
        ["appends review tasks for ranked candidate regions"],
        ["MISSING_PARAMETER", "UNKNOWN_TARGET", "DATA_UNAVAILABLE", "CANDIDATE_REFUSED"]),
    "glyph.annotate": _meta(
        ["records one attributed letter judgment"],
        ["HUMAN_ACTOR_REQUIRED", "NOT_AN_ACCEPTED_REGION", "REVIEW_REFUSED", "ALPHABET_MISMATCH"]),
    "htr.propose": _meta(
        ["records one model proposal as an AI_AGENT answer"],
        ["PROVIDER_NOT_INSTALLED", "NO_SUCH_TASK", "REVIEW_REFUSED"]),
    "transcription.claim": _meta(
        ["appends one transcription claim"],
        ["HUMAN_ACTOR_REQUIRED", "CLAIM_REFUSED", "UNKNOWN_TARGET"]),
    "language.write": _meta(
        ["writes LANGUAGE.json"], ["HUMAN_ACTOR_REQUIRED", "BAD_LANGUAGE", "NO_CHANGE"],
        reversible=True),
    "translation.propose": _meta(
        ["appends one translation proposal"],
        ["HUMAN_ACTOR_REQUIRED", "NO_TRANSCRIPTION_CLAIM", "TRANSLATION_NOT_READY",
         "TRANSLATION_REFUSED"]),
    "packet.export": _meta(
        ["writes a packet directory"], ["PACKET_REFUSED", "USER_DATA_ROOT", "UNKNOWN_TARGET"]),
    "packet.verify": _meta([], ["OUTSIDE_PACKETS_ROOT", "MISSING_PARAMETER"], reversible=True,
                           read_only=True),
}

_IMPL = {
    "candidate.open": (_impl_candidate_open, _do_candidate_open, False),
    "glyph.annotate": (_impl_glyph_annotate, _do_glyph_annotate, True),
    "htr.propose": (_impl_htr_propose, _do_htr_propose, False),
    "transcription.claim": (_impl_transcription_claim, _do_transcription_claim, True),
    "language.write": (_impl_language_write, _do_language_write, True),
    "translation.propose": (_impl_translation_propose, _do_translation_propose, True),
    "packet.export": (_impl_packet_export, _do_packet_export, False),
    "packet.verify": (_impl_packet_verify, _do_packet_verify, False),
}

PLANNERS: dict = {}
DOERS: dict = {}
for _name, (_impl, _exec, _human_only) in _IMPL.items():
    PLANNERS[_name] = _make_planner(_name, _M[_name], _impl)
    DOERS[_name] = _make_doer(_name, PLANNERS[_name], _exec, human=_human_only,
                              needs_approval=_name != "packet.verify")
