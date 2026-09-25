"""The one door the browser may knock on."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import hashlib
import json
import os
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.request

from fastapi import Body, Cookie, FastAPI, Header, HTTPException, Request, Response

from argus.core.constant_time import same_secret
from argus.service.request_guard import BodyLimit
from argus.core import process_hardening as _process_hardening
_process_hardening.apply()

REPO = pathlib.Path(_argus_public_path('repo', ''))
COMMAND_BASE = os.environ.get("ARGUS_COMMAND_BASE", "http://127.0.0.1:8788")
TOKEN_PATH = pathlib.Path(os.environ.get("ARGUS_COMMAND_TOKEN_FILE",
                                         _argus_public_path('home', 'state/command_token')))
UI_ACCESS_KEY_PATH = pathlib.Path(
    os.environ.get(
        "ARGUS_UI_ACCESS_KEY_FILE",
        str(pathlib.Path(os.environ.get("ARGUS_HOME", _argus_public_path('home', '')))
            / "state" / "ui_access_key")))
AUDIT_PATH = pathlib.Path(os.environ.get("ARGUS_BFF_AUDIT",
                                         _argus_public_path('home', 'state/bff_audit.jsonl')))

_DEFAULT_UI_ORIGINS = {
  "http://127.0.0.1:5173",
  "http://127.0.0.1:8792",
}
ALLOWED_ORIGINS = frozenset(_DEFAULT_UI_ORIGINS | {
    origin.strip() for origin in os.environ.get("ARGUS_UI_ORIGINS", "").split(",")
    if origin.strip()
})

SESSION_TTL_S = 900
MAX_BODY_BYTES = 64 * 1024
MAX_ATTACHMENT_BODY_BYTES = 28 * 1024 * 1024

SECRET_KEYS = frozenset({"token", "bearer", "authorization", "secret", "password",
                         "api_key", "apikey", "command_token", "token_file"})

app = FastAPI(title="ARGUS UI command transport", docs_url=None, redoc_url=None,
              openapi_url=None)
app.add_middleware(BodyLimit, max_bytes=30 * 1024 * 1024)

_SESSIONS: dict = {}


@app.exception_handler(HTTPException)
async def _scrubbed_error(request: Request, exc: HTTPException):
    """EVERY error response goes through scrub(), not just the ones I remembered."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": scrub(exc.detail)})



def _token() -> str:
    """Read the bearer token."""
    if not TOKEN_PATH.is_file():
        raise HTTPException(status_code=503, detail={
          "error": "command service token not present",
          "how": "start the command service once so it generates one",
          "token_file": "not disclosed"})
    t = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not t:
        raise HTTPException(status_code=503, detail={"error": "token file is empty"})
    return t


def _ui_access_key() -> str:
    """Read the separately bootstrapped operator key; never create or disclose it here."""
    if not UI_ACCESS_KEY_PATH.is_file():
        raise HTTPException(status_code=503, detail={
          "error": "operator access key is not configured",
          "how": "start the command service once, then retrieve the local key for the operator"})
    key = UI_ACCESS_KEY_PATH.read_text(encoding="utf-8").strip()
    if not key:
        raise HTTPException(status_code=503, detail={
          "error": "operator access key is not configured"})
    return key


def require_ui_access_key(got: str | None) -> None:
    """Require the operator key before allocating a browser session."""
    want = _ui_access_key()
    if not same_secret(got, want):
        raise HTTPException(status_code=401, detail={
          "error": "operator access key refused",
          "why": "the separate local operator key is required to open a governed UI session"})


