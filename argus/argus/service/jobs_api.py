"""The job control plane: one POST, and it can only ask for something on the allowlist."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from argus.core import jobs as J
from argus.core import process_hardening as _process_hardening
_process_hardening.apply()
from argus.core import manifest as MF
from argus.service import receipts as R
from argus.service import request_guard as RG

JOBS_API_SCHEMA = "argus-jobs-api-v1"

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}

ENABLE_VAR = J.JOBS_ENABLE_VAR

UI_FIELDS = frozenset({
    "ok", "reused", "job_id", "jobs", "stage", "stages", "state", "terminal", "reason",
    "note", "why", "units", "unit", "total_units", "completed_units", "executed_units",
    "remaining_units", "remaining_seconds", "planned", "planned_outputs", "estimate",
    "gpu_required", "manifest_fingerprint", "approve_with", "outputs", "missing",
    "artifacts", "artifact_count", "assets", "count", "truncated", "subdir", "staged",
    "total_staged", "staged_assets", "bytes", "progress", "candidate", "candidates",
    "sealed", "sealed_candidates", "unsealed_by", "preserved", "evidence_sha256",
    "schema", "actions", "enabled", "read_only_service", "withheld_fields",
})

ROOT_FIELDS = ("workspace", "input_root", "output_root")

STATUS = {"UNKNOWN_ACTION": 400, "BAD_ARGUMENT": 422, "PATH_ESCAPE": 403,
          "UNKNOWN_JOB": 404, "UNKNOWN_RUNNER": 400, "NOT_APPROVED": 403,
          "STATE": 409, "BUDGET": 413, "NOT_PRESERVED": 409, "SEALED": 423,
          "NO_MANIFEST": 409}
MANIFEST_STATUS = {"NO_MANIFEST": 409, "MANIFEST_REWRITTEN": 409, "UNREADABLE": 422,
                   "BAD_MANIFEST": 422, "INPUT_MISSING": 409, "OUTPUT_MISSING": 409,
                   "HASH_MISMATCH": 409}


def enabled() -> bool:
    return os.environ.get(ENABLE_VAR, "") == "1"


def public_view(rec: dict) -> dict:
    """What leaves the service: allowlisted fields, sanitized values, visible omissions."""
    out, withheld = {}, []
    for k, v in rec.items():
        if k in ROOT_FIELDS:
            withheld.append(k)
            continue
        if k not in UI_FIELDS:
            withheld.append(k)
            continue
        out[k] = R.sanitize_value(v)
    if withheld:
        out["withheld_fields"] = sorted(withheld)
        out["note"] = (str(out.get("note", "")) + " " if out.get("note") else "") + \
                      "service-side fields (roots, and anything not on the interface " \
                      "allowlist) are withheld by name rather than dropped silently"
    return R.guarded(out)


class ActionRequest(BaseModel):
    """The whole request surface."""

    action: str = Field(max_length=64)
    args: dict = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


app = FastAPI(title="ARGUS job control", version="0.1.0",
              description="Allowlisted job actions. Localhost, opt-in, no shell.")
app.add_middleware(RG.BodyLimit, max_bytes=64 * 1024)


@app.middleware("http")
async def loopback_only(request: Request, call_next):
    host = (request.client.host if request.client else "") or ""
    if host not in LOOPBACK:
        return JSONResponse(status_code=403, content={
            "error": "this control plane answers on the loopback interface only",
            "detail": "nothing here authenticates, so nothing here should be reachable"})
    if not RG.host_ok(request.headers.get("host")) or not RG.fetch_site_ok(request.headers.get("sec-fetch-site")):
        return JSONResponse(status_code=421 if not RG.host_ok(request.headers.get("host")) else 403, content={
            "error": "not addressed to this service, or requested by another site",
            "detail": "a web page cannot reach this control plane by rebinding its name to loopback"})
    return await call_next(request)


def _refuse(e) -> HTTPException:
    if isinstance(e, J.JobRefusal):
        return HTTPException(STATUS.get(e.cls, 400), e.detail)
    return HTTPException(MANIFEST_STATUS.get(e.cls, 409), e.detail)


@app.get("/api/jobs/actions")
def actions():
    """The allowlist itself."""
    return {"schema": JOBS_API_SCHEMA, "enabled": enabled(),
            "actions": J.describe_actions(),
            "note": "an action name outside this list is refused before anything is "
                    "looked up; there is no path by which one becomes a command"}


@app.post("/api/jobs/action")
def action(req: ActionRequest):
    """THE ONE MUTATION ROUTE."""
    spec = J.ACTIONS.get(req.action)
    if spec is not None and spec.mutating and not enabled():
        raise HTTPException(403,
                            "job execution is off. This service does not start work "
                            "unless %s=1 is set deliberately; the read-only observatory "
                            "reports that the interface cannot start work, and this is "
                            "what makes that true." % ENABLE_VAR)
    try:
        return public_view(J.dispatch(req.action, req.args))
    except J.JobRefusal as e:
        raise _refuse(e)
    except MF.ManifestViolation as e:
        raise _refuse(e)


@app.get("/api/jobs")
def jobs():
    return public_view(J.dispatch("list_jobs", {}))


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    try:
        return public_view(J.dispatch("job_status", {"job_id": job_id}))
    except J.JobRefusal as e:
        raise _refuse(e)


@app.get("/api/jobs/{job_id}/artifacts")
def artifacts(job_id: str, stage: str = ""):
    try:
        return public_view(J.dispatch("open_artifacts",
                                      {"job_id": job_id, "stage": stage}))
    except J.JobRefusal as e:
        raise _refuse(e)


@app.get("/api/jobs/{job_id}/candidates/{candidate}")
def candidate(job_id: str, candidate: str):
    """A sealed candidate is REFUSED here."""
    try:
        ws = J.open_workspace(job_id)
        return public_view({"ok": True, "job_id": ws.job_id, "candidate": candidate,
                            "sealed": False,
                            "evidence_sha256": J.read_candidate(ws, candidate)
                            ["evidence_sha256"]})
    except J.JobRefusal as e:
        raise _refuse(e)


def selftest() -> bool:
    import shutil
    import tempfile

    from fastapi.testclient import TestClient

    ck = []
    tmp = Path(tempfile.mkdtemp(prefix="argus_jobs_api_"))
    prior = {k: os.environ.get(k) for k in
             ("ARGUS_JOBS_ROOT", "ARGUS_JOB_INPUT_ROOTS", ENABLE_VAR)}
    try:
        src = tmp / "inputs"
        src.mkdir()
        for i in range(3):
            (src / ("u%d.txt" % i)).write_text("payload %d" % i, encoding="utf-8")
        os.environ["ARGUS_JOBS_ROOT"] = str(tmp / "jobs")
        os.environ["ARGUS_JOB_INPUT_ROOTS"] = str(tmp)
        os.environ.pop(ENABLE_VAR, None)
        c = TestClient(app)

        r = c.post("/api/jobs/action", json={"action": "create_workspace",
                                             "args": {"job_id": "j1",
                                                      "input_root": str(src)}})
        ck.append(("SABOTAGE with the switch off, a mutating action is REFUSED and says "
                   "why", r.status_code == 403 and ENABLE_VAR in r.json()["detail"]))
        ck.append(("but the allowlist is still readable, so an interface can render its "
                   "disabled state", c.get("/api/jobs/actions").status_code == 200))

        os.environ[ENABLE_VAR] = "1"
        r = c.post("/api/jobs/action", json={"action": "create_workspace",
                                             "args": {"job_id": "j1",
                                                      "input_root": str(src)}})
        ck.append(("with it on, the action runs", r.status_code == 200
                   and r.json()["ok"] is True))
        body = r.json()
        ck.append(("SABOTAGE and the roots do NOT reach the interface",
                   "input_root" not in body and "workspace" not in body
                   and "output_root" not in body))
        ck.append(("the omission is visible rather than silent",
                   set(body["withheld_fields"]) >= set(ROOT_FIELDS)))
        ck.append(("no absolute path survives anywhere in the payload",
                   R.leaks(body) == []))

        bad = c.post("/api/jobs/action", json={"action": "os.system",
                                               "args": {"cmd": "dir"}})
        ck.append(("SABOTAGE an unlisted action is 400 and names the permitted set",
                   bad.status_code == 400 and "permitted" in bad.text.lower()))
        ck.append(("SABOTAGE a real function name that is not an action is refused too",
                   c.post("/api/jobs/action",
                          json={"action": "act_create_workspace",
                                "args": {}}).status_code == 400))
        ck.append(("SABOTAGE a request with a field outside the envelope is refused",
                   c.post("/api/jobs/action",
                          json={"action": "list_jobs", "args": {},
                                "shell": True}).status_code == 422))
        ck.append(("SABOTAGE an unknown argument is 422, not ignored",
                   c.post("/api/jobs/action",
                          json={"action": "list_jobs",
                                "args": {"cmd": "x"}}).status_code == 422))

        for evil in ("../../etc/passwd", "D:/Windows/system32", "a; rm -rf /"):
            got = c.post("/api/jobs/action",
                         json={"action": "inspect_assets",
                               "args": {"job_id": "j1", "subdir": evil}})
            if got.status_code not in (403, 422):
                break
        ck.append(("SABOTAGE traversal, absolute paths and shell strings are refused with "
                   "403/422, never executed", got.status_code in (403, 422)))

        def act(name, **args):
            rr = c.post("/api/jobs/action", json={"action": name, "args": args})
            assert rr.status_code == 200, (name, rr.status_code, rr.text[:200])
            return rr.json()

        act("stage_assets", job_id="j1", items=["u0.txt", "u1.txt"])
        gen = act("generate_manifest", job_id="j1", stage="s1", runner="checksum")
        fp = gen["manifest_fingerprint"]
        ck.append(("the plan is returned by fingerprint, which is what gets approved",
                   len(fp) == 64))
        run = act("start_stage", job_id="j1", stage="s1", approved_fingerprint=fp)
        ck.append(("an approved stage runs and reports real counts",
                   run["state"] == "DONE" and run["completed_units"] == 2))
        again = act("start_stage", job_id="j1", stage="s1", approved_fingerprint=fp)
        ck.append(("re-running it through the API is idempotent",
                   again["reused"] is True))

        act("preserve_evidence", job_id="j1", candidate="c1", stage="s1",
            items=["u0.txt.sha256"])
        act("index_candidate", job_id="j1", candidate="c1", stage="s1",
            interpretation="a column of ink at x=412")
        got = c.get("/api/jobs/j1/candidates/c1")
        ck.append(("SABOTAGE the API REFUSES a sealed candidate with 423",
                   got.status_code == 423))
        ck.append(("SABOTAGE and the interpretation is nowhere in the refusal",
                   "column of ink" not in got.text))
        st = c.get("/api/jobs/j1").json()
        ck.append(("status names the sealed candidate without its content",
                   st["sealed_candidates"] == ["c1"]
                   and "column of ink" not in json.dumps(st)))

        act("unseal_candidate", job_id="j1", candidate="c1", who="operator",
            reason="writing up the result")
        ck.append(("after a deliberate unseal it is readable",
                   c.get("/api/jobs/j1/candidates/c1").status_code == 200))

        ck.append(("an unknown job is 404", c.get("/api/jobs/nope").status_code == 404))
        ck.append(("a job id that is a path is 422",
                   c.get("/api/jobs/..%2F..%2Fetc").status_code in (404, 422)))

        arts = c.get("/api/jobs/j1/artifacts", params={"stage": "s1"}).json()
        ck.append(("artifacts are listed by relative path only",
                   all(not Path(a["rel"]).is_absolute()
                       for a in arts["stages"][0]["artifacts"])))

        ck.append(("this service has no subprocess, eval, exec or shell surface",
                   J.execution_surface_offences(Path(__file__)) == []))
        ck.append(("the read-only observatory app has NOT gained a write route",
                   _observatory_is_still_read_only()))
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    for msg, good in ck:
        print("  %s %s" % ("PASS" if good else "FAIL", msg))
        ok &= bool(good)
    print("selftest: %d/%d passed" % (sum(1 for _, g in ck if g), len(ck)))
    return ok


def _observatory_is_still_read_only() -> bool:
    """The separation is the whole design; check it rather than assert it in prose."""
    from argus.service import app as A

    verbs = set()
    for r_ in A.app.routes:
        try:
            verbs |= set(r_.methods)
        except AttributeError:
            pass
    return not (verbs & A.WRITE_METHODS)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="ARGUS job control plane")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8788)
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(0 if selftest() else 1)
    if not enabled():
        print("refusing to serve: set %s=1 to enable job execution" % ENABLE_VAR)
        raise SystemExit(2)
    import uvicorn

    uvicorn.run(app, host=a.host, port=a.port)
