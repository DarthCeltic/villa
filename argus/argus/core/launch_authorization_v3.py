"""Launch authorisation v3: every recorded identity is RECOMPUTED AT LAUNCH and a mismatch REFUSES."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time

from argus.core import launch_authorization_v2 as V2
from argus.core.safe_names import is_safe_name
from argus.core import paths

CONTRACT = "argus-launch-authorization-v3"
SUPERSEDES = "argus-launch-authorization-v2"
AUTH_DIR_PARTS = ("pipeline_seams", "launch_authorization_v3")
CEILING = V2.CEILING

BOUND_FIELDS = (
  "runner_sha256",
  "module_manifest_sha256",
  "contract_sha256",
  "plan_sha256",
  "input_manifest_sha256",
  "checkpoint_sha256",
  "science_env_sha256",
  "command_sha256",
  "git_commit",
  "dirty_patch_sha256",
  "dependency_sha256",
)

UNKNOWN_VALUES = (None, "", "UNKNOWN")

MUST_NOT_AUTHORISE = tuple(V2.MUST_NOT_AUTHORISE)


class AuthorizationRefusal(V2.AuthorizationRefusal):
    """Raised when the live machine does not match what was authorised."""


def checkpoint_identity(checkpoint_path) -> dict:
    """Raw-byte sha256 of the checkpoint."""
    if checkpoint_path is None or not str(checkpoint_path).strip():
        raise AuthorizationRefusal(
          "no checkpoint path was declared. A run whose weights are unbound produces a number "
          "nobody can attribute; declare the checkpoint (or the literal file a weightless run "
          "reads instead) rather than leaving it null.")
    p = pathlib.Path(checkpoint_path)
    if not p.is_file():
        raise AuthorizationRefusal("checkpoint %s does not exist" % p)
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": h.hexdigest()}


def git_commit() -> str:
    from argus.core import git_state
    return git_state.head(V2.paths.repo()) or "UNKNOWN"


def measure(*, runner_rel: str, modules, contract_sha256: str, plan_sha256: str,
            bindings, checkpoint_path, argv=None) -> dict:
    """Everything, live."""
    m = V2.measure(runner_rel=runner_rel, modules=modules, contract_sha256=contract_sha256,
                   plan_sha256=plan_sha256, bindings=bindings, argv=argv)
    ck = checkpoint_identity(checkpoint_path)
    m["checkpoint"] = ck
    m["checkpoint_sha256"] = ck["sha256"]
    m["git_commit"] = git_commit()
    m["commit"] = m["git_commit"]
    m["measured_by"] = CONTRACT
    return m


def _canon(obj) -> str:
    return V2._canon(obj)


_ROOT_OVERRIDE = None


def set_root_for_tests(path):
    global _ROOT_OVERRIDE
    prev, _ROOT_OVERRIDE = _ROOT_OVERRIDE, path
    return prev


def auth_root() -> pathlib.Path:
    if _ROOT_OVERRIDE is not None:
        return pathlib.Path(_ROOT_OVERRIDE)
    return paths.artifact_write_root().joinpath(*AUTH_DIR_PARTS)


def _checked_id(aid) -> str:
    """An authorization id becomes part of a file name, so it is a plain name: `..` and separators cannot climb out of the authorization directory."""
    if not isinstance(aid, str) or not is_safe_name(aid):
        raise AuthorizationRefusal("an authorization id is a plain name (letters, digits, dot, dash, underscore), never a path")
    return aid


def auth_path(aid: str) -> pathlib.Path:
    return auth_root() / ("AUTH3_%s.json" % _checked_id(aid))


def consumption_path(aid: str) -> pathlib.Path:
    return auth_root() / ("CONSUMED3_%s.json" % _checked_id(aid))


def _unknown_fields(doc: dict) -> list:
    return [f for f in BOUND_FIELDS if doc.get(f) in UNKNOWN_VALUES]


def issue(*, authorization_id: str, node_id: str, measured: dict, development_controls,
          permitted_arms, ttl_hours: int = 24, authorised_by: str = "operator") -> dict:
    if not str(authorization_id or "").strip():
        raise AuthorizationRefusal("an authorisation needs an identity to be consumed against.")
    arms = sorted(set(permitted_arms or ()))
    controls = sorted(set(development_controls or ()))
    if not arms:
        raise AuthorizationRefusal("an authorisation permitting no arms authorises nothing.")
    if not controls:
        raise AuthorizationRefusal("an authorisation naming no controls does not say what the "
                                   "experiment may touch.")
    unknown = _unknown_fields(measured)
    if unknown:
        raise AuthorizationRefusal(
          "REFUSING TO ISSUE: %s measured as UNKNOWN/absent. A binding over an unknown value "
          "binds nothing." % ", ".join(unknown))
    if (measured.get("dirty") or {}).get("relevant_dirty"):
        raise AuthorizationRefusal(
          "REFUSING TO ISSUE: files the run imports are dirty: %s."
          % measured["dirty"]["relevant_dirty"])
    body = {
      "contract": CONTRACT, "supersedes": SUPERSEDES,
      "supersedes_why": "v2 recorded the commit and never compared it, bound no checkpoint, and "
                        "treated an absent live value as agreement.",
      "authorization_id": str(authorization_id), "node_id": node_id,
      "issued_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "expires_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                   time.gmtime(time.time() + ttl_hours * 3600)),
      "single_use": True, "authorised_by": authorised_by,
      "scientific_ceiling": CEILING,
      "development_controls": controls, "permitted_arms": arms,
      "what_this_does_not_authorise": list(MUST_NOT_AUTHORISE),
      "bound_fields": list(BOUND_FIELDS),
    }
    for f in BOUND_FIELDS:
        body[f] = measured[f]
    body["measured_detail"] = {k: measured.get(k) for k in
                               ("runner", "module_manifest", "input_manifest", "dependencies",
                                "dirty", "command", "science_env", "checkpoint")}
    body["receipt_sha256"] = _canon({k: v for k, v in body.items() if k != "receipt_sha256"})
    p = auth_path(body["authorization_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise AuthorizationRefusal("authorisation %r already exists; overwriting one is how "
                                   "single-use becomes reusable." % authorization_id) from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(body, indent=2, default=str))
        fh.flush()
        os.fsync(fh.fileno())
    return dict(body, path=str(p))


def load(aid: str) -> dict:
    p = auth_path(aid)
    if not p.is_file():
        raise AuthorizationRefusal("no v3 authorisation %r at %s." % (aid, p))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise AuthorizationRefusal("authorisation %r is unreadable (%s); UNKNOWN refuses."
                                   % (aid, e)) from None


def validate(aid: str, *, measured: dict, arms=None, now: float | None = None) -> dict:
    """Every bound field: present on BOTH sides, not UNKNOWN on either, and equal."""
    doc = load(aid)
    problems = []
    for f in BOUND_FIELDS:
        want, got = doc.get(f), measured.get(f)
        if want in UNKNOWN_VALUES:
            problems.append("%s is not bound by the authorisation (%r)" % (f, want))
        elif got in UNKNOWN_VALUES:
            problems.append("%s could not be measured live (%r); UNKNOWN refuses" % (f, got))
        elif want != got:
            problems.append("%s MISMATCH: authorised %s, live %s" % (f, str(want)[:16],
                                                                     str(got)[:16]))
    if doc.get("contract") != CONTRACT:
        problems.append("contract %r is not %r" % (doc.get("contract"), CONTRACT))
    if doc.get("receipt_sha256") != _canon({k: v for k, v in doc.items()
                                            if k != "receipt_sha256"}):
        problems.append("the authorisation's own content hash does not match its body; it has "
                        "been edited since issue.")
    rd = (measured.get("dirty") or {}).get("relevant_dirty") or []
    if rd:
        problems.append("files the run imports are dirty RIGHT NOW: %s" % rd)
    try:
        import calendar
        exp = calendar.timegm(time.strptime(str(doc.get("expires_utc")), "%Y-%m-%dT%H:%M:%SZ"))
        if (now or time.time()) > exp:
            problems.append("EXPIRED at %s." % doc.get("expires_utc"))
    except ValueError:
        problems.append("expires_utc %r is unparseable; UNKNOWN refuses." % doc.get("expires_utc"))
    if consumption_path(aid).exists():
        problems.append("ALREADY CONSUMED; single use means single use.")
    if arms:
        extra = sorted(set(arms) - set(doc.get("permitted_arms") or ()))
        if extra:
            problems.append("arm(s) %s not permitted." % extra)
    if doc.get("scientific_ceiling") != CEILING:
        problems.append("ceiling %r; only %r is permitted." % (doc.get("scientific_ceiling"),
                                                               CEILING))
    return {"contract": CONTRACT, "authorization_id": aid, "valid": not problems,
            "problems": problems,
            "recomputed": {f: measured.get(f) for f in BOUND_FIELDS},
            "authorised": {f: doc.get(f) for f in BOUND_FIELDS}}


def assert_valid(aid: str, **kw) -> dict:
    rec = validate(aid, **kw)
    if rec["valid"]:
        return rec
    raise AuthorizationRefusal("v3 authorisation %r does not cover this launch:\n%s"
                               % (aid, "\n".join("  - " + p for p in rec["problems"])))


def consume(aid: str, *, measured: dict) -> dict:
    p = consumption_path(aid)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"contract": CONTRACT, "authorization_id": aid,
            "consumed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "bound_values": {f: measured.get(f) for f in BOUND_FIELDS}}
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise AuthorizationRefusal("authorisation %r already consumed (%s)." % (aid, p)) from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(body, indent=2, default=str))
        fh.flush()
        os.fsync(fh.fileno())
    return body


def launch(aid: str, *, runner_rel: str, modules, contract_sha256: str, plan_sha256: str,
           bindings, checkpoint_path, argv=None, arms=None, measure_fn=None) -> dict:
    """THE launch gate: measure live NOW, validate, then consume."""
    fn = measure_fn or measure
    live = fn(runner_rel=runner_rel, modules=modules, contract_sha256=contract_sha256,
              plan_sha256=plan_sha256, bindings=bindings, checkpoint_path=checkpoint_path,
              argv=argv)
    rec = assert_valid(aid, measured=live, arms=arms)
    consumed = consume(aid, measured=live)
    return {"validated": rec, "consumed": consumed}