def scrub(obj):
    """Remove anything secret from a response, at any depth."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in SECRET_KEYS:
                out[k] = "[redacted]"
            else:
                out[k] = scrub(v)
        return out
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, str):
        try:
            tok = _token()
        except HTTPException:
            return obj
        return obj.replace(tok, "[redacted]") if tok and tok in obj else obj
    return obj



def require_origin(origin: str | None) -> str:
    """EXACT match."""
    if origin is None:
        raise HTTPException(status_code=403, detail={
          "error": "no Origin header",
          "why": "a mutation must state where it came from. A request that will not say is "
                 "refused rather than assumed local."})
    if origin not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail={
          "error": "origin not allowed", "origin": origin,
          "why": "the allowlist is matched exactly -- not by prefix, suffix or substring, "
                 "because a URL can contain an allowed origin without being it."})
    return origin



def _new_session(origin: str) -> dict:
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    s = {"sid": sid, "csrf": csrf, "origin": origin, "created": time.time(),
         "expires": time.time() + SESSION_TTL_S}
    _SESSIONS[sid] = s
    return s


def require_session(sid: str | None, csrf_header: str | None, origin: str) -> dict:
    if not sid or sid not in _SESSIONS:
        raise HTTPException(status_code=401, detail={
          "error": "no session",
          "why": "reaching the page is not authorisation. A session is established by a "
                 "deliberate operator act, so an idle tab cannot be made to act by a script "
                 "that merely reaches it."})
    s = _SESSIONS[sid]
    if time.time() > s["expires"]:
        _SESSIONS.pop(sid, None)
        raise HTTPException(status_code=401, detail={"error": "session expired"})
    if s["origin"] != origin:
        raise HTTPException(status_code=403, detail={"error": "session origin mismatch"})
    if not same_secret(csrf_header, s["csrf"]):
        raise HTTPException(status_code=403, detail={
          "error": "csrf token missing or wrong",
          "why": "double-submit: the cookie is HttpOnly and SameSite=Strict, and the matching "
                 "value must be echoed in a header a cross-site form post cannot set."})
    return s



OPERATIONS = {
  "preflight": {
    "params": {"action": str, "scrolls": str},
    "required": ("action",),
    "why": "computes what would block a route. Reads only.",
  },
  "inventory.scan": {
    "params": {},
    "required": (),
    "why": "enumerates what is on disk. Reads only.",
  },
  "evidence.reproduce": {
    "params": {"run": str},
    "required": ("run",),
    "why": "re-derives a run's own claim from its recorded inputs. Never executes anything -- "
           "do_evidence_reproduce's own docstring: \"Never runs it.\"",
  },
  "candidate.open": {
    "params": {"target": str, "segment": str, "region": list, "limit": int, "model": str,
               "approved_plan_sha256": str},
    "required": ("target", "segment", "region"),
    "why": "ranks candidate regions from the sealed probability planes under the frozen rule and appends them as review tasks for a person to judge. Creates no scientific status; existing tasks and answers are kept.",
  },
  "vigiles.final_decision": {
    "params": {"vigiles_receipt": str, "decision": str, "rationale": str,
               "supersedes_sha256": str},
    "required": ("vigiles_receipt", "decision", "rationale"),
    "why": "records the human final disposition around one VIGILES receipt; it cannot override or promote scientific certification.",
  },
  "identity.resolve": {
    "params": {"scroll": str, "volume_id": str, "source_url": str,
               "array_path": str, "declared": dict},
    "required": ("scroll", "volume_id", "source_url"),
    "why": "observes only official catalogue and small store metadata, returning the proven identity envelope required by acquisition; it never fetches a CT chunk.",
  },
  "acquire.execute": {
    "params": {
      "url": str, "scroll": str, "volume_id": str, "phase": str,
      "array_path": str, "roi": str, "zarray": dict, "byte_ceiling": int,
      "declared": dict, "official_identity": dict, "target_authority": dict,
      "authorization_id": str, "launch_packet": dict, "approved_plan_sha256": str,
    },
    "required": ("url", "scroll", "volume_id", "phase", "official_identity",
                 "authorization_id", "launch_packet"),
    "why": "executes one previously planned, identity-bound acquisition after the operator supplies the zero-fetch plan hash and launch authorization; the action remains byte-, lease- and provenance-gated.",
  },
  "provider.invoke": {
    "params": {
      "capability_id": str, "input_path": str, "output_path": str,
      "source_binding": dict, "options": dict, "authorization_id": str,
      "launch_packet": dict, "approved_plan_sha256": str, "timeout_s": int,
    },
    "required": ("capability_id", "input_path", "output_path", "source_binding",
                 "authorization_id", "launch_packet"),
    "why": "invokes only a pinned, typed Villa/VC3D adapter with immutable input/output receipts; a successful process is operational evidence, not a scientific qualification.",
  },
  "provider.update.policy": {
    "params": {"mode": str, "approved_plan_sha256": str}, "required": ("mode",),
    "why": "records whether ARGUS may check upstream for provider updates (AUTOMATIC, ASK_FIRST or NEVER).",
  },
  "provider.update.check": {
    "params": {"sources": list, "revision": str, "approved_plan_sha256": str}, "required": (),
    "why": "reads upstream metadata (no weights, no builds) and records observations and new discovered updates; with `revision` and one source it registers that exact immutable revision instead of following the channel head.",
  },
  "provider.update.stage": {
    "params": {"source_id": str, "revision": str, "approved_plan_sha256": str}, "required": ("source_id", "revision"),
    "why": "classifies one update, checks its licence and adapter contract and runs the sabotage self-check; activates nothing.",
  },
  "provider.update.test": {
    "params": {"source_id": str, "revision": str, "approved_plan_sha256": str}, "required": ("source_id", "revision"),
    "why": "runs ARGUS's own adapter tests for this source and records the result.",
  },
  "provider.update.attach_real_data_control": {
    "params": {"source_id": str, "revision": str, "receipt_path": str, "approved_plan_sha256": str},
    "required": ("source_id", "revision", "receipt_path"),
    "why": "attaches a receipt of a control run on real scroll data; a synthetic pass can never stand in for it.",
  },
  "provider.update.activate": {
    "params": {"source_id": str, "revision": str, "scope": str, "approved_plan_sha256": str},
    "required": ("source_id", "revision", "scope"),
    "why": "makes a compatible revision the one new runs of that scope use; existing runs keep their pins and the previous pin is kept for rollback.",
  },
  "provider.update.rollback": {
    "params": {"source_id": str, "scope": str, "approved_plan_sha256": str},
    "required": ("source_id", "scope"),
    "why": "restores the previous pin for a source and scope; the rolled-back revision is quarantined, not deleted.",
  },
  "surface.prepare_exact_eligible": {
    "params": {"scroll": str, "authorized": bool, "approved_plan_sha256": str},
    "required": ("scroll",),
    "why": ("runs one scroll's process-contract pipeline (identity, a bounded read of its own "
            "exact eligible volume, surface growth, flattening, multi-depth rendering, "
            "evidence recording) end to end via the already-qualified resumable workflow "
            "runner, halting honestly at the first stage that is not ready; the exact volume "
            "read is derived server-side from the registry, never accepted from the caller."),
  },
  "seed.grow.execute": {
    "params": {
      "executable": str, "qualification_receipt": str, "volume": str, "output_path": str,
      "params_path": str, "candidates": list, "source_binding": dict, "policy_path": str,
      "authorized": bool, "approved_plan_sha256": str, "timeout_s": int,
    },
    "required": ("executable", "qualification_receipt", "volume", "output_path",
                 "params_path", "candidates", "source_binding"),
    "why": "runs one real, already-qualified seed-growth binary against one already-registered scroll's volume, selecting its seed automatically via a deterministic, disclosed policy (argus.core.seed_growth.select_seed) -- never a human-typed seed point -- and gated by argus.core.eligible_target_operation_gate before anything executes.",
  },
  "blender.launch": {
    "params": {
      "mesh_path": str, "mode": str, "output_path": str, "report_path": str, "timeout_s": int,
      "approved_plan_sha256": str,
    },
    "required": ("mesh_path", "mode"),
    "why": "starts the real Blender executable against one existing mesh (interactively for a human, or a deterministic headless test edit) without ever overwriting the source mesh.",
  },
  "blender.verify_roundtrip": {
    "params": {
      "source_mesh_path": str, "edited_mesh_path": str, "receipt_path": str,
      "boundary_rel_tolerance": float, "edit_kind": str, "approved_plan_sha256": str,
    },
    "required": ("source_mesh_path", "edited_mesh_path", "receipt_path"),
    "why": "re-imports a Blender-edited mesh and refuses it if the mesh's boundary (its stitch to neighbouring patches) moved beyond a declared tolerance, writing a parity receipt either way.",
  },
  "job.resume": {
    "params": {"job_id": str, "stage": str, "approved_plan_sha256": str},
    "required": ("job_id",),
    "why": "plans (no side effects) and then resumes one interrupted job from where its own unit ledger or stage lineage says it stopped, delegating to jobs.act_resume_stage or the surface workflow; completed units are never re-run, and anything that could fetch stays behind its existing switch.",
  },
  "job.cancel": {
    "params": {"job_id": str},
    "required": ("job_id",),
    "why": "marks one job the command ledger recorded as cancellable as cancelled; it refuses a job that declares itself not cancellable.",
  },
  "import.segment": {
    "params": {"path": str, "attach_volume_source": str, "attested_by": str,
               "attestation_reason": str, "approved_plan_sha256": str},
    "required": ("path",),
    "why": "registers a local tifxyz segment in the user-data store after checking its files, its declared volume identity and its compatibility; it copies nothing, runs nothing, and refuses an unknown identity, a non-tifxyz directory and any path outside the allowed import roots.",
  },
  "provider.candidate.plan": {
    "params": {"provider_id": str, "request": dict, "approved_plan_sha256": str},
    "required": ("provider_id",),
    "why": "builds one hash-bound plan for a registered candidate provider (or refuses naming what blocks it); starts no process, fetches nothing and writes no file.",
  },
  "provider.candidate.invoke": {
    "params": {"provider_id": str, "request": dict, "approved_plan_sha256": str},
    "required": ("provider_id",),
    "why": "records approval of one candidate plan hash and then refuses (EXECUTION_NOT_ADAPTED or NOT_AUTHORIZED, naming the blocker): no candidate provider has an execution adapter ARGUS may run from here, and this door never starts a process.",
  },
  "copy_out_in.execute": {
    "params": {
      "source_tifxyz_path": str, "volume_path": str, "normal_grid_path": str, "direction": str,
      "output_dir": str, "source_binding": dict, "review": dict, "pass1_options": dict,
      "pass2_options": dict, "authorized": bool, "approved_plan_sha256": str,
    },
    "required": ("source_tifxyz_path", "volume_path", "normal_grid_path", "direction",
                 "output_dir", "source_binding", "review"),
    "why": "runs the classical (non-neural) VC3D Copy Out/In round trip on one already-reviewed source wrap, never overwriting it; disabled unless ARGUS_COPY_OUT_IN_EXECUTION_ENABLED=1 and gated by the eligible-target operation gate, an approved plan hash and a data lease.",
  },
  "glyph.annotate": {
    "params": {"target": str, "task_id": str, "answer": str, "alphabet": str, "reviewer_id": str,
               "reviewer_class": str, "confidence": float, "duration_s": float, "notes": str,
               "approved_plan_sha256": str},
    "required": ("target", "task_id", "answer", "alphabet", "reviewer_id", "reviewer_class"),
    "why": "records one named person's letter judgment for an accepted ink region (HUMAN_JUDGMENT, attributed). The reviewer's session is attached server-side; it cannot be supplied.",
  },
  "htr.propose": {
    "params": {"target": str, "task_id": str, "answer": str, "source_id": str,
               "source_version": str, "confidence": float, "notes": str, "run_provider": str,
               "approved_plan_sha256": str},
    "required": ("target", "task_id", "answer", "source_id", "source_version"),
    "why": "records a proposal from an EXTERNAL OCR/HTR source as an AI_AGENT answer that never counts as a person agreeing. No OCR/HTR model is installed; asking to run one refuses PROVIDER_NOT_INSTALLED.",
  },
  "transcription.claim": {
    "params": {"target": str, "claimed_by": str, "claimed_by_class": str, "why": str,
               "approved_plan_sha256": str},
    "required": ("target", "claimed_by", "claimed_by_class"),
    "why": "claims a transcription for the current reading board. The human review is computed from the merged review tally; it accepts no counts and needs an attributed expert validation.",
  },
  "language.write": {
    "params": {"target": str, "language": str, "declared_by": str, "basis": str,
               "approved_plan_sha256": str},
    "required": ("target", "language", "declared_by"),
    "why": "writes a person's declaration of the language of a target's text. A declaration, not a detection.",
  },
  "translation.propose": {
    "params": {"target": str, "source_token_ids": list, "text": str, "alternatives": list,
               "proposed_by": str, "proposed_by_class": str, "supersedes": str,
               "approved_plan_sha256": str},
    "required": ("target", "source_token_ids", "text", "proposed_by", "proposed_by_class"),
    "why": "appends a translation PROPOSAL by a named person, citing accepted token ids. Never a reading of an unread scroll; there is no machine translation.",
  },
  "packet.export": {
    "params": {"target": str, "receipts": list, "approved_plan_sha256": str},
    "required": ("target",),
    "why": "writes a publication/receipt packet (reading board, review records with evidence roles, translation proposals, cited receipts, claim limits, hashes) under the user-data packets root. Never publishes or uploads.",
  },
  "packet.verify": {
    "params": {"target": str, "packet": str},
    "required": ("target", "packet"),
    "why": "reopens an exported packet and re-hashes every file and every cited receipt. Reads only.",
  },
  "science.attach.plan": {
    "params": {"manifest": str, "scroll": str},
    "required": ("manifest", "scroll"),
    "why": "checks an imported science manifest against its import receipt, binds its scroll and exact volume through the eligible-target gate at GEOMETRY_ONLY, and reports every private local file as MOUNTED_VERIFIED, NOT_MOUNTED, HASH_MISMATCH or IDENTITY_MISMATCH. Writes nothing; the manifest is chosen from a fixed list, never a typed path.",
  },
  "science.attach.execute": {
    "params": {"manifest": str, "scroll": str, "approved_plan_sha256": str},
    "required": ("manifest", "scroll"),
    "why": "on approval of that plan's own hash, appends one small attachment record (path, sha256, size, status per item). Copies no large bytes and substitutes no file for one that is not mounted.",
  },
  "user_data.detach": {
    "params": {"confirmation": str, "approved_plan_sha256": str},
    "required": ("confirmation",),
    "why": "moves the active private user-data root into a recoverable same-volume quarantine and disables legacy fallback. It deletes nothing and leaves public scans, caches, models, runtimes, source and historical evidence untouched.",
  },
  "review.blind.answer": {
    "params": {"kind": str, "task_id": str, "value": str, "reviewer_id": str, "reviewer_class": str,
               "images_viewed": bool, "confidence": float, "duration_s": float, "notes": str,
               "approved_plan_sha256": str},
    "required": ("kind", "task_id", "value", "reviewer_id", "reviewer_class", "images_viewed"),
    "why": "records one named person's answer (LIKELY_SIGNAL, LIKELY_STRUCTURE, UNCERTAIN or REFUSE) on one sealed blinded task, only if the layers are mounted and hash-verified and the reviewer attests they looked. The reviewer's session is attached server-side; it cannot be supplied.",
  },
}

SESSION_BOUND_ACTIONS = frozenset({"glyph.annotate", "review.blind.answer"})


def validate(action: str, params: dict) -> dict:
    """Per-action schema."""
    op = OPERATIONS.get(action)
    if op is None:
        raise HTTPException(status_code=400, detail={
          "error": "action not carried by this transport", "action": action,
          "carried": sorted(OPERATIONS),
          "why": "this is an allowlist of explicit operations, not a forwarder. Adding one is "
                 "a code change, which is the point."})
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail={"error": "params must be an object"})
    unknown = sorted(set(params) - set(op["params"]))
    if unknown:
        raise HTTPException(status_code=400, detail={
          "error": "unknown parameter(s)", "unknown": unknown,
          "why": "ignoring an unknown parameter would let a caller believe it took effect"})
    for k in op["required"]:
        if k not in params:
            raise HTTPException(status_code=400, detail={
              "error": "missing required parameter", "missing": k})
    for k, v in params.items():
        expected = op["params"][k]
        if expected is float and isinstance(v, int) and not isinstance(v, bool):
            continue
        if (expected is int and isinstance(v, bool)) or not isinstance(v, expected):
            raise HTTPException(status_code=400, detail={
              "error": "wrong type for %s" % k,
              "expected": expected.__name__})
        if isinstance(v, str) and len(v) > 512:
            raise HTTPException(status_code=400, detail={"error": "parameter too long"})
    return params



def audit(record: dict) -> None:
    """Append one scrubbed record."""
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(scrub(record), sort_keys=True) + "\n")
    except OSError as exc:
        print("bff audit write failed (%s); event %s was not recorded" % (type(exc).__name__, record.get("event")), file=sys.stderr)


def _call_command(path: str, payload=None, method="POST") -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(COMMAND_BASE + path, data=body, method=method)
    req.add_header("Authorization", "Bearer " + _token())
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:2000]
        try:
            detail = json.loads(detail)
        except ValueError:
            pass
        raise HTTPException(status_code=e.code, detail=scrub(
          {"error": "command service refused", "detail": detail}))
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=502, detail={
          "error": "command service unreachable", "why": "%s" % e})



@app.get("/ui/health")
def health():
    command_ok = False
    command_error = None
    try:
        with urllib.request.urlopen(COMMAND_BASE + "/health", timeout=2) as response:
            command_ok = response.status == 200 and bool(json.loads(response.read().decode()).get("ok"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        command_error = type(exc).__name__
    def secret_ready(path: pathlib.Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size >= 32
        except OSError:
            return False

    token_ready = secret_ready(TOKEN_PATH)
    operator_key_ready = secret_ready(UI_ACCESS_KEY_PATH)
    ready = command_ok and token_ready and operator_key_ready
    return {"ok": ready, "service": "argus-ui-command-transport",
            "command_reachable": command_ok,
            "command_error": command_error,
            "command_token_ready": token_ready,
            "operator_key_ready": operator_key_ready,
            "carries": sorted(OPERATIONS),
            "token_disclosed": False,
            "note": ("governed controls are ready" if ready else
                     "governed controls are unavailable; start the command service and UI transport with shared credential files"),
            "secret_values_returned": False}


@app.post("/ui/session")
def open_session(response: Response, origin: str | None = Header(default=None),
                 x_argus_access_key: str | None = Header(default=None)):
    """A deliberate operator act requiring the separate local access key."""
    o = require_origin(origin)
    require_ui_access_key(x_argus_access_key)
    s = _new_session(o)
    response.set_cookie("argus_sid", s["sid"], httponly=True, samesite="strict",
                        secure=False, max_age=SESSION_TTL_S, path="/ui")
    return {"csrf": s["csrf"], "expires_in_s": SESSION_TTL_S,
            "why_the_csrf_is_returned_and_the_sid_is_not": "the sid is the credential and is "
            "HttpOnly so page script cannot read it. The csrf value is meant to be echoed in "
            "a header, which is what a cross-site form post cannot do."}


@app.get("/ui/operations")
def operations():
    """What this transport will carry, and why each one is safe to expose."""
    return {"operations": {k: {"params": {p: t.__name__ for p, t in v["params"].items()},
                               "required": list(v["required"]), "why": v["why"]}
                           for k, v in OPERATIONS.items()},
            "not_a_forwarder": "there is no route that accepts an arbitrary action id, command "
                               "string, script path, module or URL."}


@app.get("/ui/nl/intents")
def structured_intents():
    """Expose the command service's closed grammar without exposing its credential."""
    return scrub(_call_command("/nl/intents", method="GET"))




