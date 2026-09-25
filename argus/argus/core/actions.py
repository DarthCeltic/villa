"""The ARGUS command service: one line that the browser, the CLI and every agent share."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import calendar
import contextlib
import dataclasses
import hashlib
import json
import math
import os
import re
import time
import uuid
from pathlib import Path

from argus.core.safe_names import is_plain_file_name, is_safe_name

ARGUS_HOME = Path(os.environ.get("ARGUS_HOME", _argus_public_path('home', '')))
STATE = ARGUS_HOME / "state"
JOBS = STATE / "jobs"
LEASES = STATE / "leases"
AUDIT = STATE / "audit.jsonl"
IDEMPOTENCY = STATE / "idempotency.json"
WORKSPACES = STATE / "workspaces"

SEALED_ROOTS = (Path(_argus_public_path('home', 'runs')),)

LEASE_KINDS = ("gpu", "data", "experiment")
DEFAULT_LEASE_TTL_S = 6 * 3600

ACTORS = ("human", "agent", "service")


class ActionError(Exception):
    pass


class Refused(ActionError):
    """A refusal is a normal outcome, not a crash."""

    def __init__(self, code: str, why: str, **extra):
        super().__init__("%s: %s" % (code, why))
        self.code, self.why, self.extra = code, why, extra

    def as_dict(self) -> dict:
        return dict({"status": "REFUSED", "code": self.code, "why": self.why}, **self.extra)



def _refuse_network_paths(value, depth: int = 0) -> None:
    """No parameter of any action may name a network or namespace path (or hold a NUL): whatever a handler later does with a path, resolving one of these contacts another machine."""
    if depth > 8:
        raise Refused("BAD_PARAMS", "parameters are nested more than eight levels deep")
    if isinstance(value, str):
        from argus.core import paths as _paths
        if "\x00" in value or _paths.is_remote_path(value):
            raise Refused("REMOTE_PATH_REFUSED", "a parameter names a network path; actions read and write local paths only")
    elif isinstance(value, dict):
        for k, v in value.items():
            _refuse_network_paths(k, depth + 1)
            _refuse_network_paths(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _refuse_network_paths(v, depth + 1)


@dataclasses.dataclass(frozen=True)
class ActionSpec:
    """The one shape every caller sends."""

    action: str
    actor: str
    request_id: str
    idempotency_key: str
    params: dict = dataclasses.field(default_factory=dict)
    dry_run: bool = False

    FIELDS = ("action", "actor", "request_id", "idempotency_key", "params", "dry_run")

    @classmethod
    def parse(cls, obj: dict) -> "ActionSpec":
        if not isinstance(obj, dict):
            raise Refused("BAD_SPEC", "an ActionSpec must be a JSON object")
        unknown = sorted(set(obj) - set(cls.FIELDS))
        if unknown:
            raise Refused("UNKNOWN_FIELD",
                          "unknown field(s) %s. Ignoring them would let a caller believe a "
                          "parameter took effect" % unknown)
        for req in ("action", "actor", "request_id", "idempotency_key"):
            if not obj.get(req) or not isinstance(obj[req], str):
                raise Refused("MISSING_FIELD", "%r is required and must be a string" % req)
        if len(obj["idempotency_key"]) > 200 or len(obj["request_id"]) > 200:
            raise Refused("BAD_KEY", "an idempotency key and a request id are at most 200 characters")
        kind = obj["actor"].split(":", 1)[0]
        if kind not in ACTORS:
            raise Refused("BAD_ACTOR",
                          "actor must start with one of %s, e.g. 'agent:assistant'" % (ACTORS,))
        params = obj.get("params") or {}
        if not isinstance(params, dict):
            raise Refused("BAD_PARAMS", "params must be an object")
        _refuse_network_paths(params)
        return cls(action=obj["action"], actor=obj["actor"], request_id=obj["request_id"],
                   idempotency_key=obj["idempotency_key"], params=params,
                   dry_run=bool(obj.get("dry_run", False)))

    def fingerprint(self) -> str:
        payload = json.dumps({"action": self.action, "params": self.params}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()



def _now() -> float:
    return time.time()


def lease_path(kind: str) -> Path:
    return LEASES / ("%s.lease" % kind)


def read_lease(kind: str) -> dict | None:
    p = lease_path(kind)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    exp = d.get("expires_at", 0) if isinstance(d, dict) else None
    try:
        real = isinstance(exp, (int, float)) and not isinstance(exp, bool) and math.isfinite(exp) and exp <= 4102444800
    except (OverflowError, ValueError):
        real = False
    if not real:
        return None
    if d.get("expires_at", 0) < _now():
        return None
    return d


@contextlib.contextmanager
def _lease_lock(kind: str, *, wait: bool = True):
    """Serialize lease check, replacement, and release across processes."""
    lock_path = LEASES / ("%s.lock" % kind)
    deadline = time.monotonic() + (30 if wait else 0.1)
    fd = None
    try:
        while fd is None:
            try:
                LEASES.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                    os.write(fd, b"\0")
                    os.fsync(fd)
                except FileExistsError:
                    fd = os.open(str(lock_path), os.O_RDWR, 0o600)
                st = os.fstat(fd)
                if st.st_size < 1 and abs(time.time() - st.st_mtime) > 1.0:
                    os.write(fd, b"\0")
                    os.fsync(fd)
                if os.fstat(fd).st_size < 1:
                    os.close(fd)
                    fd = None
                    raise PermissionError("lease lock initialization is incomplete")
            except OSError as exc:
                if fd is not None:
                    try:
                        os.close(fd)
                    finally:
                        fd = None
                if time.monotonic() >= deadline:
                    code = "LEASE_LOCK_TIMEOUT" if wait else "LEASE_HELD"
                    raise Refused(code, "the %s lease lock could not be opened" % kind) from exc
                time.sleep(0.05)
        lock_file = os.fdopen(fd, "r+b")
        fd = None
        with lock_file as lock:
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            code = "LEASE_LOCK_TIMEOUT" if wait else "LEASE_HELD"
                            raise Refused(code, "the %s lease lock is held" % kind) from exc
                        time.sleep(0.05)
                        lock.seek(0)
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError as exc:
                        if time.monotonic() >= deadline:
                            code = "LEASE_LOCK_TIMEOUT" if wait else "LEASE_HELD"
                            raise Refused(code, "the %s lease lock is held" % kind) from exc
                        time.sleep(0.05)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    finally:
        if fd is not None:
            os.close(fd)


def acquire_lease(kind: str, holder: str, *, ttl_s: int = DEFAULT_LEASE_TTL_S,
                  job_id: str | None = None) -> dict:
    """Acquire one lease while serializing its check and replacement across processes."""
    if kind not in LEASE_KINDS:
        raise Refused("BAD_LEASE", "unknown lease kind %r" % kind)
    with _lease_lock(kind):
        return _acquire_lease_locked(kind, holder, ttl_s=ttl_s, job_id=job_id)


def _acquire_lease_locked(kind: str, holder: str, *, ttl_s: int, job_id: str | None, os_locked: bool = False) -> dict:
    p = lease_path(kind)
    held = read_lease(kind)
    superseded = None
    if held and os_locked and held.get("os_locked"):
        superseded = {"holder": held.get("holder"), "pid": held.get("pid"), "job_id": held.get("job_id"),
                      "expires_at": held.get("expires_at"),
                      "why": "the OS lock was free while this os-locked lease was unexpired: its process is gone"}
        held = None
    if held:
        raise Refused("LEASE_HELD",
                      "the %s lease is held by %s until %s. It is refused rather than "
                      "queued: a second holder would mean two owners"
                      % (kind, held.get("holder"),
                         time.strftime("%H:%M:%SZ", time.gmtime(held["expires_at"]))),
                      lease=held)
    rec = {"kind": kind, "holder": holder, "token": uuid.uuid4().hex,
           "job_id": job_id, "pid": os.getpid(), "os_locked": bool(os_locked),
           "acquired_at": _now(), "expires_at": _now() + ttl_s}
    if superseded:
        rec["superseded"] = superseded
    tmp = p.with_name(".%s.tmp.%d.%s" % (p.name, os.getpid(), uuid.uuid4().hex))
    try:
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(str(tmp), str(p))
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise Refused("LEASE_RACE", str(exc)[:200]) from exc
    return rec


def release_lease(kind: str, holder: str, *, lease_token: str) -> bool:
    """Release only the exact acquisition; a stale holder cannot clear its successor."""
    if kind not in LEASE_KINDS:
        raise Refused("BAD_LEASE", "unknown lease kind %r" % kind)
    with _lease_lock(kind):
        return _release_lease_locked(kind, holder, lease_token=lease_token)


def _release_lease_locked(kind: str, holder: str, *, lease_token: str) -> bool:
    try:
        held = json.loads(lease_path(kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if held.get("holder") != holder:
        raise Refused("NOT_LEASE_HOLDER",
                      "%s does not hold the %s lease; %s does" % (holder, kind,
                                                                  held.get("holder")))
    if not lease_token or held.get("token") != lease_token:
        return False
    lease_path(kind).unlink(missing_ok=True)
    return True


@contextlib.contextmanager
def held_lease(kind: str, holder: str, *, ttl_s: int = DEFAULT_LEASE_TTL_S,
               job_id: str | None = None):
    """Hold the OS lease lock for the full action, including after the display TTL expires."""
    if kind not in LEASE_KINDS:
        raise Refused("BAD_LEASE", "unknown lease kind %r" % kind)
    with _lease_lock(kind, wait=False):
        rec = _acquire_lease_locked(kind, holder, ttl_s=ttl_s, job_id=job_id, os_locked=True)
        try:
            yield rec
        finally:
            _release_lease_locked(kind, holder, lease_token=rec["token"])


def lease_holder_alive(kind: str) -> bool | None:
    """Is the process that holds this lease still running?"""
    if not is_safe_name(kind):
        return None
    try:
        raw = json.loads(lease_path(kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not raw.get("os_locked"):
        return None
    try:
        with _lease_lock(kind, wait=False):
            return False
    except Refused as exc:
        if exc.code == "LEASE_HELD":
            return True
        raise


GPU_OWNER_PATTERN = (os.environ.get("ARGUS_GPU_OWNER_PATTERN") or "argus_gpu_worker").replace("'", "''")


def observe_external_gpu_owner() -> dict:
    """A science run launched outside this service still owns the card."""
    try:
        import subprocess
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and "
             "$_.CommandLine -match '" + GPU_OWNER_PATTERN + "' } | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30)
        data = json.loads(p.stdout or "null")
        if isinstance(data, dict):
            data = [data]
        procs = [d for d in (data or [])
                 if not any(t in (d.get("CommandLine") or "")
                            for t in ("until ", "for i in", "seq ", "while "))]
        return {"busy": bool(procs), "pids": [d.get("ProcessId") for d in procs]}
    except Exception as exc:
        return {"busy": None, "why": "could not observe: %s" % str(exc)[:120]}



SECRET_PARAMS = ("value", "token", "password", "secret", "key")
_SECRET_EXACT = frozenset({"value", "key", "token", "auth", "authorization", "cookie", "setcookie", "bearer", "jwt", "pwd", "pass", "passwd", "credential", "credentials",
                           "psk", "otp", "pin", "dsn", "hmac", "mnemonic", "authcode", "oauthcode", "privkey"})
_SECRET_CONTAINS = ("password", "passwd", "passphrase", "secret", "apikey", "privatekey", "privkey", "accesskey", "credential", "accesstoken", "refreshtoken", "idtoken", "authtoken",
                    "sessiontoken", "bearertoken", "tokenvalue", "sshkey", "awskey", "signingkey", "authkey", "masterkey", "encryptionkey", "clientkey", "authcode", "oauthcode",
                    "authheader", "authorizationheader", "bearerauth", "cookie", "connectionstring", "hmac")
_SECRET_ENDS = ("token",)
_KW = (r"(?:authorization|api[-_]?key|x-api-key|secret[-_]?key|signing[-_]?key|private[-_]?key|access[-_]?key|ssh[-_]?key|x-amz-signature|token|password|passwd|passphrase|secret|cookie"
       r"|\b(?:pass|pwd|sig|signature)\b)")
_VALUE = r"(?:'[^'\n]{1,2000}'|\"[^\"\n]{1,2000}\"|[\"'][^\"'\r\n]*|[^\s&;,\"']{2,})"
_SECRET_IN_TEXT = (
    (re.compile(r"(?i)-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----[\s\S]{0,40000}?(?:-----END [A-Z ]{0,30}PRIVATE KEY-----|$)"), "<redacted private key>"),
    (re.compile(r"(?i)\b(authorization[ \t]{0,8}[:=][ \t]{0,8})(?:(?:basic|bearer|token|digest|negotiate|apikey)[ \t]{1,8})?[A-Za-z0-9._~+/=:-]{8,}"), r"\1<redacted>"),
    (re.compile(r"(?i)\bbearer[ \t]{1,8}[A-Za-z0-9._~+/=-]{8,}"), "Bearer <redacted>"),
    (re.compile(r"(?i)\b((?:set-)?cookie[ \t]{0,8}:[ \t]{0,8})[^\r\n]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(://)[^\s/]{0,200}@"), r"\1<redacted>@"),
    (re.compile(r"(?i)(" + _KW + r"[\"']?[ \t]{0,8}[:=][ \t]{0,8})(?!<redacted)" + _VALUE), r"\1<redacted>"),
    (re.compile(r"(?i)(\"[^\"\n]{0,64}" + _KW + r"[^\"\n]{0,64}\"[ \t]{0,8}:[ \t]{0,8})\"[^\"\n]{0,4000}\""), r'\1"<redacted>"'),
    (re.compile(r"(?i)(--[a-z0-9_-]{0,30}(?:token|password|passwd|passphrase|secret|api[-_]?key|access[-_]?key|key|auth)(?:=|[ \t]+))\S+"), r"\1<redacted>"),
)
_TEXT_LIMIT = 2_000_000


def _is_secret_key(name) -> bool:
    plain = "".join(c for c in str(name).lower() if c.isalnum())
    return plain in _SECRET_EXACT or any(f in plain for f in _SECRET_CONTAINS) or plain.endswith(_SECRET_ENDS)


def redact(params, _depth: int = 0):
    """`params` with every secret-named value replaced, at any depth of dicts and lists, and secrets written inside a string (a bearer header, URL credentials, `key=value`, pasted JSON, a `--token x`..."""
    if _depth > 8:
        return "<redacted: nested too deeply to inspect>"
    if isinstance(params, dict):
        return {k: ("<redacted>" if _is_secret_key(k) else redact(v, _depth + 1)) for k, v in params.items()}
    if isinstance(params, (list, tuple)):
        return [redact(v, _depth + 1) for v in params]
    if isinstance(params, str):
        if len(params) >= _TEXT_LIMIT:
            return "<redacted: text of %d characters is too long to inspect>" % len(params)
        if params.lower().count("-----begin") > 20:
            return "<redacted: too many key blocks to inspect>"
        for rx, rep in _SECRET_IN_TEXT:
            params = rx.sub(rep, params)
    return params


def audit(event: dict) -> str:
    """Append-only, hash-chained."""
    from argus.core import ledger_v2
    if ledger_v2.is_sealed():
        return ledger_v2.append(dict(event))["this_hash"]
    STATE.mkdir(parents=True, exist_ok=True)
    with _lease_lock("audit_v1"):
        if ledger_v2.is_sealed():
            return ledger_v2.append(dict(event))["this_hash"]
        prev = "0" * 64
        if AUDIT.is_file():
            try:
                last = AUDIT.read_text(encoding="utf-8").strip().splitlines()
                if last:
                    prev = json.loads(last[-1]).get("this_hash", prev)
            except (OSError, ValueError):
                pass
        rec = dict(event)
        rec["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec["prev_hash"] = prev
        rec["this_hash"] = hashlib.sha256(
            (prev + json.dumps(rec, sort_keys=True, default=str)).encode("utf-8")).hexdigest()
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return rec["this_hash"]


def verify_audit_chain(path=None) -> dict:
    """Verify the v1 ledger."""
    src = Path(path) if path is not None else AUDIT
    if not src.is_file():
        return {"status": "EMPTY", "n": 0}
    prev = "0" * 64
    n = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("prev_hash") != prev:
            return {"status": "BROKEN", "at": n, "why": "prev_hash does not chain"}
        body = {k: v for k, v in rec.items() if k != "this_hash"}
        want = hashlib.sha256(
            (prev + json.dumps(body, sort_keys=True, default=str)).encode("utf-8")).hexdigest()
        if want != rec.get("this_hash"):
            return {"status": "BROKEN", "at": n,
                    "why": "record content does not match its own hash"}
        prev = rec["this_hash"]
        n += 1
    return {"status": "INTACT", "n": n, "head": prev}



def is_sealed_root(d) -> bool:
    """Is this run directory sealed?"""
    d = Path(d)
    if not d.is_dir():
        return False
    if not (d / "COMPLETE").is_file():
        return False
    files = [f for f in d.rglob("*") if f.is_file()]
    return bool(files) and all(not os.access(f, os.W_OK) for f in files)


def refuse_if_sealed(path) -> None:
    """No action may write inside a sealed run root."""
    from argus.core import paths as _paths
    if _paths.is_remote_path(path) or not _paths.plain_local_path(path):
        raise Refused("BAD_PATH", "a path an action writes to is a plain local path: no network path, stream or device form")
    p = Path(path).resolve()
    for root in SEALED_ROOTS:
        try:
            r = root.resolve()
        except OSError:
            continue
        if p == r or r in p.parents:
            for anc in [p] + list(p.parents):
                if anc.parent == r and is_sealed_root(anc):
                    raise Refused("SEALED_EVIDENCE",
                                  "%s is inside the sealed run root %s. Sealed evidence is "
                                  "immutable; reproduce it, do not amend it" % (p, anc))
    return None



_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}")


def job_dir(job_id: str) -> Path:
    """A job id is a bare file name."""
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id) or ".." in job_id or not is_plain_file_name(job_id):
        raise Refused("BAD_JOB_ID", "a job id is letters, digits, dot, dash and underscore only")
    return JOBS / job_id


def _write_job_unlocked(job: dict) -> dict:
    d = job_dir(job["job_id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ("job.json.%d.%s.tmp" % (os.getpid(), uuid.uuid4().hex[:8]))
    try:
        tmp.write_text(json.dumps(job, indent=1, default=str), encoding="utf-8")
        deadline = time.monotonic() + 5.0
        while True:
            try:
                os.replace(str(tmp), str(d / "job.json"))
                break
            except PermissionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return job


def write_job(job: dict) -> dict:
    with _lease_lock("jobs"):
        return _write_job_unlocked(job)


def read_job(job_id: str) -> dict | None:
    try:
        p = job_dir(job_id) / "job.json"
    except Refused:
        return None
    if not p.is_file():
        return None
    for attempt in range(50):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except PermissionError:
            time.sleep(0.02)
        except (OSError, ValueError):
            return None
    return None


def _idempotency() -> dict:
    if not IDEMPOTENCY.is_file():
        return {}
    try:
        m = json.loads(IDEMPOTENCY.read_text(encoding="utf-8"))
        if isinstance(m, dict):
            return m
    except (OSError, ValueError):
        pass
    with contextlib.suppress(OSError):
        os.replace(str(IDEMPOTENCY), str(IDEMPOTENCY.with_name("%s.corrupt-%d-%s" % (IDEMPOTENCY.name, int(time.time()), uuid.uuid4().hex[:6]))))
    return {}


def _store_idempotency(m: dict) -> None:
    tmp = IDEMPOTENCY.with_name("%s.%d.%s.tmp" % (IDEMPOTENCY.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        tmp.write_text(json.dumps(m, indent=1), encoding="utf-8")
        for attempt in range(250):
            try:
                os.replace(str(tmp), str(IDEMPOTENCY))
                return
            except PermissionError:
                if attempt == 249:
                    raise
                time.sleep(0.02)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def release_idempotency(key: str, job_id: str) -> bool:
    """Forget a claim that never led to a run (the job could not even be recorded), so the same request can be sent again."""
    with _lease_lock("idempotency"):
        m = _idempotency()
        if (m.get(key) or {}).get("job_id") != job_id:
            return False
        del m[key]
        _store_idempotency(m)
    return True


def claim_idempotency(key: str, job_id: str, fingerprint: str) -> dict | None:
    """Atomically record `key -> job`."""
    STATE.mkdir(parents=True, exist_ok=True)
    with _lease_lock("idempotency"):
        m = _idempotency()
        if key in m:
            return m[key]
        m[key] = {"job_id": job_id, "fingerprint": fingerprint, "at": _now()}
        _store_idempotency(m)
    return None


def _remember(key: str, job_id: str, fingerprint: str) -> None:
    claim_idempotency(key, job_id, fingerprint)


def _prune_orphan_claims(min_age_s: float, now: float) -> list:
    """A key claimed by a process that died before it wrote the job answers DUPLICATE with no job, forever."""
    dropped = []
    with _lease_lock("idempotency"):
        m = _idempotency()
        for key, rec in list(m.items()):
            try:
                if not isinstance(rec, dict):
                    orphan = True
                else:
                    try:
                        has_job = (job_dir(rec.get("job_id", "")) / "job.json").exists()
                    except Refused:
                        has_job = False
                    orphan = not has_job and now - float(rec.get("at", 0)) >= min_age_s
            except (TypeError, ValueError, OverflowError):
                orphan = True
            if orphan:
                dropped.append(key)
                del m[key]
        if dropped:
            _store_idempotency(m)
    return dropped


def reconcile_jobs(*, min_age_s: float = 900.0, now: float | None = None) -> list:
    """Mark jobs left RUNNING by a process that is gone as INTERRUPTED, so a restart does not leave them \"running\" (and their idempotency key answering DUPLICATE for a job that will never finish)."""
    changed = []
    now = _now() if now is None else now
    for d in (sorted(JOBS.iterdir()) if JOBS.is_dir() else []):
        try:
            job_dir(d.name)
            job = json.loads((d / "job.json").read_text(encoding="utf-8"))
            if not isinstance(job, dict) or job.get("state") != "RUNNING" or job.get("job_id") != d.name:
                continue
            try:
                started = calendar.timegm(time.strptime(job.get("started_utc", ""), "%Y-%m-%dT%H:%M:%SZ"))
            except (ValueError, OverflowError, TypeError):
                started = 0.0
            if now - started < min_age_s:
                continue
            plan_doc = job.get("plan") if isinstance(job.get("plan"), dict) else {}
            if any(lease_holder_alive(k) for k in (plan_doc.get("leases") or []) if isinstance(k, str)):
                continue
            with _lease_lock("jobs"):
                current = json.loads((d / "job.json").read_text(encoding="utf-8"))
                if not isinstance(current, dict) or current.get("state") != "RUNNING":
                    continue
                current.update(state="INTERRUPTED", result={"status": "INTERRUPTED", "code": "INTERRUPTED_BY_RESTART",
                                                            "why": "the process running this job ended before it finished; nothing was recorded as its result. Submit it again with a new idempotency key."},
                               finished_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                _write_job_unlocked(current)
            changed.append(current.get("job_id"))
            with contextlib.suppress(Exception):
                audit({"event": "action.interrupted", "job_id": current.get("job_id"), "action": current.get("action")})
        except Exception:
            continue
    with contextlib.suppress(Exception):
        _prune_orphan_claims(min_age_s, now)
    return changed
