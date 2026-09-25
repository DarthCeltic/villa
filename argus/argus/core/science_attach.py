"""Attach local science material by hash: no private imports are part of the public ARGUS release.

The operator's build verifies imported science manifests against their own receipts and records what is mounted on the machine. Those
imports are not shipped, so nothing here can be attached, mounted or served: the fixed list of manifests is empty, every plan says it
would be refused, and every view says nothing is imported. The generic helpers (path resolution without traversal, chunked and cached
hashing, file classification, JSON-safe copies) are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from argus.core import actions as A
from argus.core import paths

PLAN_SCHEMA = "argus-science-attach-plan-v1"
RECORD_SCHEMA = "argus-science-attachment-record-v1"
ROOT_ENV = "ARGUS_PRIVATE_SCIENCE_ROOT"
STATE_ENV = "ARGUS_SCIENCE_STATE"
BUDGET_ENV = "ARGUS_SCIENCE_HASH_BUDGET_S"
DEFAULT_BUDGET_S = 20.0
CHUNK = 4 * 1024 * 1024

MOUNTED_VERIFIED = "MOUNTED_VERIFIED"
NOT_MOUNTED = "NOT_MOUNTED"
HASH_MISMATCH = "HASH_MISMATCH"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
UNVERIFIED_TIME_BUDGET = "UNVERIFIED_TIME_BUDGET"
STATUSES = (MOUNTED_VERIFIED, NOT_MOUNTED, HASH_MISMATCH, IDENTITY_MISMATCH, UNVERIFIED_TIME_BUDGET)

IMPORTS: dict = {}

CLOSEOUT_IMPORT = "science_closeout_import"
SEALS_IMPORT = "science_review_seals_import"
ATTACH_FIELDS = frozenset({"manifest", "scroll", "approved_plan_sha256"})

_NO_IMPORTS = "no private science imports are available in this build"
_HASH_CACHE: dict = {}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon_sha(obj) -> str:
    return _sha(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


def sanitize(obj):
    """JSON-safe copy: NaN and infinities become null (browsers cannot parse NaN)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj


def imports_root() -> Path:
    return Path(paths.artifact_write_root())


def state_root() -> Path:
    env = os.environ.get(STATE_ENV)
    return Path(env) if env else paths.argus_home("science")


def private_root() -> Path | None:
    env = os.environ.get(ROOT_ENV)
    return Path(env) if env else None


def hash_budget_s() -> float:
    try:
        return float(os.environ.get(BUDGET_ENV, DEFAULT_BUDGET_S))
    except ValueError:
        return DEFAULT_BUDGET_S