@app.post("/ui/annotations")
async def annotate(request: Request,
                   argus_sid: str | None = Cookie(default=None),
                   x_argus_csrf: str | None = Header(default=None),
                   origin: str | None = Header(default=None)):
    """Append one operator note."""
    from argus.core import annotations as A

    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body must be JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})

    unknown = sorted(set(body) - {"target", "text", "supersedes", "attachment_ids"})
    if unknown:
        raise HTTPException(status_code=400, detail={
          "error": "unknown field(s)", "unknown": unknown})

    attachment_ids = body.get("attachment_ids")
    if attachment_ids is not None and (
        not isinstance(attachment_ids, list) or not all(isinstance(i, str) for i in attachment_ids)
    ):
        raise HTTPException(status_code=400, detail={"error": "attachment_ids must be a list of strings"})

    try:
        rec = A.add(body.get("target", ""), body.get("text", ""),
                    supersedes=body.get("supersedes"), attachment_ids=attachment_ids)
    except A.AnnotationError as exc:
        raise HTTPException(status_code=400, detail={"error": "refused", "why": str(exc)})

    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": "annotation.add", "target": rec["target"], "id": rec["id"],
           "chars": len(rec["text"]), "attachments": len(rec.get("attachment_ids") or [])})
    return {"ok": True, "note": rec,
            "not_evidence": "an operator note. It is stored outside the repository and outside "
                            "every artifact root, and no derivation reads it."}


