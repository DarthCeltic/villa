"""The ARGUS command service."""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from argus.core import action_registry as R
from argus.core import process_hardening as _process_hardening
_process_hardening.apply()
from argus.core import actions as A
from argus.core import intent_resolver as NL
from argus.core import workspace as W
from argus.core.constant_time import same_secret
from argus.service.request_guard import BodyLimit

TOKEN_PATH = Path(os.environ.get("ARGUS_COMMAND_TOKEN_FILE",
                                 str(A.STATE / "command_token")))
UI_ACCESS_KEY_PATH = Path(os.environ.get(
    "ARGUS_UI_ACCESS_KEY_FILE", str(A.STATE / "ui_access_key")))

app = FastAPI(title="ARGUS command service", version="0.1.0",
              description=(__doc__ or "").strip().split("\n\n")[0])


MAX_REQUEST_BYTES = 256 * 1024


app.add_middleware(BodyLimit, max_bytes=MAX_REQUEST_BYTES)


@app.on_event("startup")
def _reconcile_jobs_on_start() -> None:
    """Jobs left RUNNING by a process that is gone are marked INTERRUPTED, so a restart does not leave them running forever."""
    try:
        A.reconcile_jobs()
    except Exception as exc:
        import sys
        print("job reconcile failed (%s)" % type(exc).__name__, file=sys.stderr)


SECRET_MIN_CHARS = 32
SECRET_INIT_WAIT_S = 90.0
SECRET_MARKER_STALE_S = 60.0
_RESTRICTED: set = set()


def _file_identity(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_ino, st.st_mtime_ns)
    except OSError:
        return (str(path), 0, 0)


def _read_secret(path: Path) -> str | None:
    """The stored secret, or None when the file is absent, empty, whitespace-only, not text, or too short to be a generated secret."""
    text = None
    for attempt in range(20):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except (FileNotFoundError, UnicodeDecodeError):
            return None
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.02)
    text = text.strip()
    return text if len(text) >= SECRET_MIN_CHARS else None


def _system_tool(name: str) -> str:
    """The Windows tool by absolute path, under the directory the OS itself reports (not one an environment variable names), so nothing planted on PATH, in the current directory or through a launcher's..."""
    root = None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buf, 260):
            root = buf.value
    except Exception:
        pass
    root = root or os.environ.get("SystemRoot") or "C:" + chr(92) + "Windows"
    return os.path.join(root, "System32", name)


