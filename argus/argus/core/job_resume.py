"""job.resume: put an interrupted job back to work through one plan-then-approve door."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from argus.core import actions as A
from argus.core import jobs as J
from argus.core import manifest as MF
from argus.core import paths

CONTRACT = "argus-job-resume-plan-v1"
PLAN_FIELDS = frozenset({"job_id", "stage", "approved_plan_sha256"})

KIND_UNIT_LEDGER = "UNIT_LEDGER"
KIND_SCROLL_WORKFLOW = "SCROLL_WORKFLOW"
KIND_PROVIDER_TILED = "PROVIDER_TILED_RUN"
KIND_NONE = "NOT_RESUMABLE"

SCROLL_WORKFLOW_ACTION = "surface.prepare_exact_eligible"
PROVIDER_ACTION = "provider.invoke"
SURFACE_ENABLE_VAR = "ARGUS_SURFACE_PREPARE_ENABLED"
PROVIDER_ENABLE_VAR = "ARGUS_PROVIDER_EXECUTION_ENABLED"

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_PREVIEW = 10
MAX_OVERVIEW = 100


def _sha256_json(value) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _file_sha256(p) -> str | None:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def _flag(var: str) -> dict:
    return {"flag": var, "enabled": os.environ.get(var) == "1"}



def _unit_ledger_view(job_id: str, stage: str | None) -> dict:
    ws = J.open_workspace(job_id)
    sdir = ws.root / "stages"
    names = [p.name for p in sorted(sdir.iterdir()) if p.is_dir() and MF.exists(p)] \
        if sdir.is_dir() else []
    if not names:
        raise A.Refused("NO_STAGE", "job %s has no planned stage, so there is no unit ledger "
                        "to resume" % job_id)
    if stage:
        stage = J.ident(stage, field="stage")
        if stage not in names:
            raise A.Refused("NO_STAGE", "job %s has no stage %r; its stages are %s"
                            % (job_id, stage, names))
    else:
        views = {n: J.stage_resume_view(job_id, n) for n in names}
        open_ = [n for n in names if views[n]["resumable"]]
        if len(open_) > 1:
            raise A.Refused("AMBIGUOUS_STAGE", "job %s has %d resumable stages %s; name one"
                            % (job_id, len(open_), open_))
        stage = open_[0] if open_ else next(
            (n for n in names if views[n]["state"] != "complete"), names[-1])
    v = J.stage_resume_view(job_id, stage)
    return {
        "kind": KIND_UNIT_LEDGER, "job_id": job_id, "stage": stage, "state": v["state"],
        "resumable": v["resumable"], "why_not": v["why_not"], "unit_word": "unit",
        "units_total": v["units_total"], "done": v["done"], "pending": v["pending"],
        "resume_unit": v["resume_unit"], "stopped_after": v["stopped_after"],
        "binds": {"manifest_fingerprint": v["manifest_fingerprint"],
                  "unit_outputs_sha256": {u: r.get("sha256") for u, r in v["ledger"].items()},
                  "runner": v["runner"]},
        "stable": {"state": v["state"], "manifest_fingerprint": v["manifest_fingerprint"],
                   "done": v["done"], "pending": v["pending"], "ledger": v["ledger"]},
        "observed": {"heartbeat": v["heartbeat"]},
        "flag": _flag(J.JOBS_ENABLE_VAR),
        "delegate": {"to": "argus.core.jobs.act_resume_stage",
                     "recovers_interrupted": v["state"] == "interrupted"},
    }


def _receipt_name(p) -> str | None:
    return Path(p).name if p else None


def _scroll_workflow_view(rec: dict) -> dict:
    from argus.core import action_registry as AR
    from argus.core import scroll_workflow as SW
    from argus.core import scroll_ids
    from argus.core import stage_lineage as SL

    job_id = rec["job_id"]
    base = {"kind": KIND_SCROLL_WORKFLOW, "job_id": job_id, "stage": None, "unit_word": "stage",
            "flag": _flag(SURFACE_ENABLE_VAR), "observed": {}, "delegate": None}
    try:
        canon = scroll_ids.resolve(str((rec.get("params") or {}).get("scroll") or ""))
    except KeyError as e:
        return dict(base, state="unresolvable", resumable=False, units_total=0, done=[],
                    pending=[], resume_unit=None, stopped_after=None, binds={},
                    why_not="UNRESOLVABLE_SCROLL: %s" % e, stable={"why": str(e)})
    st = SW.status(canon)
    if st["chain_state"] not in ("VERIFIED", "EMPTY"):
        return dict(base, state="unverifiable", resumable=False, units_total=0, done=[],
                    pending=[], resume_unit=None, stopped_after=None, binds={},
                    why_not="the stage lineage chain for %s is %s; a resume would extend a "
                            "history that no longer verifies" % (canon, st["chain_state"]),
                    stable={"chain_state": st["chain_state"]})
    order = list(st["stages"])
    resume_from = st["resume_from"]
    idx = order.index(resume_from) if resume_from else len(order)
    done_ids, pending_ids = order[:idx], order[idx:]
    receipts = []
    for sid in done_ids:
        latest = st["stages"][sid].get("latest") or {}
        receipts.append({"stage_id": sid, "outcome": latest.get("outcome"),
                         "receipt": _receipt_name(latest.get("receipt_path")),
                         "receipt_sha256": _file_sha256(latest["receipt_path"])
                         if latest.get("receipt_path") else None})
    head = SL.read(canon).get("head")
    job_state = rec.get("state")
    lease = A.read_lease("data")
    alive = bool(lease and str(lease.get("holder", "")) ==
                 "surface-prepare:%s" % rec.get("idempotency_key")) and A.lease_holder_alive("data") is not False
    delegate = AR._surface_prepare_plan({"scroll": canon})
    why_not = None
    if job_state in ("PLANNED_ONLY",):
        word, why_not = "planned", "this record is a dry run; nothing was started"
    elif job_state == "CANCELLED":
        word, why_not = "cancelled", "the job was cancelled"
    elif job_state == "RUNNING" and alive:
        word, why_not = "running", "the data lease is held by this job's own process; " \
                                   "resuming would run it twice"
    elif resume_from is None:
        word, why_not = "complete", "every stage is recorded COMPLETED in %s's lineage" % canon
    else:
        word = "interrupted" if job_state == "RUNNING" else "halted"
        if not delegate.get("ready"):
            why_not = str(delegate.get("planning_refusal") or "the surface plan is not ready")
    return dict(
        base, state=word, resumable=why_not is None, why_not=why_not, scroll=canon,
        units_total=len(order), done=done_ids, pending=pending_ids, resume_unit=resume_from,
        stopped_after=done_ids[-1] if done_ids else None,
        binds={"lineage_head": head, "chain_state": st["chain_state"], "receipts": receipts,
               "delegate_plan_sha256": delegate.get("plan_sha256")},
        stable={"state": word, "scroll": canon, "head": head, "done": receipts,
                "pending": pending_ids, "delegate": delegate.get("plan_sha256")},
        delegate={"to": "surface.prepare_exact_eligible -> scroll_workflow.run",
                  "eligible_target_gate": (delegate.get("eligible_target_gate") or {})
                  .get("verdict"), "ready": bool(delegate.get("ready"))})


def _provider_receipts(capability_id: str, source_binding) -> list:
    root = paths.science_data("provider_runs")
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.json"))[:2000]:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(r, dict) and r.get("schema") == "argus-villa-provider-receipt-v1"
                and r.get("capability_id") == capability_id
                and r.get("source_binding") == source_binding):
            out.append(r)
    return out


def _provider_view(rec: dict) -> dict:
    from argus.core import tiled_run_resume as TR

    params = rec.get("params") or {}
    cap = str(params.get("capability_id") or "")
    parts = (params.get("options") or {}).get("num_parts")
    base = {"kind": KIND_PROVIDER_TILED, "job_id": rec["job_id"], "stage": None,
            "unit_word": "part", "flag": _flag(PROVIDER_ENABLE_VAR), "observed": {},
            "resumable": False, "delegate": None}
    if cap not in TR.TILED_CAPABILITIES or isinstance(parts, bool) or not isinstance(parts, int) \
            or parts < 2:
        return dict(base, state="single_invocation", units_total=1, done=[], pending=[],
                    resume_unit=None, stopped_after=None, binds={}, stable={"cap": cap},
                    why_not="this provider job is one invocation, not a tiled multi-part run, "
                            "so there is nothing to resume; re-plan provider.invoke")
    try:
        tp = TR.plan_resume(cap, parts, _provider_receipts(cap, params.get("source_binding")))
    except TR.TiledRunRefusal as e:
        return dict(base, state="unresumable", units_total=parts, done=[], pending=[],
                    resume_unit=None, stopped_after=None, binds={}, stable={"refusal": str(e)},
                    why_not=str(e))
    done, missing = tp["done_part_ids"], tp["missing_part_ids"]
    return dict(
        base, state="complete" if tp["complete"] else "partial", units_total=parts,
        done=[str(i) for i in done], pending=[str(i) for i in missing],
        resume_unit=str(tp["next_part_id"]) if tp["next_part_id"] is not None else None,
        stopped_after=str(done[-1]) if done else None,
        why_not=("every part has an OK receipt" if tp["complete"] else
                 "each missing part is its own governed provider.invoke and needs its own "
                 "launch authorization and %s=1; job.resume does not launch provider "
                 "processes. Plan part %s there. The completed parts share consistent seam "
                 "parameters" % (PROVIDER_ENABLE_VAR, tp["next_part_id"])),
        binds={"seam_parameters_consistent": tp["seam_parameters_consistent"]},
        stable={"done": done, "missing": missing, "reference": tp["reference_parameters"]},
        delegate={"to": "provider.invoke, one part at a time"})


def _refused_view(rec: dict, why: str, state: str) -> dict:
    return {"kind": KIND_NONE, "job_id": rec["job_id"], "stage": None, "state": state,
            "resumable": False, "why_not": why, "unit_word": "unit", "units_total": 0,
            "done": [], "pending": [], "resume_unit": None, "stopped_after": None, "binds": {},
            "stable": {"state": state, "action": rec.get("action")}, "observed": {},
            "flag": None, "delegate": None}


def view(job_id: str, stage: str | None = None) -> dict:
    """Resolve one job id to its resumable kind and read where it stopped."""
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id) or job_id.endswith("."):
        raise A.Refused("BAD_JOB_ID", "job_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    rec = A.read_job(job_id)
    if rec:
        action = rec.get("action")
        if action == SCROLL_WORKFLOW_ACTION:
            return _scroll_workflow_view(rec)
        if action == PROVIDER_ACTION:
            return _provider_view(rec)
        return _refused_view(
            rec, "%s has no resume machinery: it is one indivisible step with no unit ledger "
                 "and no stage lineage, so an interrupted or refused one is submitted again as "
                 "a new governed request rather than resumed" % action, "not_resumable")
    try:
        return _unit_ledger_view(job_id, stage)
    except J.JobRefusal as e:
        if e.cls == "UNKNOWN_JOB":
            raise A.Refused("NO_JOB", "no job %r in the command ledger or the job workspaces"
                            % job_id) from None
        raise A.Refused(e.cls, e.detail) from None
    except MF.ManifestViolation as e:
        raise A.Refused("NO_MANIFEST", str(e)) from None



def _changes(v: dict) -> list:
    n_pending, n_done = len(v["pending"]), len(v["done"])
    w = v["unit_word"]
    out = []
    if v["kind"] == KIND_UNIT_LEDGER:
        if v["delegate"]["recovers_interrupted"]:
            out.append("first marks stage %s PARTIAL: its control file says RUNNING but its "
                       "heartbeat has gone stale, so the worker is gone" % v["stage"])
        out.append("resumes stage %s of job %s at %s %s, running the %d pending %s(s): %s"
                   % (v["stage"], v["job_id"], w, v["resume_unit"], n_pending, w,
                      ", ".join(v["pending"][:_PREVIEW]) + (" ..." if n_pending > _PREVIEW else "")))
        out.append("will NOT run the %d completed %s(s) again; their outputs stay as written "
                   "and are bound below by sha256" % (n_done, w))
        out.append("binds to manifest %s and the sha256 of %d completed unit output(s); a job "
                   "that moves before approval is refused"
                   % (v["binds"]["manifest_fingerprint"][:12], n_done))
        out.append("writes only inside this job's own workspace, through the vetted %r runner; "
                   "fetches nothing" % v["binds"].get("runner"))
    elif v["kind"] == KIND_SCROLL_WORKFLOW:
        out.append("resumes the process-contract walk for %s at stage %s, running %d pending "
                   "stage(s): %s" % (v.get("scroll"), v["resume_unit"], n_pending,
                                     ", ".join(v["pending"][:_PREVIEW])))
        out.append("will NOT re-attempt the %d stage(s) already resolved in the lineage; their "
                   "receipts are bound below" % n_done)
        out.append("binds to lineage head %s and the receipt sha256 of %d resolved stage(s)"
                   % (str(v["binds"].get("lineage_head"))[:12], n_done))
        out.append("delegates to surface.prepare_exact_eligible, so its eligible-target gate "
                   "(GEOMETRY_ONLY ceiling), storage floors, data lease and %s switch all apply"
                   % SURFACE_ENABLE_VAR)
    return out


def _not_ready(body: dict, code: str, why: str) -> dict:
    body.update(ready=False, resumable=False, would_be_refused=True, refusal_code=code,
                planning_refusal="%s: %s" % (code, why), note=why)
    body["plan_sha256"] = _sha256_json({k: body[k] for k in ("schema", "refusal_code",
                                                               "planning_refusal")})
    return body


def plan(params: dict) -> dict:
    """The zero-side-effect plan `job.resume` returns for /plan and re-derives inside `do`."""
    unknown = sorted(set(params) - PLAN_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "job.resume does not accept %s" % unknown)
    body = {"schema": CONTRACT,
            "changes": ["re-queues nothing new: resumes one interrupted job from where its own "
                        "ledger says it stopped"],
            "cost": {"gpu": "none", "network": "none for a unit-ledger job; a scroll job "
                     "inherits surface.prepare_exact_eligible's bounded read",
                     "seconds": "the pending units only"},
            "leases": [], "reversible": False,
            "may_refuse": ["NO_JOB", "NOT_RESUMABLE", "PLAN_NOT_APPROVED", "JOBS_DISABLED",
                           "SURFACE_PREPARE_DISABLED", "ELIGIBLE_TARGET_GATE", "STORAGE_FLOOR",
                           "AMBIGUOUS_STAGE", "NO_STAGE"],
            "scientific_boundary": "resuming continues mechanical work already approved and "
                                   "planned; it qualifies no detector and reads nothing"}
    job_id = params.get("job_id")
    if not job_id:
        return _not_ready(body, "MISSING_PARAMETER", "job_id is required")
    try:
        v = view(str(job_id), params.get("stage"))
    except A.Refused as e:
        return _not_ready(body, e.code, e.why)
    body.update(
        kind=v["kind"], job_id=v["job_id"], stage=v["stage"], job_state=v["state"],
        scroll=v.get("scroll"),
        resumable=v["resumable"], why_not=v["why_not"],
        resume_point={"unit": v["resume_unit"], "stopped_after": v["stopped_after"],
                      "index": len(v["done"]), "of": v["units_total"], "unit_word": v["unit_word"]},
        units={"total": v["units_total"], "done": v["done"], "pending": v["pending"]},
        will_not_rerun=v["done"], binds_to=v["binds"], observed=v["observed"],
        delegates_to=v["delegate"], enabled=(v["flag"] or {}).get("enabled"),
        enable_flag=(v["flag"] or {}).get("flag"))
    if v["resumable"]:
        body["changes"] = _changes(v)
        body["ready"] = True
        if v["flag"] and not v["flag"]["enabled"]:
            body["note"] = ("planning is always allowed; the resume itself stays disabled until "
                            "the operator sets %s=1" % v["flag"]["flag"])
    else:
        body.update(ready=False, would_be_refused=True, note=v["why_not"],
                    changes=["nothing: this job cannot be resumed (see why_not)"],
                    refusal_code="NOT_RESUMABLE")
    body["plan_sha256"] = _sha256_json({"schema": CONTRACT, "kind": v["kind"],
                                        "job_id": v["job_id"], "stage": v["stage"],
                                        "resumable": v["resumable"], "stable": v["stable"]})
    return body



def execute(params: dict, *, actor: str, request_id: str, idempotency_key: str) -> dict:
    p = plan(params)
    if not p.get("ready"):
        raise A.Refused(p.get("refusal_code") or "NOT_RESUMABLE",
                        p.get("planning_refusal") or p.get("why_not") or "not resumable",
                        plan=p)
    if params.get("approved_plan_sha256") != p["plan_sha256"]:
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the plan hash returned by /plan; the "
                        "job may have moved since it was planned", expected=p["plan_sha256"])
    if p["kind"] == KIND_UNIT_LEDGER:
        out = _resume_unit_ledger(p)
    elif p["kind"] == KIND_SCROLL_WORKFLOW:
        out = _resume_scroll_workflow(p, params, actor, request_id, idempotency_key)
    else:
        raise A.Refused("NOT_RESUMABLE", p.get("why_not") or "not resumable", plan=p)
    _record_history(p, actor)
    return out


def _resume_unit_ledger(p: dict) -> dict:
    if os.environ.get(J.JOBS_ENABLE_VAR) != "1":
        raise A.Refused("JOBS_DISABLED",
                        "resuming a job re-runs units, and job execution is off. Nothing here "
                        "starts work unless the operator sets %s=1 deliberately"
                        % J.JOBS_ENABLE_VAR)
    job_id, stage = p["job_id"], p["stage"]
    fingerprint = p["binds_to"]["manifest_fingerprint"]
    try:
        if p["job_state"] == "interrupted":
            J.mark_interrupted(job_id, stage)
        res = J.act_resume_stage(job_id, stage, fingerprint, 0)
    except J.JobRefusal as e:
        raise A.Refused("RESUME_REFUSED", e.detail, refusal_class=e.cls) from None
    except MF.ManifestViolation as e:
        raise A.Refused("RESUME_REFUSED", str(e)) from None
    keep = ("state", "executed_units", "completed_units", "total_units", "reused",
            "manifest_fingerprint")
    return {"status": "OK", "kind": KIND_UNIT_LEDGER, "job_id": job_id, "stage": stage,
            "resumed": True, "skipped_completed_units": p["will_not_rerun"],
            "result": {k: res.get(k) for k in keep}, "plan_sha256": p["plan_sha256"]}


def _resume_scroll_workflow(p: dict, params: dict, actor: str, request_id: str,
                            idempotency_key: str) -> dict:
    from argus.core import action_registry as AR

    surface_plan = AR._surface_prepare_plan({"scroll": p["scroll"]})
    spec = A.ActionSpec(
        action=SCROLL_WORKFLOW_ACTION, actor=actor, request_id=request_id,
        idempotency_key=idempotency_key,
        params={"scroll": p["scroll"], "approved_plan_sha256": surface_plan.get("plan_sha256")})
    doc = AR.do_surface_prepare(spec)
    return {"status": "OK", "kind": KIND_SCROLL_WORKFLOW, "job_id": p["job_id"], "resumed": True,
            "resumed_from": doc.get("resumed_from"),
            "skipped_completed_stages": p["will_not_rerun"],
            "attempts": doc.get("attempts"), "halted_at": doc.get("halted_at"),
            "halted_reason": doc.get("halted_reason"), "complete": doc.get("complete"),
            "plan_sha256": p["plan_sha256"]}


def _record_history(p: dict, actor: str) -> None:
    rec = A.read_job(p["job_id"])
    if not rec:
        return
    rec.setdefault("resume_history", []).append({
        "by": actor, "plan_sha256": p["plan_sha256"], "kind": p["kind"],
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    A.write_job(rec)



def _row(v: dict, action: str | None = None) -> dict:
    pending = v["pending"]
    return {"job_id": v["job_id"], "kind": v["kind"], "stage": v["stage"], "action": action,
            "state": v["state"], "resumable": v["resumable"], "why_not": v["why_not"],
            "unit_word": v["unit_word"], "units_total": v["units_total"],
            "units_done": len(v["done"]), "units_pending": len(pending),
            "resume_unit": v["resume_unit"], "stopped_after": v["stopped_after"],
            "pending_preview": pending[:_PREVIEW], "heartbeat": v["observed"].get("heartbeat"),
            "flag": v["flag"],
            "resume_params": dict({"job_id": v["job_id"]},
                                  **({"stage": v["stage"]} if v["stage"] else {}))}


def overview(*, limit: int = MAX_OVERVIEW) -> dict:
    """Every job that could be resumed or is worth explaining, for GET /api/resumable-jobs."""
    rows = []
    for sv in J.list_stage_views(limit=limit):
        if sv.get("state") == "unreadable":
            rows.append({"job_id": sv["job_id"], "kind": KIND_UNIT_LEDGER, "stage": sv["stage"],
                         "state": "unreadable", "resumable": False, "why_not": sv["why_not"],
                         "units_total": 0, "units_done": 0, "units_pending": 0,
                         "resume_unit": None, "stopped_after": None, "pending_preview": [],
                         "unit_word": "unit", "action": None, "heartbeat": None, "flag": None,
                         "resume_params": {"job_id": sv["job_id"]}})
            continue
        rows.append(_row(_unit_ledger_view(sv["job_id"], sv["stage"])))
    cmd = []
    if A.JOBS.is_dir():
        for d in A.JOBS.iterdir():
            rec = A.read_job(d.name)
            if rec and rec.get("action") in (SCROLL_WORKFLOW_ACTION, PROVIDER_ACTION):
                cmd.append(rec)
    cmd.sort(key=lambda r: str(r.get("started_utc") or ""), reverse=True)
    for rec in cmd[:limit]:
        try:
            v = view(rec["job_id"])
        except A.Refused as e:
            rows.append({"job_id": rec["job_id"], "kind": KIND_NONE, "stage": None,
                         "action": rec.get("action"), "state": "unreadable", "resumable": False,
                         "why_not": e.why, "units_total": 0, "units_done": 0, "units_pending": 0,
                         "resume_unit": None, "stopped_after": None, "pending_preview": [],
                         "unit_word": "unit", "heartbeat": None, "flag": None,
                         "resume_params": {"job_id": rec["job_id"]}})
            continue
        except ModuleNotFoundError as e:
            missing = str(getattr(e, "name", None) or e)
            rows.append({"job_id": rec["job_id"], "kind": KIND_NONE, "stage": None,
                         "action": rec.get("action"), "state": "unreadable",
                         "resumable": False,
                         "why_not": ("this job's resume plan needs optional runtime package "
                                     "%s, which is not installed in the observe service; "
                                     "open it in the pinned science runtime" % missing),
                         "units_total": 0, "units_done": 0, "units_pending": 0,
                         "resume_unit": None, "stopped_after": None,
                         "pending_preview": [], "unit_word": "stage", "heartbeat": None,
                         "flag": None, "resume_params": {"job_id": rec["job_id"]}})
            continue
        rows.append(_row(v, rec.get("action")))
    by_state: dict = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    return {"schema": "argus-resumable-jobs-v1",
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "read_only": True,
            "jobs": rows[:limit], "by_state": by_state,
            "resumable": sum(1 for r in rows if r["resumable"]),
            "how": "resuming is a governed action: job.resume is planned (no side effects), "
                   "approved by the plan's own hash, then run. Completed units are never re-run."}