def _bind_reviewer(session: dict, reviewer_id: str) -> str:
    """Bind this browser session to the reviewer name it first answered as, and return the session's binding token (a hash; the sid itself is the credential and is never stored)."""
    from argus.core.review_lab import norm_id
    bound = session.get("reviewer_id")
    if bound is not None and norm_id(bound) != norm_id(reviewer_id):
        raise HTTPException(status_code=409, detail={
          "error": "refused",
          "why": "this session is already reviewing as %r. One person cannot become two "
                 "independent reviewers by changing their name; a second reviewer opens their "
                 "own session." % bound})
    session["reviewer_id"] = reviewer_id
    return hashlib.sha256(session["sid"].encode("utf-8")).hexdigest()[:16]


async def _review_body(request: Request, allowed: set) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body must be JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail={"error": "unknown field(s)", "unknown": unknown})
    return body


def _review_target(body: dict) -> tuple:
    from argus.core import paths as PATHS
    from argus.core import review_store as RS
    target = body.get("target")
    task_id = body.get("task_id")
    if not isinstance(target, str) or not target:
        raise HTTPException(status_code=400, detail={"error": "target is required"})
    if not isinstance(task_id, str) or not task_id:
        raise HTTPException(status_code=400, detail={"error": "task_id is required"})
    target_dir = PATHS.resolve_ui_target_dir(target)
    if target_dir is None:
        raise HTTPException(status_code=404, detail={"error": "unknown target", "target": target})
    tasks_payload = RS.load_raw_payload(target_dir)
    if tasks_payload is None:
        raise HTTPException(status_code=404, detail={"error": "no review tasks for this target"})
    return target, task_id, target_dir, tasks_payload