def _current_principal() -> str | None:
    """The account this process actually runs as, as an icacls `*SID` argument, from the process token (`whoami`), not from environment variables that a launcher can set to anything."""
    import subprocess
    try:
        r = subprocess.run([_system_tool("whoami.exe"), "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=20)
        text = (r.stdout or b"").decode("utf-8", errors="replace")
        if r.returncode == 0:
            sid = text.strip().rsplit(",", 1)[-1].strip().strip('"')
            if sid.startswith("S-1-"):
                return "*" + sid
    except (OSError, subprocess.SubprocessError):
        pass
    return None


ACL_RETRY_S = 300.0
_ACL_FAILED_AT: dict = {}


def _restrict_to_owner(path: Path) -> bool:
    """On Windows a POSIX mode does nothing, so the file inherits whatever the parent directory grants."""
    if os.name != "nt":
        return True
    import subprocess
    import sys
    last = _ACL_FAILED_AT.get(str(path))
    if last is not None and time.monotonic() - last < ACL_RETRY_S:
        return False
    _ACL_FAILED_AT[str(path)] = time.monotonic()
    principal = _current_principal()
    if principal is None:
        print("could not restrict %s: the process identity could not be read" % path.name, file=sys.stderr)
        return False
    try:
        r = subprocess.run([_system_tool("icacls.exe"), str(path), "/inheritance:r", "/grant:r", "%s:F" % principal,
                            "/remove:g", "*S-1-1-0", "*S-1-5-11", "*S-1-5-32-545"], capture_output=True, timeout=20)
        if r.returncode != 0:
            print("could not restrict %s to its owner (icacls exit %d)" % (path.name, r.returncode), file=sys.stderr)
            return False
        _ACL_FAILED_AT.pop(str(path), None)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        print("could not restrict %s to its owner (%s)" % (path.name, type(exc).__name__), file=sys.stderr)
        return False


def _ensure_secret(path: Path) -> str:
    """Read or create one persistent local secret without logging its value."""
    value = _read_secret(path)
    if value:
        ident = _file_identity(path)
        if ident not in _RESTRICTED:
            if _restrict_to_owner(path):
                _RESTRICTED.add(ident)
        return value
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = path.with_name(path.name + ".init")
    give_up = time.monotonic() + SECRET_INIT_WAIT_S
    while True:
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = _read_secret(path)
            if value:
                return value
            if time.monotonic() > give_up:
                raise RuntimeError("secret initialisation is stuck: %s exists and no secret appeared within %d s; remove it if no other service is starting"
                                   % (marker.name, SECRET_INIT_WAIT_S))
            try:
                age = time.time() - marker.stat().st_mtime
                if age > SECRET_MARKER_STALE_S or age < -60:
                    marker.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            time.sleep(0.02)
            continue
        os.close(fd)
        tmp = path.with_name("%s.%d.%s.tmp" % (path.name, os.getpid(), secrets.token_hex(4)))
        try:
            value = _read_secret(path)
            if value:
                return value
            value = secrets.token_urlsafe(32)
            tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(tfd, "w", encoding="utf-8") as fh:
                restricted = _restrict_to_owner(tmp)
                fh.write(value)
                fh.flush()
                os.fsync(fh.fileno())
            for attempt in range(50):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 49:
                        raise
                    time.sleep(0.02)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            if restricted:
                _RESTRICTED.add(_file_identity(path))
            return value
        finally:
            tmp.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)


def _token() -> str:
    """Read, or create on first start."""
    return _ensure_secret(TOKEN_PATH)


def _ui_access_key() -> str:
    """Read, or create the separate operator key used by the browser transport."""
    return _ensure_secret(UI_ACCESS_KEY_PATH)


def require_token(authorization: str = Header(default="")) -> str:
    """Constant-time compare."""
    want = _token()
    got = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    if not same_secret(got, want):
        raise HTTPException(status_code=401, detail={
            "error": "unauthorised",
            "how": ("send Authorization: Bearer <token>. The token is generated on first "
                    "start and lives in a file outside the repository; this service will "
                    "not tell you its value")})
    return "ok"


def _refused(exc: A.Refused, code: int = 409) -> JSONResponse:
    return JSONResponse(status_code=code, content=exc.as_dict())


@app.get("/health")
def health():
    """The one unauthenticated route, and it reveals nothing about state."""
    return {"service": "argus-command", "ok": True,
            "auth": "bearer token required on every other route",
            "token_value_ever_returned": False}


@app.get("/registry", dependencies=[Depends(require_token)])
def registry():
    """What may be asked for, and what each would do."""
    out = []
    for name, (planner, _) in sorted(R.REGISTRY.items()):
        try:
            p = planner({})
        except Exception as exc:
            p = {"plan_error": str(exc)[:200]}
        out.append({"action": name, "plan_with_empty_params": p})
    return {"actions": out, "n": len(out),
            "pipelines": R.PIPELINES,
            "note": ("every action resolves to a Python callable in the registry. No action "
                     "accepts a command, a script path, or a module to import")}


@app.post("/plan", dependencies=[Depends(require_token)])
def plan(spec: dict):
    """What would happen, and what it would cost."""
    try:
        return R.plan(spec)
    except A.Refused as exc:
        return _refused(exc, 400)


@app.post("/submit", dependencies=[Depends(require_token)])
def submit(spec: dict):
    """The one door."""
    try:
        return R.submit(spec)
    except A.Refused as exc:
        return _refused(exc)


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def job(job_id: str):
    j = A.read_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail={"error": "no such job"})
    return j


