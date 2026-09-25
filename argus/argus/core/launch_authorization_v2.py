"""Source-bound launch authorisation."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

from argus.core import paths

CONTRACT = "argus-launch-authorization-v2"

AUTH_DIR_PARTS = ("launch_authorization_v2",)

CEILING = "DEVELOPMENT_ONLY"

SCIENCE_ENV = (
  "ARGUS_SEED", "ARGUS_DEVICE", "ARGUS_BUDGET", "ARGUS_DEPTH",
  "CUDA_VISIBLE_DEVICES", "PYTORCH_CUDA_ALLOC_CONF", "OMP_NUM_THREADS",
  "ARGUS_GPU_TESTS", "PYTHONHASHSEED",
)

BOUND_FIELDS = (
  "runner_sha256",
  "module_manifest_sha256",
  "contract_sha256",
  "plan_sha256",
  "input_manifest_sha256",
  "dependency_sha256",
  "dirty_patch_sha256",
  "command_sha256",
  "science_env_sha256",
)

MUST_NOT_AUTHORISE = (
  "editing the frozen contract after results",
  "rerunning a sealed experiment",
  "an unauthorised target search",
  "opening prize-target voxels",
  "prize submission",
  "public publication",
  "calling mixed-authority evaluation qualification",
)


class AuthorizationRefusal(RuntimeError):
    """Raised when the live machine does not match what was authorised."""


def _sha_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha_norm(b: bytes) -> str:
    """LF-normalised, so a CRLF checkout and an LF one agree."""
    return hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest()


def _sha_file(p) -> str:
    return _sha_norm(pathlib.Path(p).read_bytes())


def _canon(obj) -> str:
    return _sha_text(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str))


def _git(*args) -> str:
    """Bare git at ARGUS_ROOT."""
    from argus.core import git_state
    rc, out, _ = git_state.git(paths.repo(), *args)
    return out if rc == 0 else ""



def runner_identity(runner_rel: str) -> dict:
    p = paths.repo(runner_rel)
    if not p.is_file():
        raise AuthorizationRefusal("runner %r does not exist" % runner_rel)
    return {"path": runner_rel, "sha256": _sha_file(p)}


def module_manifest(modules) -> dict:
    """Ordered hash manifest of every named ARGUS module."""
    rows = []
    for name in sorted(modules):
        rel = "argus/core/%s.py" % name
        p = paths.repo(rel)
        rows.append({"module": name, "path": rel,
                     "sha256": _sha_file(p) if p.is_file() else None,
                     "present": p.is_file()})
    missing = [r["module"] for r in rows if not r["present"]]
    if missing:
        raise AuthorizationRefusal(
          "these ARGUS modules are named in the manifest and absent from disk: %s. A manifest "
          "over files that do not exist binds nothing." % missing)
    return {"modules": rows, "count": len(rows), "manifest_sha256": _canon(rows)}


def dependency_identity() -> dict:
    """Interpreter, torch and CUDA."""
    out = {"python": sys.version.split()[0], "executable": sys.executable,
           "platform": sys.platform}
    try:
        import torch
        out["torch"] = torch.__version__
        out["cuda_available"] = bool(torch.cuda.is_available())
        out["cuda_version"] = getattr(torch.version, "cuda", None)
        out["device_name"] = (torch.cuda.get_device_name(0)
                              if torch.cuda.is_available() else None)
    except Exception as e:
        out["torch"] = "UNKNOWN: %s" % type(e).__name__
    try:
        import numpy
        out["numpy"] = numpy.__version__
    except Exception:
        out["numpy"] = "UNKNOWN"
    out["dependency_sha256"] = _canon(out)
    return out


def _scroll_metadata_advisory(scroll) -> dict:
    """Scroll-metadata check at launch authorization -- ADVISORY, NOT A GATE."""
    if not scroll:
        return {"present": False, "identity_problems": [], "record_sha256": None}
    from argus.core import scroll_dataset_metadata as SDM
    try:
        rec = SDM.load_scroll_metadata(str(scroll))
    except SDM.MetadataRefusal as exc:
        return {"present": False, "identity_problems": ["refused: %s" % exc], "record_sha256": None}
    if rec is None:
        return {"present": False, "identity_problems": [], "record_sha256": None}
    return {"present": True, "identity_problems": [], "record_sha256": rec.record_sha256}


def input_manifest(bindings) -> dict:
    """Arrays, stores and label artifacts by hash."""
    rows = []
    for b in bindings:
        rows.append({
          "scroll": b.get("scroll"), "segment": b.get("segment"),
          "array_path": str(b.get("array_path")),
          "array_shape": b.get("array_shape"),
          "declared_level": b.get("declared_level"),
          "label_sha256": b.get("label_sha256"),
          "label_meta_sha256": b.get("label_meta_sha256"),
          "label_authority": b.get("label_authority"),
          "scroll_dataset_metadata": _scroll_metadata_advisory(b.get("scroll")),
        })
    rows.sort(key=lambda r: (str(r["scroll"]), str(r["segment"])))
    return {"segments": rows, "count": len(rows), "input_manifest_sha256": _canon(rows)}


def dirty_patch(relevant) -> dict:
    """A canonical hash of the uncommitted diff, and a REFUSAL if a relevant file is dirty."""
    from argus.core import git_state
    porcelain = git_state.status_porcelain(paths.repo()) or ""
    if porcelain == "" and git_state.head(paths.repo()) == "":
        raise AuthorizationRefusal(
          "git could not be read, so whether the source is committed is UNKNOWN. Unknown "
          "refuses: the alternative is binding a tree nobody could describe.")
    dirty = git_state.dirty_paths(paths.repo())
    rel = sorted(set(relevant))
    offending = sorted(d for d in dirty if d in rel)
    diff = _git("diff", "HEAD", "--", *rel) if rel else ""
    binding = git_state.binding(paths.repo())
    tree_hash = binding.get("tree_hash")
    patch_sha = _sha_text(diff) if tree_hash is None else _sha_text(diff + "\n#tree:" + tree_hash)
    extra = {} if tree_hash is None else {"subdir": binding["subdir"], "source_tree_hash": tree_hash}
    return {
      **extra,
      "dirty_path_count": len(dirty),
      "relevant_paths": rel,
      "relevant_dirty": offending,
      "dirty_patch_sha256": patch_sha,
      "patch_bytes": len(diff),
      "refuses_when": "a file the run imports is dirty. Its bytes exist in no commit, so the "
                      "run is unreproducible from any recorded state.",
      "does_not_refuse_when": "unrelated paths are dirty. A gate that fails for reasons "
                              "outside the experiment gets bypassed rather than satisfied.",
    }


def command_identity(argv=None) -> dict:
    a = list(argv if argv is not None else sys.argv)
    return {"argv": a, "command_sha256": _canon(a)}


def science_env() -> dict:
    env = {k: os.environ.get(k) for k in SCIENCE_ENV}
    return {"variables": env, "declared_set": list(SCIENCE_ENV),
            "science_env_sha256": _canon(env),
            "why_not_all_of_environ": "binding PATH or TEMP would make every gate fail for "
                                      "reasons that change nothing scientific. Only variables "
                                      "that alter behaviour are bound, and the list is "
                                      "declared so a reader can see what is not."}


def measure(*, runner_rel: str, modules, contract_sha256: str, plan_sha256: str,
            bindings, argv=None) -> dict:
    """Everything, measured live."""
    from argus.core import git_state
    mods = module_manifest(modules)
    relevant = [runner_rel] + [m["path"] for m in mods["modules"]]
    dep = dependency_identity()
    inp = input_manifest(bindings)
    cmd = command_identity(argv)
    env = science_env()
    dp = dirty_patch(relevant)
    run = runner_identity(runner_rel)
    return {
      "runner": run,
      "runner_sha256": run["sha256"],
      "module_manifest": mods,
      "module_manifest_sha256": mods["manifest_sha256"],
      "contract_sha256": contract_sha256,
      "plan_sha256": plan_sha256,
      "input_manifest": inp,
      "input_manifest_sha256": inp["input_manifest_sha256"],
      "dependencies": dep,
      "dependency_sha256": dep["dependency_sha256"],
      "dirty": dp,
      "dirty_patch_sha256": dp["dirty_patch_sha256"],
      "command": cmd,
      "command_sha256": cmd["command_sha256"],
      "science_env": env,
      "science_env_sha256": env["science_env_sha256"],
      "commit": git_state.head(paths.repo()) or "UNKNOWN",
    }



_ROOT_OVERRIDE = None


def set_root_for_tests(path):
    global _ROOT_OVERRIDE
    prev, _ROOT_OVERRIDE = _ROOT_OVERRIDE, path
    return prev


def auth_root():
    if _ROOT_OVERRIDE is not None:
        return pathlib.Path(_ROOT_OVERRIDE)
    return paths.find_artifact(*AUTH_DIR_PARTS)


def auth_path(aid: str):
    return auth_root() / ("AUTH2_%s.json" % aid)


def consumption_path(aid: str):
    return auth_root() / ("CONSUMED2_%s.json" % aid)


def issue(*, authorization_id: str, node_id: str, measured: dict,
          development_controls, permitted_arms, ttl_hours: int = 24,
          authorised_by: str = "operator") -> dict:
    if not str(authorization_id or "").strip():
        raise AuthorizationRefusal("an authorisation needs an identity to be consumed against.")
    arms = sorted(set(permitted_arms or ()))
    controls = sorted(set(development_controls or ()))
    if not arms:
        raise AuthorizationRefusal("an authorisation permitting no arms authorises nothing.")
    if not controls:
        raise AuthorizationRefusal("an authorisation naming no controls does not say what the "
                                   "experiment may touch.")
    if measured["dirty"]["relevant_dirty"]:
        raise AuthorizationRefusal(
          "REFUSING TO ISSUE: these files the run imports are dirty: %s. Their bytes exist in "
          "no commit, so the run would be unreproducible from any recorded state. Commit them "
          "or do not launch." % measured["dirty"]["relevant_dirty"])

    body = {
      "contract": CONTRACT,
      "authorization_id": str(authorization_id),
      "node_id": node_id,
      "issued_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "expires_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                   time.gmtime(time.time() + ttl_hours * 3600)),
      "single_use": True,
      "authorised_by": authorised_by,
      "scientific_ceiling": CEILING,
      "development_controls": controls,
      "permitted_arms": arms,
      "what_this_does_not_authorise": list(MUST_NOT_AUTHORISE),
      "bound_fields": list(BOUND_FIELDS),
      "supersedes": "argus-launch-authorization-v1, whose validate() compared no source "
                    "identity.",
      "what_this_still_cannot_do": "it cannot manufacture authority, and it cannot prove the "
                                   "bytes were what the operator INTENDED -- only that they "
                                   "are the bytes recorded. Provenance is identity, not intent.",
    }
    for f in BOUND_FIELDS:
        body[f] = measured[f]
    body["measured_detail"] = {k: measured[k] for k in
                               ("runner", "module_manifest", "input_manifest",
                                "dependencies", "dirty", "command", "science_env", "commit")}
    body["receipt_sha256"] = _canon({k: v for k, v in body.items()
                                     if k != "receipt_sha256"})

    p = auth_path(body["authorization_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        raise AuthorizationRefusal("an authorisation %r already exists; overwriting one is how "
                                   "single-use becomes reusable." % authorization_id)
    p.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return dict(body, path=str(p))


def load(aid: str) -> dict:
    p = auth_path(aid)
    if not p.is_file():
        raise AuthorizationRefusal("no v2 authorisation %r at %s." % (aid, p))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise AuthorizationRefusal("authorisation %r is unreadable (%s); UNKNOWN refuses."
                                   % (aid, e)) from None


def consumed(aid: str):
    p = consumption_path(aid)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"unreadable": True, "path": str(p)}


def validate(aid: str, *, measured: dict, arms=None, now: float | None = None) -> dict:
    """RECOMPUTE AND COMPARE every bound field."""
    doc = load(aid)
    problems = []

    missing = [f for f in BOUND_FIELDS if not doc.get(f)]
    if missing:
        problems.append("the authorisation does not bind %s. An unbound field is a field "
                        "nothing checks, which is the v1 defect." % ", ".join(missing))

    for f in BOUND_FIELDS:
        want, got = doc.get(f), measured.get(f)
        if want and got and want != got:
            problems.append("%s MISMATCH: authorised %s, live %s" % (f, str(want)[:16],
                                                                     str(got)[:16]))

    if doc.get("receipt_sha256") != _canon({k: v for k, v in doc.items()
                                            if k != "receipt_sha256"}):
        problems.append("the authorisation's own content hash does not match its body; it has "
                        "been edited since issue.")

    rd = (measured.get("dirty") or {}).get("relevant_dirty") or []
    if rd:
        problems.append("files the run imports are dirty RIGHT NOW: %s. Their bytes exist in "
                        "no commit." % rd)

    try:
        import calendar
        exp = calendar.timegm(time.strptime(str(doc.get("expires_utc")),
                                            "%Y-%m-%dT%H:%M:%SZ"))
        if (now or time.time()) > exp:
            problems.append("EXPIRED at %s." % doc.get("expires_utc"))
    except ValueError:
        problems.append("expires_utc %r is unparseable; UNKNOWN refuses."
                        % doc.get("expires_utc"))

    c = consumed(aid)
    if c:
        problems.append("ALREADY CONSUMED (%s); single use means single use."
                        % (c.get("consumed_utc") or c.get("path")))

    if arms:
        extra = sorted(set(arms) - set(doc.get("permitted_arms") or ()))
        if extra:
            problems.append("arm(s) %s not permitted." % extra)

    if doc.get("scientific_ceiling") != CEILING:
        problems.append("ceiling %r; only %r is permitted." % (doc.get("scientific_ceiling"),
                                                               CEILING))
    return {
      "contract": CONTRACT, "authorization_id": aid,
      "valid": not problems, "problems": problems,
      "recomputed": {f: measured.get(f) for f in BOUND_FIELDS},
      "authorised": {f: doc.get(f) for f in BOUND_FIELDS},
      "rule": "every bound field is RECOMPUTED live and compared. A field recorded and never "
              "recomputed lies as soon as anything moves, which is what happened to v1.",
    }


def assert_valid(aid: str, **kw) -> dict:
    rec = validate(aid, **kw)
    if rec["valid"]:
        return rec
    raise AuthorizationRefusal(
      "v2 authorisation %r does not cover this run:\n%s"
      % (aid, "\n".join("  - " + p for p in rec["problems"])))


def consume(aid: str, *, measured: dict) -> dict:
    """Spend it, recording EVERY bound value."""
    p = consumption_path(aid)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
      "contract": CONTRACT, "authorization_id": aid,
      "consumed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "bound_values": {f: measured.get(f) for f in BOUND_FIELDS},
      "commit": measured.get("commit"),
      "detail": {k: measured.get(k) for k in ("runner", "module_manifest", "input_manifest",
                                              "dependencies", "dirty", "command",
                                              "science_env")},
      "why_written_first": "a record written after the run is missing for exactly the case it "
                           "covers: a run that started, crashed and was retried.",
    }
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise AuthorizationRefusal("authorisation %r already consumed (%s)." % (aid, p)) from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2, default=str)
    return body