def _reviewer_identity(body: dict) -> tuple:
    """(name, class, named)."""
    from argus.core.review_lab import HUMAN_CLASSES
    name = body.get("reviewer_id")
    cls = body.get("reviewer_class")
    if name is None and cls is None:
        return "operator", "OPERATOR", False
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail={"error": "reviewer_id must be a name"})
    cls = cls or "COMMUNITY"
    if cls not in HUMAN_CLASSES:
        raise HTTPException(status_code=400, detail={
          "error": "refused",
          "why": "reviewer_class must be a human class (%s). A model's answer is recorded through "
                 "htr.propose with its source identity, never as a person's." % ", ".join(sorted(HUMAN_CLASSES))})
    return name, cls, True


@app.post("/ui/review/answer")
async def review_answer(request: Request,
                        argus_sid: str | None = Cookie(default=None),
                        x_argus_csrf: str | None = Header(default=None),
                        origin: str | None = Header(default=None)):
    """Record one reviewer answer to one review-lab task."""
    from argus.core.review_lab import ReviewRefusal
    from argus.core.review_store import ReviewStoreError, record_answer

    o = require_origin(origin)
    sess = require_session(argus_sid, x_argus_csrf, o)
    body = await _review_body(request, {"target", "task_id", "value", "confidence", "duration_s",
                                        "notes", "reviewer_id", "reviewer_class"})
    value = body.get("value")
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=400, detail={"error": "value is required"})
    confidence = body.get("confidence")
    duration_s = body.get("duration_s")
    if not isinstance(confidence, (int, float)):
        raise HTTPException(status_code=400, detail={"error": "confidence (0..1) is required"})
    if not isinstance(duration_s, (int, float)):
        raise HTTPException(status_code=400, detail={"error": "duration_s is required"})
    notes = body.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise HTTPException(status_code=400, detail={"error": "notes must be a string"})
    target, task_id, target_dir, tasks_payload = _review_target(body)
    name, cls, _named = _reviewer_identity(body)

    try:
        binding = _bind_reviewer(sess, name)
        rec = record_answer(target_dir=target_dir, tasks_payload=tasks_payload, task_id=task_id,
                            value=value, confidence=float(confidence), duration_s=float(duration_s),
                            notes=notes, reviewer_id=name, reviewer_class=cls,
                            session_binding=binding)
    except ReviewStoreError as exc:
        raise HTTPException(status_code=404, detail={"error": "refused", "why": str(exc)})
    except ReviewRefusal as exc:
        raise HTTPException(status_code=400, detail={"error": "refused", "why": str(exc)})

    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": "review.answer", "target": target, "task_id": task_id, "value": value,
           "reviewer_id": name, "reviewer_class": cls})
    return {"ok": True, "task": rec}