@app.get("/jobs", dependencies=[Depends(require_token)])
def jobs(limit: int = 40):
    if not A.JOBS.is_dir():
        return {"jobs": [], "n": 0}
    ds = sorted(A.JOBS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for d in ds:
        j = A.read_job(d.name)
        if j:
            out.append({k: j.get(k) for k in
                        ("job_id", "action", "actor", "state", "started_utc",
                         "finished_utc", "cancellable", "resumable")})
    return {"jobs": out, "n": len(out)}


@app.get("/leases", dependencies=[Depends(require_token)])
def leases():
    held = {k: A.read_lease(k) for k in A.LEASE_KINDS}
    return {"leases": held,
            "external_gpu_owner": A.observe_external_gpu_owner(),
            "note": ("a science run launched from a shell holds no lease and still owns the "
                     "card. Both are reported, and a GPU action refuses on either")}


@app.get("/audit", dependencies=[Depends(require_token)])
def audit(limit: int = 50):
    chain = A.verify_audit_chain()
    tail = []
    if A.AUDIT.is_file():
        lines = A.AUDIT.read_text(encoding="utf-8").strip().splitlines()[-limit:]
        for ln in lines:
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            rec.pop("params", None)
            tail.append(rec)
    return {"chain": chain, "recent": tail,
            "append_only": True,
            "why_hash_chained": "a log that can be edited without trace is a diary"}



@app.get("/nl/intents", dependencies=[Depends(require_token)])
def nl_intents():
    """The whole closed vocabulary."""
    intents = NL.describe_intents()
    return {"intents": intents, "n": len(intents),
            "note": ("a fixed, closed set of typed request shapes -- not a free-text NLP "
                     "layer, not a chatbot. An unmapped or ambiguous request is refused, "
                     "never guessed at. Every intent here resolves to one of the SAME "
                     "actions listed at GET /registry")}


@app.post("/nl/preview", dependencies=[Depends(require_token)])
def nl_preview(body: dict):
    """Resolve a structured request to the same typed plan action_registry.plan() would produce for the equivalent ActionSpec -- cost, leases, may_refuse, scientific_boundary."""
    body = body or {}
    out = NL.preview(body.get("text", ""), actor=body.get("actor", "human:nl"),
                     request_id=body.get("request_id"),
                     idempotency_key=body.get("idempotency_key"))
    if out.get("status") == "REFUSED":
        return JSONResponse(status_code=400, content=out)
    return out


@app.post("/nl/confirm", dependencies=[Depends(require_token)])
def nl_confirm(body: dict):
    """Execute a previously-previewed structured request."""
    body = body or {}
    key = body.get("idempotency_key")
    if not key:
        raise HTTPException(status_code=400, detail={
            "error": "idempotency_key is required",
            "how": "reuse the idempotency_key returned by /nl/preview"})
    out = NL.confirm_and_submit(body.get("text", ""), actor=body.get("actor", "human:nl"),
                                idempotency_key=key, confirm_sha256=body.get("confirm_sha256"),
                                request_id=body.get("request_id"))
    if out.get("status") == "REFUSED":
        return JSONResponse(status_code=409, content=out)
    return out


@app.get("/workspaces", dependencies=[Depends(require_token)])
def workspaces():
    cur = A.STATE / "current_workspace.json"
    current = None
    if cur.is_file():
        try:
            current = json.loads(cur.read_text(encoding="utf-8"))
        except ValueError:
            current = None
    return {"workspaces": W.list_workspaces(), "current": current}


RPC_METHODS = ("registry", "plan", "submit", "job", "jobs", "leases", "audit", "workspaces")


@app.post("/rpc", dependencies=[Depends(require_token)])
def rpc(body: dict):
    """JSON-RPC 2.0."""
    rid = (body or {}).get("id")
    method = (body or {}).get("method")
    params = (body or {}).get("params") or {}

    def err(code: int, message: str, data=None):
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": code, "message": message, "data": data}}

    if (body or {}).get("jsonrpc") != "2.0":
        return err(-32600, "jsonrpc must be \"2.0\"")
    if method not in RPC_METHODS:
        return err(-32601, "unknown method %r" % method,
                   {"methods": list(RPC_METHODS)})
    try:
        if method == "registry":
            result = registry()
        elif method == "plan":
            result = R.plan(params)
        elif method == "submit":
            result = R.submit(params)
        elif method == "job":
            result = A.read_job(params.get("job_id", "")) or {}
        elif method == "jobs":
            result = jobs(int(params.get("limit", 40)))
        elif method == "leases":
            result = leases()
        elif method == "audit":
            result = audit(int(params.get("limit", 50)))
        else:
            result = workspaces()
    except A.Refused as exc:
        return err(-32000, exc.why, exc.as_dict())
    except Exception as exc:
        return err(-32603, "internal error", {"detail": str(exc)[:200]})
    return {"jsonrpc": "2.0", "id": rid, "result": result}


@app.get("/secrets/status", dependencies=[Depends(require_token)])
def secrets_status():
    """Which credentials are configured, and NEVER what they are."""
    providers = ("GITHUB_TOKEN", "HF_TOKEN", "AWS_ACCESS_KEY_ID", "OPENROUTER_API_KEY")
    out = []
    for name in providers:
        v = os.environ.get(name)
        out.append({"provider": name, "configured": bool(v),
                    "length": (len(v) if v else 0),
                    "value": None,
                    "source": "process environment" if v else None})
    return {"providers": out,
            "value_ever_returned": False,
            "storage_recommendation": ("OS keyring or credential manager. Only .env.example "
                                       "belongs in Git")}