def hash_file(path: Path, deadline: float | None = None) -> str | None:
    """sha256 in chunks, cached by (path, size, mtime). None if the deadline passed mid-file."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    hit = _HASH_CACHE.get(key)
    if hit:
        return hit
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            if deadline is not None and time.monotonic() > deadline:
                return None
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    _HASH_CACHE[key] = h.hexdigest()
    return _HASH_CACHE[key]


def _parts(recorded: str) -> list:
    return [p for p in recorded.replace("\\", "/").split("/") if p]


def candidate_paths(recorded: str, root: Path | None) -> list:
    """Where a recorded private path may be found on this host, and nowhere else.

    With a mount root configured (containers) the recorded path is re-rooted: its drive and first directory are dropped and the
    rest is joined under the root. Without one (native) the recorded absolute path is used as written. A `..` component makes the
    path unusable.
    """
    parts = _parts(recorded)
    if not parts or any(p == ".." for p in parts):
        return []
    if root is not None:
        rest = parts[1:] if parts[0].endswith(":") else parts
        rest = rest[1:]
        return [root.joinpath(*rest)] if rest else []
    lit = Path(recorded)
    return [lit] if lit.is_absolute() else []


def check_file(recorded: str, expected_sha256: str, *, root: Path | None, deadline: float | None) -> dict:
    found = [c for c in candidate_paths(recorded, root) if c.is_file()]
    if not found:
        return {"status": NOT_MOUNTED}
    observed = None
    for c in found:
        observed = hash_file(c, deadline)
        if observed is None:
            return {"status": UNVERIFIED_TIME_BUDGET, "size": c.stat().st_size}
        if observed == expected_sha256:
            return {"status": MOUNTED_VERIFIED, "size": c.stat().st_size, "path": str(c)}
    return {"status": HASH_MISMATCH, "size": found[0].stat().st_size, "observed_sha256": observed}


def verify_import(imp_id: str, root: Path | None = None) -> dict:
    """No imported manifest exists in this build, so none can verify."""
    return {"ok": False, "problems": [_NO_IMPORTS], "dir": None}


def classify(name: str) -> str:
    """Role of one listed file. The operator's render-product naming is not part of the public release, so only a
    layer stack is recognised; everything else is OTHER."""
    if name.endswith(".npz"):
        return "LAYER_STACK"
    return "OTHER"


def _dirname(p: str) -> str:
    i = max(p.rfind("\\"), p.rfind("/"))
    return p[:i] if i >= 0 else ""


def _basename(p: str) -> str:
    return p[max(p.rfind("\\"), p.rfind("/")) + 1:]


def _join(recorded_dir: str, *rel: str) -> str:
    sep = "\\" if "\\" in recorded_dir else "/"
    return recorded_dir.rstrip("\\/") + sep + sep.join(rel)


def _mask_last_dir(path: str) -> str:
    sep = "\\" if "\\" in path else "/"
    parts = path.split(sep)
    if len(parts) >= 2:
        parts[-2] = "<window>"
    return sep.join(parts)


def _refused_plan(body: dict, code: str, why: str) -> tuple:
    body["refusal"] = {"code": code, "why": why}
    body["would_be_refused"] = True
    body["plan_sha256"] = _canon_sha(body)
    return body, {}


def selections(root: Path | None = None) -> list:
    """Every (manifest, scroll) that can be chosen: none, because no import ships in this build."""
    return []


def _plan_full(params: dict, *, root: Path | None = None, registry_path=None, now_utc: str | None = None,
               budget_s: float | None = None) -> tuple:
    manifest, scroll = params.get("manifest"), params.get("scroll")
    body = {"schema": PLAN_SCHEMA, "manifest": manifest, "scroll": scroll,
            "changes": [],
            "cost": {"bytes": "0 copied", "gpu": "none", "seconds": "none"},
            "leases": [], "reversible": True,
            "may_refuse": ["UNKNOWN_MANIFEST", "IMPORT_NOT_VERIFIED", "IDENTITY_REFUSED", "SEAL_INCONSISTENT",
                           "PLAN_NOT_APPROVED"],
            "honesty": _NO_IMPORTS + ", so nothing can be attached; a refusal is the honest answer.",
            "read_only": True}
    return _refused_plan(body, "UNKNOWN_MANIFEST", "%r is not one of the imported manifests %s" % (manifest, sorted(IMPORTS)))


def plan(params: dict, **kw) -> dict:
    return _plan_full(params, **kw)[0]


def attachments_dir() -> Path:
    return state_root() / "attachments"


def records_path() -> Path:
    return attachments_dir() / "ATTACHMENTS.jsonl"


def load_records() -> list:
    p = records_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def execute(params: dict, *, actor: str, root: Path | None = None, registry_path=None,
            now_utc: str | None = None, budget_s: float | None = None) -> dict:
    p = _plan_full({k: v for k, v in params.items() if k in ("manifest", "scroll")})[0]
    raise A.Refused(p["refusal"]["code"], p["refusal"]["why"])


def attachments_state(root: Path | None = None) -> dict:
    return {"schema": "argus-science-attachments-v1", "read_only": True, "selections": [],
            "records": 0, "mount_root_configured": False,
            "note": _NO_IMPORTS + "; nothing can be attached, mounted or served"}


_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def find_by_sha(manifest: str, sha256: str, root: Path | None = None) -> dict:
    return {"found": False, "code": "UNKNOWN_HASH", "why": "not a listed manifest or a sha256"}


def serve_file(manifest: str, sha256: str, *, root: Path | None = None) -> dict:
    """No private file is served in this build: the manifest list is empty."""
    return {"status": 404, "code": "UNKNOWN_HASH", "why": _NO_IMPORTS + ", so no private file can be served"}


def plan_for_action(p: dict) -> dict:
    """The plan both science.attach.plan and science.attach.execute return. A plan never raises: an unusable request is a plan
    that says it would be refused, and why."""
    p = p or {}
    unknown = sorted(set(p) - ATTACH_FIELDS)
    if unknown:
        body = {"schema": PLAN_SCHEMA, "manifest": p.get("manifest"), "scroll": p.get("scroll"),
                "changes": [], "may_refuse": ["UNKNOWN_PARAMETER"], "read_only": True}
        return _refused_plan(body, "UNKNOWN_PARAMETER", "science.attach does not accept %s" % unknown)[0]
    return plan({k: p.get(k) for k in ("manifest", "scroll")})


def do_attach_plan(spec: A.ActionSpec) -> dict:
    return {"status": "OK", "writes": "nothing", "plan": plan_for_action(spec.params)}


def do_attach_execute(spec: A.ActionSpec) -> dict:
    unknown = sorted(set(spec.params) - ATTACH_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "science.attach.execute does not accept %s" % unknown)
    return execute(spec.params, actor=spec.actor)


PLANNERS = {"science.attach.plan": plan_for_action, "science.attach.execute": plan_for_action}
DOERS = {"science.attach.plan": do_attach_plan, "science.attach.execute": do_attach_execute}


def closeout_view(*, root: Path | None = None) -> dict:
    """What the Workbench shows about local closeout material: no import exists in this build, so nothing."""
    return {"schema": "argus-science-closeout-view-v1", "read_only": True,
            "imports": {"closeout": {"id": CLOSEOUT_IMPORT, "verified": False, "problems": [_NO_IMPORTS], "source_commit": None},
                        "seals": {"id": SEALS_IMPORT, "verified": False, "problems": [_NO_IMPORTS], "source_commit": None}},
            "NO_QUALIFIED_DETECTOR": None, "native": None,
            "human_review": {"answers_recorded": 0, "statement": "no human review exists"}}