@app.post("/ui/review/validate")
async def review_validate(request: Request,
                          argus_sid: str | None = Cookie(default=None),
                          x_argus_csrf: str | None = Header(default=None),
                          origin: str | None = Header(default=None)):
    """Record an attributed expert validation (the adjudicator) of a task that reached consensus."""
    from argus.core.review_lab import ReviewRefusal
    from argus.core.review_store import ReviewStoreError, record_validation

    o = require_origin(origin)
    sess = require_session(argus_sid, x_argus_csrf, o)
    body = await _review_body(request, {"target", "task_id", "value", "notes", "reviewer_id",
                                        "reviewer_class"})
    value = body.get("value")
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=400, detail={"error": "value is required"})
    if body.get("reviewer_id") is None or body.get("reviewer_class") is None:
        raise HTTPException(status_code=400, detail={
          "error": "a validation is attributed: reviewer_id and reviewer_class are required"})
    notes = body.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise HTTPException(status_code=400, detail={"error": "notes must be a string"})
    target, task_id, target_dir, tasks_payload = _review_target(body)
    name, cls, _named = _reviewer_identity(body)

    try:
        binding = _bind_reviewer(sess, name)
        rec = record_validation(target_dir=target_dir, tasks_payload=tasks_payload,
                                task_id=task_id, value=value, reviewer_id=name,
                                reviewer_class=cls, notes=notes, session_binding=binding)
    except ReviewStoreError as exc:
        raise HTTPException(status_code=404, detail={"error": "refused", "why": str(exc)})
    except ReviewRefusal as exc:
        raise HTTPException(status_code=400, detail={"error": "refused", "why": str(exc)})

    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": "review.validate", "target": target, "task_id": task_id, "value": value,
           "reviewer_id": name, "reviewer_class": cls})
    return {"ok": True, "task": rec}


def _resolve_correction_target(mesh_path: str):
    """The shared, single-definition path resolution + containment check (see argus.core.paths.serve_roots/resolve_served_path/within_root) applied to one mesh directory a correction request names."""
    from argus.core import paths as PATHS
    roots = PATHS.serve_roots()
    p = PATHS.resolve_served_path(mesh_path, roots)
    if not any(PATHS.within_root(p, r) for r in roots):
        raise HTTPException(status_code=403, detail={"error": "outside the declared roots"})
    return p


@app.post("/ui/corrections/record")
async def corrections_record(request: Request,
                             argus_sid: str | None = Cookie(default=None),
                             x_argus_csrf: str | None = Header(default=None),
                             origin: str | None = Header(default=None)):
    """Record one surface correction."""
    from argus.core.surface_corrections import ACTIONS, CorrectionRefusal
    from argus.core.correction_store import CorrectionStoreError, record_correction
    from argus.core import feed as F

    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body must be JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})

    allowed = {"mesh_path", "proposal_id", "proposal_sha256", "action", "payload",
              "transform", "view_hash"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail={"error": "unknown field(s)", "unknown": unknown})
    missing = sorted(allowed - {"view_hash"} - set(body))
    if missing:
        raise HTTPException(status_code=400, detail={"error": "missing field(s)", "missing": missing})
    if body["action"] not in ACTIONS:
        raise HTTPException(status_code=400, detail={
          "error": "refused", "why": "unknown action %r; the set is closed (%s)"
                                      % (body["action"], ", ".join(ACTIONS))})

    mesh_dir = _resolve_correction_target(body["mesh_path"])
    if F.sealed_by(mesh_dir):
        raise HTTPException(status_code=423, detail={"error": "under an active blinded experiment"})

    try:
        constraint = record_correction(
            mesh_dir, proposal_id=body["proposal_id"], proposal_sha256=body["proposal_sha256"],
            action=body["action"], payload=body["payload"], transform=body["transform"],
            view_hash=body.get("view_hash", ""),
        )
    except CorrectionStoreError as exc:
        raise HTTPException(status_code=409, detail={"error": "refused", "why": str(exc)})
    except CorrectionRefusal as exc:
        raise HTTPException(status_code=400, detail={"error": "refused", "why": str(exc)})

    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "correction.record",
           "mesh_path": body["mesh_path"], "proposal_id": body["proposal_id"], "action": body["action"]})
    return {"ok": True, "constraint": constraint}


@app.post("/ui/corrections/undo")
async def corrections_undo(request: Request,
                           argus_sid: str | None = Cookie(default=None),
                           x_argus_csrf: str | None = Header(default=None),
                           origin: str | None = Header(default=None)):
    """Undo one correction -- a COMPENSATING EVENT, never a deletion; see argus.core.correction_store's own header for why."""
    from argus.core.correction_store import CorrectionStoreError, record_undo
    from argus.core import feed as F

    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body must be JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})
    unknown = sorted(set(body) - {"mesh_path", "event_id"})
    if unknown:
        raise HTTPException(status_code=400, detail={"error": "unknown field(s)", "unknown": unknown})
    if not body.get("mesh_path") or not body.get("event_id"):
        raise HTTPException(status_code=400, detail={"error": "mesh_path and event_id are required"})

    mesh_dir = _resolve_correction_target(body["mesh_path"])
    if F.sealed_by(mesh_dir):
        raise HTTPException(status_code=423, detail={"error": "under an active blinded experiment"})

    try:
        event = record_undo(mesh_dir, body["event_id"])
    except CorrectionStoreError as exc:
        raise HTTPException(status_code=404, detail={"error": "refused", "why": str(exc)})

    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "correction.undo",
           "mesh_path": body["mesh_path"], "event_id": body["event_id"]})
    return {"ok": True, "undo": event}


@app.post("/ui/corrections/decision")
async def corrections_decision(request: Request,
                               argus_sid: str | None = Cookie(default=None),
                               x_argus_csrf: str | None = Header(default=None),
                               origin: str | None = Header(default=None)):
    """Accept or reject a whole proposed surface."""
    from argus.core.correction_store import CorrectionStoreError, record_decision
    from argus.core import feed as F

    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body must be JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})
    unknown = sorted(set(body) - {"mesh_path", "proposal_id", "decision", "why"})
    if unknown:
        raise HTTPException(status_code=400, detail={"error": "unknown field(s)", "unknown": unknown})
    if not body.get("mesh_path") or not body.get("proposal_id") or not body.get("decision"):
        raise HTTPException(status_code=400, detail={
          "error": "mesh_path, proposal_id and decision are required"})

    mesh_dir = _resolve_correction_target(body["mesh_path"])
    if F.sealed_by(mesh_dir):
        raise HTTPException(status_code=423, detail={"error": "under an active blinded experiment"})

    try:
        event = record_decision(mesh_dir, body["proposal_id"], body["decision"],
                                why=body.get("why"))
    except CorrectionStoreError as exc:
        raise HTTPException(status_code=400, detail={"error": "refused", "why": str(exc)})

    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "correction.decision",
           "mesh_path": body["mesh_path"], "proposal_id": body["proposal_id"],
           "decision": body["decision"]})
    return {"ok": True, "decision": event}


@app.post("/ui/corrections/promote")
async def corrections_promote(request: Request,
                              argus_sid: str | None = Cookie(default=None),
                              x_argus_csrf: str | None = Header(default=None),
                              origin: str | None = Header(default=None)):
    """Advance one proposal one step on the promotion ladder."""
    from argus.core.surface_corrections import CorrectionRefusal
    from argus.core.correction_store import record_promotion
    from argus.core import feed as F

    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body must be JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})
    unknown = sorted(set(body) - {"mesh_path", "proposal_id", "to_state"})
    if unknown:
        raise HTTPException(status_code=400, detail={"error": "unknown field(s)", "unknown": unknown})
    if not body.get("mesh_path") or not body.get("proposal_id") or not body.get("to_state"):
        raise HTTPException(status_code=400, detail={
          "error": "mesh_path, proposal_id and to_state are required"})

    mesh_dir = _resolve_correction_target(body["mesh_path"])
    if F.sealed_by(mesh_dir):
        raise HTTPException(status_code=423, detail={"error": "under an active blinded experiment"})

    try:
        event = record_promotion(mesh_dir, body["proposal_id"], body["to_state"])
    except CorrectionRefusal as exc:
        raise HTTPException(status_code=400, detail={"error": "refused", "why": str(exc)})

    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": "correction.promote",
           "mesh_path": body["mesh_path"], "proposal_id": body["proposal_id"],
           "to_state": event["to_state"]})
    return {"ok": True, "promotion": event}


@app.post("/ui/annotations/attachments")
async def annotation_attachment_upload(request: Request,
                                       argus_sid: str | None = Cookie(default=None),
                                       x_argus_csrf: str | None = Header(default=None),
                                       origin: str | None = Header(default=None)):
    """Store one photo/clip for the notebook."""
    from argus.core import annotations as A

    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)

    raw = await request.body()
    if len(raw) > MAX_ATTACHMENT_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body must be JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})

    unknown = sorted(set(body) - {"filename", "mime", "data_base64"})
    if unknown:
        raise HTTPException(status_code=400, detail={
          "error": "unknown field(s)", "unknown": unknown})

    import base64
    try:
        content = base64.b64decode(body.get("data_base64", ""), validate=True)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail={"error": "data_base64 is not valid base64"})

    try:
        rec = A.save_attachment(content, body.get("mime", ""), body.get("filename", ""))
    except A.AnnotationError as exc:
        raise HTTPException(status_code=400, detail={"error": "refused", "why": str(exc)})

    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": "annotation.attachment", "id": rec["id"], "mime": rec["mime"],
           "size": rec["size"]})
    return {"ok": True, "attachment": rec,
            "not_evidence": "an operator's own upload. It is stored outside the repository and "
                            "outside every artifact root, and no derivation reads it."}


@app.post("/ui/annotations/{note_id}/hide")
async def hide_annotation(note_id: str,
                          argus_sid: str | None = Cookie(default=None),
                          x_argus_csrf: str | None = Header(default=None),
                          origin: str | None = Header(default=None)):
    """Hide, deliberately not delete -- what we thought last week is the point of a diary."""
    from argus.core import annotations as A

    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)
    try:
        rec = A.hide(note_id)
    except A.AnnotationError as exc:
        raise HTTPException(status_code=400, detail={"error": "refused", "why": str(exc)})
    audit({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": "annotation.hide", "id": note_id})
    return {"ok": True, "note": rec,
            "why_not_deleted": "the record is kept and marked hidden. A diary that erases what "
                               "it used to say cannot answer the question it exists for."}


@app.post("/ui/act/{action_id}")
async def act(action_id: str, request: Request,
              argus_sid: str | None = Cookie(default=None),
              x_argus_csrf: str | None = Header(default=None),
              x_idempotency_key: str | None = Header(default=None),
              origin: str | None = Header(default=None)):
    o = require_origin(origin)
    sess = require_session(argus_sid, x_argus_csrf, o)

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body is not JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})

    if not x_idempotency_key or len(x_idempotency_key) < 8:
        raise HTTPException(status_code=400, detail={
          "error": "idempotency key required",
          "why": "a double click is the normal case, not an attack. Two identical submissions "
                 "must produce one job."})

    params = validate(action_id, body.get("params") or {})
    request_hash = hashlib.sha256(
      json.dumps({"a": action_id, "p": params}, sort_keys=True).encode()).hexdigest()

    sent = params
    if action_id in SESSION_BOUND_ACTIONS:
        sent = dict(params, reviewer_session=_bind_reviewer(sess, params.get("reviewer_id", "")))

    spec = {"action": action_id, "actor": "human:ui",
            "request_id": request_hash[:32],
            "idempotency_key": x_idempotency_key,
            "params": sent, "dry_run": False}

    plan = _call_command("/plan", spec)
    plan_hash = hashlib.sha256(
      json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()

    started = time.time()
    result = _call_command("/submit", spec)
    out = scrub(result)

    audit({
      "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "actor": "human:ui", "origin": o, "action": action_id,
      "request_hash": request_hash, "plan_hash": plan_hash,
      "idempotency_key": x_idempotency_key,
      "job_id": (out or {}).get("job_id") or (out or {}).get("id"),
      "elapsed_s": round(time.time() - started, 3),
      "terminal_outcome": (out or {}).get("state") or (out or {}).get("status"),
      "receipt": (out or {}).get("receipt"),
    })
    return {"plan_hash": plan_hash, "request_hash": request_hash, "result": out}


@app.post("/ui/plan/{action_id}")
async def plan_action(action_id: str, request: Request,
                      argus_sid: str | None = Cookie(default=None),
                      x_argus_csrf: str | None = Header(default=None),
                      x_idempotency_key: str | None = Header(default=None),
                      origin: str | None = Header(default=None)):
    """Return the exact command-service plan without submitting it."""
    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body is not JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})
    if not x_idempotency_key or len(x_idempotency_key) < 8:
        raise HTTPException(status_code=400, detail={"error": "idempotency key required"})
    params = validate(action_id, body.get("params") or {})
    request_hash = hashlib.sha256(
      json.dumps({"a": action_id, "p": params}, sort_keys=True).encode()).hexdigest()
    sent = params
    if action_id in SESSION_BOUND_ACTIONS:
        sent = dict(params, reviewer_session=hashlib.sha256(
            _SESSIONS[argus_sid]["sid"].encode("utf-8")).hexdigest()[:16])
    spec = {"action": action_id, "actor": "human:ui", "request_id": request_hash[:32],
            "idempotency_key": x_idempotency_key, "params": sent, "dry_run": True}
    plan = _call_command("/plan", spec)
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()
    return {"plan_hash": plan_hash, "request_hash": request_hash, "plan": scrub(plan),
            "read_only": True, "execution": "submit only after explicit operator approval"}


async def _structured_intent_body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"error": "request too large"})
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "body is not JSON"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "body must be an object"})
    unknown = sorted(set(body) - {"text", "confirm_sha256"})
    if unknown:
        raise HTTPException(status_code=400, detail={
          "error": "unknown field(s)", "unknown": unknown,
          "why": "the structured-intent door accepts a sentence and its displayed plan hash only"})
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail={"error": "text must be a non-empty string"})
    if len(text) > 512:
        raise HTTPException(status_code=400, detail={"error": "text is too long"})
    digest = body.get("confirm_sha256")
    if digest is not None and (not isinstance(digest, str) or len(digest) != 64):
        raise HTTPException(status_code=400, detail={
          "error": "confirm_sha256 must be a 64-character digest"})
    return {"text": text.strip(), "confirm_sha256": digest}


@app.post("/ui/nl/preview")
async def structured_intent_preview(
    request: Request,
    argus_sid: str | None = Cookie(default=None),
    x_argus_csrf: str | None = Header(default=None),
    x_idempotency_key: str | None = Header(default=None),
    origin: str | None = Header(default=None),
):
    """Resolve one closed-vocabulary sentence and return its real governed plan."""
    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)
    if not x_idempotency_key or len(x_idempotency_key) < 8:
        raise HTTPException(status_code=400, detail={"error": "idempotency key required"})
    body = await _structured_intent_body(request)
    payload = {"text": body["text"], "actor": "human:ui-intent",
               "idempotency_key": x_idempotency_key}
    out = scrub(_call_command("/nl/preview", payload))
    return out


@app.post("/ui/nl/confirm")
async def structured_intent_confirm(
    request: Request,
    argus_sid: str | None = Cookie(default=None),
    x_argus_csrf: str | None = Header(default=None),
    x_idempotency_key: str | None = Header(default=None),
    origin: str | None = Header(default=None),
):
    """Confirm the freshly recomputed structured-intent plan through the one command door."""
    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)
    if not x_idempotency_key or len(x_idempotency_key) < 8:
        raise HTTPException(status_code=400, detail={"error": "idempotency key required"})
    body = await _structured_intent_body(request)
    payload = {"text": body["text"], "actor": "human:ui-intent",
               "idempotency_key": x_idempotency_key,
               "confirm_sha256": body.get("confirm_sha256")}
    started = time.time()
    out = scrub(_call_command("/nl/confirm", payload))
    audit({
      "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "actor": "human:ui-intent", "origin": o, "action": out.get("action"),
      "intent": out.get("intent"), "text_sha256": hashlib.sha256(
          body["text"].encode("utf-8")).hexdigest(),
      "idempotency_key": x_idempotency_key,
      "job_id": out.get("job_id"), "terminal_outcome": out.get("status"),
      "elapsed_s": round(time.time() - started, 3),
    })
    return out


@app.get("/ui/job/{job_id}")
def job(job_id: str, argus_sid: str | None = Cookie(default=None),
        x_argus_csrf: str | None = Header(default=None),
        origin: str | None = Header(default=None)):
    o = require_origin(origin)
    require_session(argus_sid, x_argus_csrf, o)
    if not job_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail={"error": "malformed job id"})
    return scrub(_call_command("/jobs/" + job_id, method="GET"))
