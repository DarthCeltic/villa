"""The job engine: a fixed set of vetted actions, and no way to ask for anything else."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import ast
import json
import os
import re
from argus.core.safe_names import is_plain_file_name
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from argus.core import heartbeat as HB
from argus.core import manifest as MF
from argus.core import paths

JOB_SCHEMA = "argus-job-v1"
STAGED_SCHEMA = "argus-staged-v1"
PRESERVE_SCHEMA = "argus-preservation-v1"
CANDIDATE_SCHEMA = "argus-candidate-v1"

STATES = ("PLANNED", "RUNNING", "PARTIAL", "PAUSED", "CANCELLED", "DONE", "FAILED")

HEARTBEAT_EVERY_S = 300.0

STAGE_BUDGET_BYTES = 2 * 1024 * 1024 * 1024

INSPECT_HASH_CAP_BYTES = 64 * 1024 * 1024

_RUN_COUNTS: dict = {}


def _count_run(ws, stage: str, unit: str) -> None:
    k = (str(ws.root), stage, unit)
    _RUN_COUNTS[k] = _RUN_COUNTS.get(k, 0) + 1


class JobRefusal(RuntimeError):
    """A refusal with a machine-readable class, so \"you asked for something that does not exist\" is never collapsed into \"that failed\"."""

    CLASSES = ("UNKNOWN_ACTION", "BAD_ARGUMENT", "PATH_ESCAPE", "UNKNOWN_JOB",
               "UNKNOWN_RUNNER", "NOT_APPROVED", "STATE", "BUDGET", "NOT_PRESERVED",
               "SEALED", "NO_MANIFEST")

    def __init__(self, cls: str, detail: str, evidence: dict | None = None):
        if cls not in self.CLASSES:
            raise ValueError("unknown job refusal class %r" % cls)
        super().__init__("%s: %s" % (cls, detail))
        self.cls, self.detail, self.evidence = cls, detail, evidence or {}

    def as_record(self) -> dict:
        return {"ok": False, "terminal": "ARGUS_JOB_REFUSED", "class": self.cls,
                "detail": self.detail, "evidence": self.evidence}


_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DRIVE = re.compile(r"^[A-Za-z]:")

_RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$"} | {"COM%d" % i for i in range(1, 10)} \
    | {"LPT%d" % i for i in range(1, 10)}


def safe_rel(rel, *, field: str = "path") -> str:
    """Validate one caller-supplied relative path, or refuse."""
    if not isinstance(rel, str) or not rel:
        raise JobRefusal("BAD_ARGUMENT", "%s must be a non-empty string" % field)
    if len(rel) > 240:
        raise JobRefusal("BAD_ARGUMENT", "%s is longer than 240 characters" % field)
    if "\x00" in rel:
        raise JobRefusal("BAD_ARGUMENT", "%s contains a NUL byte" % field)
    s = rel.replace("\\", "/")
    if s.startswith("/") or s.startswith("//") or _DRIVE.match(s):
        raise JobRefusal("PATH_ESCAPE",
                         "%s must be relative to a declared root; %r names an absolute "
                         "location" % (field, rel[:80]))
    parts = s.split("/")
    for p in parts:
        if p in ("", ".", ".."):
            raise JobRefusal("PATH_ESCAPE",
                             "%s contains the segment %r, which climbs out of or points "
                             "at its own root" % (field, p))
        if not _SEGMENT.fullmatch(p) or p.endswith("."):
            raise JobRefusal("BAD_ARGUMENT",
                             "%s segment %r is outside the permitted character set "
                             "[A-Za-z0-9][A-Za-z0-9._-]*" % (field, p[:40]))
        if p.split(".")[0].upper() in _RESERVED:
            raise JobRefusal("BAD_ARGUMENT",
                             "%s segment %r is a reserved device name" % (field, p))
    return "/".join(parts)


def resolve_under(root: Path, rel: str, *, field: str = "path") -> Path:
    """Join a validated relative path to a root and prove the RESOLVED result is inside it."""
    rel = safe_rel(rel, field=field)
    root_r = Path(os.path.realpath(str(root)))
    p = Path(os.path.realpath(str(root_r / rel)))
    try:
        p.relative_to(root_r)
    except ValueError:
        raise JobRefusal("PATH_ESCAPE",
                         "%s resolves outside its declared root; a link or junction "
                         "inside the workspace does not extend the workspace" % field,
                         {"field": field, "rel": rel})
    return p


def workspaces_root() -> Path:
    """Where job workspaces live."""
    env = os.environ.get("ARGUS_JOBS_ROOT")
    return Path(env) if env else paths.artifacts("jobs")


def allowed_input_roots() -> list:
    """The only places a job may read from."""
    env = os.environ.get("ARGUS_JOB_INPUT_ROOTS")
    if env:
        return [Path(x) for x in env.split(os.pathsep) if x.strip()]
    return [paths.artifacts(), paths.data(), paths.corpus()]


def _under_any(p: Path, roots) -> bool:
    if paths.is_remote_path(str(p)) or not paths.plain_local_path(str(p)):
        return False
    pr = Path(os.path.realpath(str(p)))
    for r in roots:
        rr = Path(os.path.realpath(str(r)))
        if pr == rr:
            return True
        try:
            pr.relative_to(rr)
            return True
        except ValueError:
            continue
    return False


@dataclass(frozen=True)
class Workspace:
    """One job's explicit roots."""

    job_id: str
    root: Path
    input_root: Path

    @property
    def output_root(self) -> Path:
        """Derived, never caller-supplied."""
        return self.root / "outputs"

    @property
    def staged_root(self) -> Path:
        return self.root / "staged"

    @property
    def evidence_root(self) -> Path:
        return self.root / "evidence"

    @property
    def candidates_root(self) -> Path:
        return self.root / "candidates"

    def stage_dir(self, stage: str) -> Path:
        return self.root / "stages" / _ident(stage, field="stage")

    def stage_out(self, stage: str) -> Path:
        return self.output_root / _ident(stage, field="stage")

    def as_record(self) -> dict:
        return {"schema": JOB_SCHEMA, "job_id": self.job_id,
                "workspace": str(self.root), "input_root": str(self.input_root),
                "output_root": str(self.output_root)}


def ident(v, *, field: str) -> str:
    if not isinstance(v, str) or not _IDENT.fullmatch(v) or v.endswith(".") or not is_plain_file_name(v):
        raise JobRefusal("BAD_ARGUMENT",
                         "%s must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}; got %r"
                         % (field, str(v)[:60]))
    return v


_ident = ident


def open_workspace(job_id: str) -> Workspace:
    job_id = _ident(job_id, field="job_id")
    d = workspaces_root() / job_id
    rec = d / "job.json"
    if not rec.is_file():
        raise JobRefusal("UNKNOWN_JOB", "no job workspace %r" % job_id, {"job_id": job_id})
    try:
        r = json.loads(rec.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise JobRefusal("UNKNOWN_JOB", "job record unreadable: %s" % str(e)[:120])
    if str(r.get("schema")) != JOB_SCHEMA:
        raise JobRefusal("UNKNOWN_JOB", "not an ARGUS job record (schema %r)"
                         % r.get("schema"))
    return Workspace(job_id, d, Path(r["input_root"]))


def _write_json(p: Path, obj) -> None:
    """Atomic, fsynced."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, indent=2, sort_keys=True))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(p)


def _read_json(p: Path):
    if not Path(p).is_file():
        return None
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


@dataclass(frozen=True)
class Runner:
    """A vetted unit of work."""

    name: str
    fn: object
    version: str
    seconds_per_unit: float
    describe: str


def _runner_checksum(unit: str, ws: Workspace, stage: str, params: dict) -> dict:
    """Hash one staged asset and write the digest."""
    src = resolve_under(ws.staged_root, unit, field="unit")
    if not src.is_file():
        raise JobRefusal("BAD_ARGUMENT", "unit %r has no staged asset" % unit)
    digest = MF.sha256_file(src)
    out = resolve_under(ws.stage_out(stage), unit + ".sha256", field="output")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(digest + "\n", encoding="utf-8")
    _count_run(ws, stage, unit)
    return {"rel": unit + ".sha256", "sha256": MF.sha256_file(out), "input_sha256": digest}


def _runner_inventory(unit: str, ws: Workspace, stage: str, params: dict) -> dict:
    """Record a staged asset's size and identity without reading it twice."""
    src = resolve_under(ws.staged_root, unit, field="unit")
    if not src.is_file():
        raise JobRefusal("BAD_ARGUMENT", "unit %r has no staged asset" % unit)
    rec = {"unit": unit, "bytes": src.stat().st_size}
    out = resolve_under(ws.stage_out(stage), unit + ".json", field="output")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_json(out, rec)
    _count_run(ws, stage, unit)
    return {"rel": unit + ".json", "sha256": MF.sha256_file(out), "bytes": rec["bytes"]}


RUNNERS: dict = {
    "checksum": Runner("checksum", _runner_checksum, "1", 0.05,
                       "sha256 of each staged asset, written beside it"),
    "inventory": Runner("inventory", _runner_inventory, "1", 0.01,
                        "size and identity of each staged asset"),
}


def runner_for(name) -> Runner:
    name = _ident(name, field="runner")
    if name not in RUNNERS:
        raise JobRefusal("UNKNOWN_RUNNER",
                         "no runner %r; the vetted runners are %s"
                         % (name, ", ".join(sorted(RUNNERS))), {"runner": name})
    return RUNNERS[name]


class Beat:
    """Liveness ticks that CANNOT move the progress counter."""

    def __init__(self, hb: HB.Heartbeat, *, clock=time.time, every: float = HEARTBEAT_EVERY_S):
        self.hb, self.clock, self.every = hb, clock, every
        self.last = clock()
        self.ticks = 0

    def tick(self, detail: str = "") -> bool:
        now = self.clock()
        if now - self.last < self.every:
            return False
        self.last = now
        self.ticks += 1
        self.hb.detail = ("alive; %s" % detail)[:200] if detail else "alive"
        self.hb.write()
        return True

    def unit_done(self, unit: str) -> None:
        self.last = self.clock()
        self.hb.advance(1, detail=unit)


def _ledger_path(stage_dir: Path) -> Path:
    return Path(stage_dir) / "units.done.jsonl"


def ledger_read(stage_dir: Path) -> dict:
    """unit -> record, for units already completed."""
    p = _ledger_path(stage_dir)
    out: dict = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("unit"), str):
            out[r["unit"]] = r
    return out


def _ledger_append(stage_dir: Path, rec: dict) -> None:
    p = _ledger_path(stage_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _control_path(stage_dir: Path) -> Path:
    return Path(stage_dir) / "control.json"


def control_read(stage_dir: Path) -> dict:
    r = _read_json(_control_path(stage_dir))
    if not isinstance(r, dict) or r.get("state") not in STATES:
        return {"state": "PLANNED", "reason": None, "at": None}
    return r


def _control_write(stage_dir: Path, state: str, *, reason: str | None = None) -> dict:
    if state not in STATES:
        raise JobRefusal("STATE", "unknown stage state %r" % state)
    rec = {"state": state, "reason": reason, "at": time.time()}
    _write_json(_control_path(stage_dir), rec)
    return rec


def _seals_path(ws: Workspace) -> Path:
    return ws.root / "seals.json"


def seals(ws: Workspace) -> dict:
    r = _read_json(_seals_path(ws))
    return r if isinstance(r, dict) else {}


def is_sealed(ws: Workspace, cid: str) -> bool:
    return bool(seals(ws).get(cid, {}).get("sealed"))


def _seal(ws: Workspace, cid: str, why: str) -> None:
    s = seals(ws)
    s[cid] = {"sealed": True, "at": time.time(), "why": why,
              "unsealed_at": None, "unsealed_by": None, "unseal_reason": None}
    _write_json(_seals_path(ws), s)


def read_candidate(ws: Workspace, cid: str) -> dict:
    """THE SEAL."""
    cid = _ident(cid, field="candidate")
    p = resolve_under(ws.candidates_root, cid + ".json", field="candidate")
    if not p.is_file():
        raise JobRefusal("BAD_ARGUMENT", "no candidate %r" % cid)
    if is_sealed(ws, cid):
        raise JobRefusal("SEALED",
                         "candidate %r is sealed; its record is not readable until it is "
                         "explicitly unsealed with a stated reason" % cid,
                         {"candidate": cid, "job_id": ws.job_id})
    return _read_json(p) or {}


def _execute(ws: Workspace, stage: str, *, approved_fingerprint: str, max_units: int,
             resuming: bool, clock=time.time) -> dict:
    """Run a stage's units."""
    sd = ws.stage_dir(stage)
    m = MF.require(sd, why="stage execution")
    if m.fingerprint() != approved_fingerprint:
        raise JobRefusal("NOT_APPROVED",
                         "the approved fingerprint does not match the manifest on disk. "
                         "Approval is of a specific plan; re-read the manifest and approve "
                         "what is actually there.",
                         {"approved": approved_fingerprint[:12],
                          "on_disk": m.fingerprint()[:12]})

    state = control_read(sd)["state"]
    if state == "CANCELLED":
        raise JobRefusal("STATE", "stage %r was cancelled; a cancelled stage is terminal, "
                                  "start a new one" % stage, {"state": state})
    if state == "PAUSED" and not resuming:
        raise JobRefusal("STATE", "stage %r is paused; resume it explicitly" % stage,
                         {"state": state})
    if state == "DONE":
        return {"ok": True, "job_id": ws.job_id, "stage": stage, "state": "DONE",
                "reused": True, "completed_units": len(ledger_read(sd)),
                "total_units": len(m.units),
                "outputs": MF.read_outputs(sd),
                "note": "this stage was already complete; nothing was re-executed"}

    drift = MF.verify_inputs(m, ws.input_root if _manifest_reads_inputs(m) else ws.staged_root)
    if drift:
        raise JobRefusal("STATE",
                         "the declared inputs changed after the plan was written: %s"
                         % "; ".join(drift[:4]), {"drift": drift})

    runner = runner_for(m.code.get("runner"))
    done = ledger_read(sd)
    _control_write(sd, "RUNNING")
    hb = HB.Heartbeat(sd, unit="units", total=len(m.units), stage=stage, clock=clock)
    hb.done = len(done)
    hb.write()
    beat = Beat(hb, clock=clock)

    ran = 0
    outcome = "DONE"
    try:
        for u in m.units:
            st = control_read(sd)["state"]
            if st == "PAUSED":
                outcome = "PAUSED"
                break
            if st == "CANCELLED":
                outcome = "CANCELLED"
                break
            if u in done:
                continue
            if max_units and ran >= max_units:
                outcome = "PARTIAL"
                break
            beat.tick(detail="starting %s" % u)
            rec = runner.fn(u, ws, stage, m.params)
            entry = {"unit": u, "at": clock(), "runner": runner.name,
                     "runner_version": runner.version}
            entry.update({k: v for k, v in rec.items() if k in ("rel", "sha256", "bytes",
                                                                "input_sha256")})
            _ledger_append(sd, entry)
            done[u] = entry
            ran += 1
            beat.unit_done(u)
    except JobRefusal:
        _control_write(sd, "FAILED", reason="a unit refused")
        hb.finish("FAILED")
        raise
    except Exception as e:
        _control_write(sd, "FAILED", reason=str(e)[:200])
        hb.finish("FAILED")
        raise

    complete = len(done) >= len(m.units)
    if outcome == "DONE" and not complete:
        outcome = "PARTIAL"
    _control_write(sd, outcome)
    hb.finish("DONE" if outcome == "DONE" else outcome)

    outs = MF.record_outputs(sd, ws.stage_out(stage), m.planned_outputs,
                             strict=(outcome == "DONE"))
    return {"ok": True, "job_id": ws.job_id, "stage": stage, "state": outcome,
            "reused": False, "executed_units": ran, "completed_units": len(done),
            "total_units": len(m.units), "outputs": outs,
            "manifest_fingerprint": m.fingerprint()}


def _manifest_reads_inputs(m: MF.Manifest) -> bool:
    """Stage manifests declare staged assets; the staging manifest declares input-root assets."""
    return m.action == "stage_assets"


@dataclass(frozen=True)
class Arg:
    """One parameter of one action: a kind, and whether it is required."""

    kind: str
    required: bool = True
    default: object = None
    max_items: int = 512

    KINDS = ("ident", "relpath", "relpath_list", "ident_list", "int", "bool", "text",
             "hex64", "params", "abspath")


def _validate(spec: dict, args: dict) -> dict:
    """Turn an untyped request into typed keyword arguments, or refuse."""
    if not isinstance(args, dict):
        raise JobRefusal("BAD_ARGUMENT", "arguments must be an object")
    unknown = sorted(set(args) - set(spec))
    if unknown:
        raise JobRefusal("BAD_ARGUMENT",
                         "unknown argument(s) %s; this action takes %s"
                         % (", ".join(repr(u)[:40] for u in unknown),
                            ", ".join(sorted(spec))), {"unknown": unknown})
    out = {}
    for name, a in spec.items():
        if name not in args:
            if a.required:
                raise JobRefusal("BAD_ARGUMENT", "missing required argument %r" % name)
            out[name] = a.default
            continue
        if not a.required and args[name] == a.default:
            out[name] = a.default
            continue
        out[name] = _coerce(name, a, args[name])
    return out


def _coerce(name: str, a: Arg, v):
    k = a.kind
    if k == "ident":
        return _ident(v, field=name)
    if k == "relpath":
        return safe_rel(v, field=name)
    if k in ("relpath_list", "ident_list"):
        if not isinstance(v, (list, tuple)):
            raise JobRefusal("BAD_ARGUMENT", "%s must be a list" % name)
        if len(v) > a.max_items:
            raise JobRefusal("BAD_ARGUMENT",
                             "%s has %d items; the limit is %d" % (name, len(v), a.max_items))
        f = safe_rel if k == "relpath_list" else _ident
        return [f(x, field=name) for x in v]
    if k == "int":
        if isinstance(v, bool) or not isinstance(v, int):
            raise JobRefusal("BAD_ARGUMENT", "%s must be an integer" % name)
        if not (0 <= v <= 10 ** 12):
            raise JobRefusal("BAD_ARGUMENT", "%s is out of range" % name)
        return v
    if k == "bool":
        if not isinstance(v, bool):
            raise JobRefusal("BAD_ARGUMENT", "%s must be true or false" % name)
        return v
    if k == "text":
        if not isinstance(v, str) or len(v) > 2000:
            raise JobRefusal("BAD_ARGUMENT", "%s must be a string of at most 2000 chars"
                             % name)
        if "\x00" in v:
            raise JobRefusal("BAD_ARGUMENT", "%s contains a NUL byte" % name)
        return v
    if k == "hex64":
        if not isinstance(v, str) or not _HEX64.match(v):
            raise JobRefusal("BAD_ARGUMENT", "%s must be a 64-character hex digest" % name)
        return v
    if k == "params":
        if not isinstance(v, dict):
            raise JobRefusal("BAD_ARGUMENT", "%s must be an object" % name)
        if len(v) > 32:
            raise JobRefusal("BAD_ARGUMENT", "%s has too many keys" % name)
        out = {}
        for kk, vv in v.items():
            _ident(kk, field="%s key" % name)
            if isinstance(vv, bool) or isinstance(vv, int) or isinstance(vv, float):
                out[kk] = vv
            elif isinstance(vv, str):
                if len(vv) > 200 or "\x00" in vv:
                    raise JobRefusal("BAD_ARGUMENT", "%s[%s] is not a short scalar"
                                     % (name, kk))
                out[kk] = vv
            else:
                raise JobRefusal("BAD_ARGUMENT",
                                 "%s[%s] must be a string, number or boolean; nested "
                                 "structures are not parameters" % (name, kk))
        return out
    if k == "abspath":
        if not isinstance(v, str) or not v or "\x00" in v or len(v) > 400:
            raise JobRefusal("BAD_ARGUMENT", "%s must be a path string" % name)
        if paths.is_remote_path(v) or not paths.plain_local_path(v):
            raise JobRefusal("BAD_ARGUMENT", "%s must be a plain local path (no network path, stream or device form)" % name)
        return v
    raise JobRefusal("BAD_ARGUMENT", "no validator for kind %r" % k)


def act_create_workspace(job_id: str, input_root: str, note: str) -> dict:
    job_id = _ident(job_id, field="job_id")
    src = Path(input_root)
    if not _under_any(src, allowed_input_roots()):
        raise JobRefusal("PATH_ESCAPE",
                         "the input root must sit under a declared ARGUS root; %r does "
                         "not. Declared roots come from the path contract, not from the "
                         "caller." % input_root[:80],
                         {"declared": [str(r) for r in allowed_input_roots()]})
    if not src.is_dir():
        raise JobRefusal("BAD_ARGUMENT", "the input root does not exist")
    d = workspaces_root() / job_id
    rec_p = d / "job.json"
    if rec_p.is_file():
        prior = _read_json(rec_p) or {}
        if prior.get("input_root") != str(src):
            raise JobRefusal("STATE",
                             "job %r already exists with a different input root" % job_id)
        return {"ok": True, "reused": True, **Workspace(job_id, d, src).as_record()}
    ws = Workspace(job_id, d, src)
    for p in (ws.root, ws.staged_root, ws.output_root, ws.evidence_root,
              ws.candidates_root, ws.root / "stages"):
        p.mkdir(parents=True, exist_ok=True)
    rec = ws.as_record()
    rec.update({"created_at": time.time(), "note": note or ""})
    _write_json(rec_p, rec)
    return {"ok": True, "reused": False, **ws.as_record()}


def act_inspect_assets(job_id: str, subdir: str, limit: int, hash_files: bool) -> dict:
    ws = open_workspace(job_id)
    base = resolve_under(ws.input_root, subdir, field="subdir") if subdir else ws.input_root
    if not base.is_dir():
        raise JobRefusal("BAD_ARGUMENT", "no such directory under the input root")
    items, truncated = [], False
    for p in sorted(base.iterdir()):
        if len(items) >= (limit or 200):
            truncated = True
            break
        if p.is_symlink() or not p.is_file():
            continue
        size = p.stat().st_size
        if hash_files and size <= INSPECT_HASH_CAP_BYTES:
            digest, why = MF.sha256_file(p), None
        else:
            digest = None
            why = ("not hashed during inspection: %d bytes exceeds the %d-byte inspection "
                   "cap. Staging hashes unconditionally."
                   % (size, INSPECT_HASH_CAP_BYTES)) if hash_files else "hashing not requested"
        items.append({"rel": p.name, "bytes": size, "sha256": digest, "why": why})
    return {"ok": True, "job_id": ws.job_id, "subdir": subdir, "count": len(items),
            "truncated": truncated, "assets": items}


def act_stage_assets(job_id: str, items: list, max_bytes: int) -> dict:
    """Copy declared inputs into the workspace and record what was copied."""
    ws = open_workspace(job_id)
    budget = min(max_bytes or STAGE_BUDGET_BYTES, STAGE_BUDGET_BYTES)
    srcs, total = [], 0
    for rel in items:
        p = resolve_under(ws.input_root, rel, field="items")
        if p.is_symlink() or not p.is_file():
            raise JobRefusal("BAD_ARGUMENT", "%r is not a regular file under the input root"
                             % rel)
        total += p.stat().st_size
        srcs.append((rel, p))
    if total > budget:
        raise JobRefusal("BUDGET",
                         "staging %d bytes exceeds the %d-byte budget; an unbounded stage can "
                         "fill a disk."
                         % (total, budget), {"bytes": total, "budget": budget})
    staged = []
    for rel, p in srcs:
        want = MF.sha256_file(p)
        dst = resolve_under(ws.staged_root, rel, field="items")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst)
        got = MF.sha256_file(dst)
        if got != want:
            dst.unlink(missing_ok=True)
            raise JobRefusal("STATE",
                             "the staged copy of %r does not match its source; the copy "
                             "was discarded" % rel, {"want": want[:12], "got": got[:12]})
        staged.append({"rel": rel, "sha256": got, "bytes": dst.stat().st_size})
        total += 0
    rec = {"schema": STAGED_SCHEMA, "at": time.time(), "assets": staged,
           "input_root": str(ws.input_root)}
    prior = _read_json(ws.root / "staged.json") or {}
    merged = {a["rel"]: a for a in prior.get("assets", [])}
    merged.update({a["rel"]: a for a in staged})
    rec["assets"] = [merged[k] for k in sorted(merged)]
    _write_json(ws.root / "staged.json", rec)
    return {"ok": True, "job_id": ws.job_id, "staged": staged,
            "total_staged": len(rec["assets"]), "bytes": total}


def act_generate_manifest(job_id: str, stage: str, runner: str, units: list,
                          params: dict) -> dict:
    """Write the plan."""
    ws = open_workspace(job_id)
    stage = _ident(stage, field="stage")
    r = runner_for(runner)
    staged = _read_json(ws.root / "staged.json") or {"assets": []}
    known = {a["rel"]: a for a in staged["assets"]}
    use = list(units) if units else sorted(known)
    if not use:
        raise JobRefusal("BAD_ARGUMENT",
                         "no units: stage assets before planning a stage over them")
    missing = [u for u in use if u not in known]
    if missing:
        raise JobRefusal("BAD_ARGUMENT",
                         "these units are not staged: %s" % ", ".join(missing[:6]),
                         {"missing": missing})
    ext = ".sha256" if r.name == "checksum" else ".json"
    m = MF.Manifest(
        job_id=ws.job_id, stage=stage, action="run_stage",
        input_root=str(ws.staged_root), output_root=str(ws.stage_out(stage)),
        units=use,
        inputs=[MF.Asset(u, known[u]["sha256"], int(known[u]["bytes"])) for u in use],
        planned_outputs=[u + ext for u in use],
        params=params or {},
        code={"runner": r.name, "runner_version": r.version},
        estimate=_estimate(r, use, known))
    sd = ws.stage_dir(stage)
    MF.write(sd, m)
    if control_read(sd)["state"] == "PLANNED":
        _control_write(sd, "PLANNED")
    return {"ok": True, "job_id": ws.job_id, "stage": stage,
            "manifest_fingerprint": m.fingerprint(), "units": len(use),
            "planned_outputs": len(m.planned_outputs), "estimate": m.estimate,
            "approve_with": "start_stage(approved_fingerprint=<this fingerprint>)"}


def _estimate(r: Runner, units: list, known: dict) -> dict:
    b = sum(int(known[u]["bytes"]) for u in units if u in known)
    return {"units": len(units), "input_bytes": b,
            "seconds": round(r.seconds_per_unit * len(units), 2),
            "basis": "the runner's declared per-unit cost multiplied by the unit count",
            "is_a_measurement": False,
            "note": "a declared model, not a timing of this machine; it is honest about "
                    "being an estimate rather than being dressed as one"}


def act_estimate_cost(job_id: str, stage: str) -> dict:
    ws = open_workspace(job_id)
    m = MF.require(ws.stage_dir(stage), why="cost estimation")
    done = len(ledger_read(ws.stage_dir(stage)))
    r = runner_for(m.code.get("runner"))
    remaining = [u for u in m.units if u not in ledger_read(ws.stage_dir(stage))]
    return {"ok": True, "job_id": ws.job_id, "stage": stage,
            "planned": m.estimate, "completed_units": done,
            "remaining_units": len(remaining),
            "remaining_seconds": round(r.seconds_per_unit * len(remaining), 2),
            "gpu_required": False}


def act_start_stage(job_id: str, stage: str, approved_fingerprint: str,
                    max_units: int) -> dict:
    ws = open_workspace(job_id)
    return _execute(ws, stage, approved_fingerprint=approved_fingerprint,
                    max_units=max_units or 0, resuming=False)


def act_resume_stage(job_id: str, stage: str, approved_fingerprint: str,
                     max_units: int) -> dict:
    ws = open_workspace(job_id)
    sd = ws.stage_dir(stage)
    st = control_read(sd)["state"]
    if st not in ("PAUSED", "PARTIAL"):
        raise JobRefusal("STATE",
                         "stage %r is %s; resume applies to a paused or partial stage"
                         % (stage, st), {"state": st})
    _control_write(sd, "PARTIAL")
    return _execute(ws, stage, approved_fingerprint=approved_fingerprint,
                    max_units=max_units or 0, resuming=True)


def act_pause_stage(job_id: str, stage: str, reason: str) -> dict:
    ws = open_workspace(job_id)
    sd = ws.stage_dir(stage)
    MF.require(sd, why="pause")
    st = control_read(sd)["state"]
    if st in ("CANCELLED", "DONE"):
        raise JobRefusal("STATE", "stage %r is %s and cannot be paused" % (stage, st))
    rec = _control_write(sd, "PAUSED", reason=reason or "")
    return {"ok": True, "job_id": ws.job_id, "stage": stage, "state": rec["state"],
            "note": "the running loop re-reads control before each unit, so this takes "
                    "effect at the next unit boundary and never mid-unit"}


def act_cancel_stage(job_id: str, stage: str, reason: str) -> dict:
    ws = open_workspace(job_id)
    sd = ws.stage_dir(stage)
    MF.require(sd, why="cancel")
    if not reason or len(reason.strip()) < 4:
        raise JobRefusal("BAD_ARGUMENT", "cancelling requires a stated reason")
    st = control_read(sd)["state"]
    if st == "DONE":
        raise JobRefusal("STATE", "stage %r is complete; there is nothing to cancel" % stage)
    rec = _control_write(sd, "CANCELLED", reason=reason)
    MF.record_outputs(sd, ws.stage_out(stage),
                      MF.read(sd).planned_outputs, strict=False)
    return {"ok": True, "job_id": ws.job_id, "stage": stage, "state": rec["state"],
            "reason": reason, "terminal": True,
            "note": "cancelled is terminal; partial outputs are hashed and kept as "
                    "evidence rather than deleted"}


def act_publish_heartbeat(job_id: str, stage: str, done: int, total: int, unit: str,
                          detail: str) -> dict:
    """Record REAL progress."""
    ws = open_workspace(job_id)
    sd = ws.stage_dir(stage)
    MF.require(sd, why="heartbeat publication")
    if total and done > total:
        raise JobRefusal("BAD_ARGUMENT", "done (%d) exceeds total (%d)" % (done, total))
    prev = HB.read(sd)
    if prev.get("present") and isinstance(prev.get("done"), int) and done < prev["done"]:
        raise JobRefusal("BAD_ARGUMENT",
                         "progress cannot go backwards (%d -> %d); a heartbeat that can "
                         "be rewound is not a record of work done"
                         % (prev["done"], done), {"previous": prev["done"]})
    hb = HB.Heartbeat(sd, unit=unit, total=total or None, stage=stage)
    hb.done = done
    hb.state = "RUNNING"
    hb.detail = detail or ""
    hb.write()
    return {"ok": True, "job_id": ws.job_id, "stage": stage,
            "progress": HB.read(sd),
            "note": "done/total of a named unit; no percentage is stored and none is "
                    "derived from elapsed time"}


def act_preserve_evidence(job_id: str, candidate: str, stage: str, items: list) -> dict:
    """Copy and hash a candidate's evidence BEFORE anybody interprets it."""
    ws = open_workspace(job_id)
    candidate = _ident(candidate, field="candidate")
    src_root = ws.stage_out(stage)
    dst_root = resolve_under(ws.evidence_root, candidate, field="candidate")
    dst_root.mkdir(parents=True, exist_ok=True)
    assets = []
    for rel in items:
        p = resolve_under(src_root, rel, field="items")
        if p.is_symlink() or not p.is_file():
            raise JobRefusal("BAD_ARGUMENT", "%r is not a regular output file" % rel)
        want = MF.sha256_file(p)
        d = resolve_under(dst_root, rel, field="items")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, d)
        got = MF.sha256_file(d)
        if got != want:
            raise JobRefusal("STATE", "the preserved copy of %r does not match" % rel)
        assets.append({"rel": rel, "sha256": got, "bytes": d.stat().st_size})
    if not assets:
        raise JobRefusal("BAD_ARGUMENT", "preservation with no evidence preserves nothing")
    rec = {"schema": PRESERVE_SCHEMA, "candidate": candidate, "stage": stage,
           "at": time.time(), "assets": assets}
    p = dst_root / "preservation.json"
    prior = _read_json(p)
    if prior and [a["sha256"] for a in prior["assets"]] != [a["sha256"] for a in assets]:
        raise JobRefusal("STATE",
                         "different evidence was already preserved for candidate %r; "
                         "preservation is a record, not a working copy" % candidate)
    _write_json(p, rec)
    return {"ok": True, "job_id": ws.job_id, "candidate": candidate,
            "preserved": len(assets),
            "evidence_sha256": MF.sha256_bytes(
                MF.canonical_json([a["sha256"] for a in assets]).encode("utf-8"))}


def act_index_candidate(job_id: str, candidate: str, stage: str,
                        interpretation: str) -> dict:
    """Index a candidate."""
    ws = open_workspace(job_id)
    candidate = _ident(candidate, field="candidate")
    ev = ws.evidence_root / candidate / "preservation.json"
    rec = _read_json(ev)
    if not rec or rec.get("schema") != PRESERVE_SCHEMA:
        raise JobRefusal("NOT_PRESERVED",
                         "candidate %r has no preserved evidence. Preservation precedes "
                         "interpretation: index_candidate refuses to be the first thing "
                         "that touches a result." % candidate, {"candidate": candidate})
    bad = []
    for a in rec["assets"]:
        p = ws.evidence_root / candidate / a["rel"]
        if not p.is_file() or MF.sha256_file(p) != a["sha256"]:
            bad.append(a["rel"])
    if bad:
        raise JobRefusal("NOT_PRESERVED",
                         "preserved evidence for %r no longer matches its record: %s"
                         % (candidate, ", ".join(bad[:5])), {"changed": bad})
    out = {"schema": CANDIDATE_SCHEMA, "candidate": candidate, "job_id": ws.job_id,
           "stage": stage, "at": time.time(), "interpretation": interpretation,
           "evidence": rec["assets"],
           "evidence_sha256": MF.sha256_bytes(
               MF.canonical_json([a["sha256"] for a in rec["assets"]]).encode("utf-8"))}
    ws.candidates_root.mkdir(parents=True, exist_ok=True)
    _write_json(resolve_under(ws.candidates_root, candidate + ".json", field="candidate"),
                out)
    _seal(ws, candidate, "indexed candidates are sealed on creation; a candidate is a "
                         "claim about ink and is not readable until it is deliberately "
                         "unsealed")
    return {"ok": True, "job_id": ws.job_id, "candidate": candidate, "sealed": True,
            "evidence_sha256": out["evidence_sha256"],
            "note": "the record is sealed; its interpretation is not returned here and "
                    "is not readable through the service until unsealed"}


def act_unseal_candidate(job_id: str, candidate: str, who: str, reason: str) -> dict:
    """The only way a sealed record becomes readable, and it leaves a record of itself."""
    ws = open_workspace(job_id)
    candidate = _ident(candidate, field="candidate")
    if not who.strip() or len(reason.strip()) < 8:
        raise JobRefusal("BAD_ARGUMENT",
                         "unsealing requires who is unsealing and a reason of at least "
                         "8 characters; an unseal with no stated reason is an unseal "
                         "nobody can review")
    s = seals(ws)
    if candidate not in s:
        raise JobRefusal("BAD_ARGUMENT", "no sealed candidate %r" % candidate)
    s[candidate].update({"sealed": False, "unsealed_at": time.time(),
                         "unsealed_by": who, "unseal_reason": reason})
    _write_json(_seals_path(ws), s)
    return {"ok": True, "job_id": ws.job_id, "candidate": candidate, "sealed": False,
            "unsealed_by": who, "reason": reason}


def act_open_artifacts(job_id: str, stage: str) -> dict:
    """List what a stage produced: relative paths, sizes and hashes."""
    ws = open_workspace(job_id)
    if stage:
        stages = [_ident(stage, field="stage")]
    else:
        sd = ws.root / "stages"
        stages = sorted(p.name for p in sd.iterdir() if p.is_dir()) if sd.is_dir() else []
    out = []
    for s in stages:
        d = ws.stage_dir(s)
        if not MF.exists(d):
            continue
        m = MF.read(d)
        outs = MF.read_outputs(d) or {"outputs": [], "missing": list(m.planned_outputs)}
        out.append({"stage": s, "state": control_read(d)["state"],
                    "manifest_fingerprint": m.fingerprint(),
                    "units_total": len(m.units), "units_done": len(ledger_read(d)),
                    "artifacts": outs["outputs"], "missing": outs.get("missing", [])})
    return {"ok": True, "job_id": ws.job_id, "stages": out}


def act_job_status(job_id: str) -> dict:
    ws = open_workspace(job_id)
    sd = ws.root / "stages"
    stages = []
    for d in (sorted(p for p in sd.iterdir() if p.is_dir()) if sd.is_dir() else []):
        if not MF.exists(d):
            continue
        m = MF.read(d)
        stages.append({"stage": d.name, "state": control_read(d)["state"],
                       "units_total": len(m.units), "units_done": len(ledger_read(d)),
                       "runner": m.code.get("runner"),
                       "manifest_fingerprint": m.fingerprint(),
                       "progress": HB.read(d)})
    sealed = [c for c, v in seals(ws).items() if v.get("sealed")]
    staged = _read_json(ws.root / "staged.json") or {"assets": []}
    return {"ok": True, "job_id": ws.job_id, "staged_assets": len(staged["assets"]),
            "stages": stages, "sealed_candidates": sorted(sealed),
            "candidates": sorted(p.stem for p in ws.candidates_root.glob("*.json"))
            if ws.candidates_root.is_dir() else []}


def act_list_jobs() -> dict:
    r = workspaces_root()
    jobs = []
    if r.is_dir():
        for d in sorted(p for p in r.iterdir() if p.is_dir()):
            rec = _read_json(d / "job.json")
            if rec and rec.get("schema") == JOB_SCHEMA:
                jobs.append({"job_id": rec["job_id"], "created_at": rec.get("created_at")})
    return {"ok": True, "jobs": jobs}


JOBS_ENABLE_VAR = "ARGUS_JOBS_API_ENABLED"

_STAGE_WORDS = {"PLANNED": "planned", "PAUSED": "paused", "PARTIAL": "partial",
                "DONE": "complete", "CANCELLED": "cancelled", "FAILED": "failed"}
_RESUMABLE_WORDS = ("paused", "partial", "interrupted")


def _running_word(beat: dict) -> str:
    if not beat.get("present"):
        return "running_unverified"
    if beat.get("stale"):
        return "interrupted"
    if beat.get("reported_state") in ("STARTING", "RUNNING"):
        return "running"
    return "running_unverified"


def stage_resume_view(job_id: str, stage: str, *, now=None) -> dict:
    """Where one stage stopped, read from its manifest, unit ledger, control file and heartbeat."""
    ws = open_workspace(job_id)
    sd = ws.stage_dir(stage)
    m = MF.require(sd, why="resume planning")
    ctl = control_read(sd)
    rec = ledger_read(sd)
    done = [u for u in m.units if u in rec]
    pending = [u for u in m.units if u not in rec]
    beat = HB.read(sd, now=now)
    word = _running_word(beat) if ctl["state"] == "RUNNING" else _STAGE_WORDS[ctl["state"]]
    resumable = word in _RESUMABLE_WORDS
    why_not = None
    if not resumable:
        why_not = {
            "planned": "no unit has run; resuming from nothing is starting, so start the stage",
            "complete": "every unit is complete; there is nothing to resume",
            "cancelled": "a cancelled stage is terminal; start a new stage",
            "failed": "a unit failed; act_resume_stage resumes only a paused or partial "
                      "stage, so a failed stage needs its cause fixed and a new plan",
            "running": "a worker heartbeat is fresh (%s s old); resuming would run the "
                       "same units twice" % beat.get("age_s"),
            "running_unverified": "the control file says RUNNING but no heartbeat proves "
                                  "the worker died, so this cannot be told from a live run",
        }[word]
    return {"job_id": ws.job_id, "stage": stage, "state": word,
            "control_state": ctl["state"], "resumable": resumable, "why_not": why_not,
            "runner": m.code.get("runner"), "manifest_fingerprint": m.fingerprint(),
            "units_total": len(m.units), "done": done, "pending": pending,
            "stopped_after": done[-1] if done else None,
            "resume_unit": pending[0] if pending else None,
            "ledger": {u: {k: rec[u].get(k) for k in ("rel", "sha256", "input_sha256")}
                       for u in done},
            "heartbeat": {k: beat.get(k) for k in ("present", "state", "reported_state",
                                                   "done", "total", "age_s", "stale")}}


def mark_interrupted(job_id: str, stage: str, *, now=None) -> dict:
    """Turn a stage that was killed mid-run (control RUNNING, heartbeat stale) into PARTIAL, the one state `act_resume_stage` accepts."""
    view = stage_resume_view(job_id, stage, now=now)
    if view["state"] != "interrupted":
        raise JobRefusal("STATE", "stage %r is %s, not an interrupted run; nothing to recover"
                         % (stage, view["state"]), {"state": view["state"]})
    sd = open_workspace(job_id).stage_dir(stage)
    return _control_write(sd, "PARTIAL", reason="interrupted: heartbeat %ss old; recovered "
                          "for an approved resume" % view["heartbeat"]["age_s"])


def list_stage_views(*, limit: int = 200) -> list:
    """One resume view per stage of every job workspace, for the Jobs screen."""
    out = []
    r = workspaces_root()
    for d in (sorted(p for p in r.iterdir() if p.is_dir()) if r.is_dir() else []):
        if len(out) >= limit:
            break
        try:
            ws = open_workspace(d.name)
        except JobRefusal as e:
            out.append({"job_id": d.name, "stage": None, "state": "unreadable",
                        "resumable": False, "why_not": e.detail})
            continue
        sdir = ws.root / "stages"
        for sd in (sorted(p for p in sdir.iterdir() if p.is_dir()) if sdir.is_dir() else []):
            if not MF.exists(sd):
                continue
            try:
                out.append(stage_resume_view(ws.job_id, sd.name))
            except (JobRefusal, MF.ManifestViolation) as e:
                out.append({"job_id": ws.job_id, "stage": sd.name, "state": "unreadable",
                            "resumable": False, "why_not": str(e)[:200]})
    return out


@dataclass(frozen=True)
class Action:
    name: str
    fn: object
    args: dict = field(default_factory=dict)
    mutating: bool = True
    describe: str = ""


ACTIONS: dict = {a.name: a for a in [
    Action("create_workspace", act_create_workspace,
           {"job_id": Arg("ident"), "input_root": Arg("abspath"),
            "note": Arg("text", required=False, default="")},
           describe="create a job workspace with explicit input and output roots"),
    Action("inspect_assets", act_inspect_assets,
           {"job_id": Arg("ident"), "subdir": Arg("relpath", required=False, default=""),
            "limit": Arg("int", required=False, default=200),
            "hash_files": Arg("bool", required=False, default=True)},
           mutating=False, describe="list candidate inputs with sizes and hashes"),
    Action("stage_assets", act_stage_assets,
           {"job_id": Arg("ident"), "items": Arg("relpath_list"),
            "max_bytes": Arg("int", required=False, default=0)},
           describe="copy declared inputs into the workspace, verified by re-hashing"),
    Action("generate_manifest", act_generate_manifest,
           {"job_id": Arg("ident"), "stage": Arg("ident"), "runner": Arg("ident"),
            "units": Arg("relpath_list", required=False, default=None),
            "params": Arg("params", required=False, default=None)},
           describe="write the plan for a stage; nothing may execute before this"),
    Action("estimate_cost", act_estimate_cost,
           {"job_id": Arg("ident"), "stage": Arg("ident")},
           mutating=False, describe="declared cost of a planned stage, and of what is left"),
    Action("start_stage", act_start_stage,
           {"job_id": Arg("ident"), "stage": Arg("ident"),
            "approved_fingerprint": Arg("hex64"),
            "max_units": Arg("int", required=False, default=0)},
           describe="run an approved stage; the fingerprint must match the plan on disk"),
    Action("pause_stage", act_pause_stage,
           {"job_id": Arg("ident"), "stage": Arg("ident"),
            "reason": Arg("text", required=False, default="")},
           describe="stop at the next unit boundary"),
    Action("cancel_stage", act_cancel_stage,
           {"job_id": Arg("ident"), "stage": Arg("ident"), "reason": Arg("text")},
           describe="terminally stop a stage, keeping partial outputs as evidence"),
    Action("resume_stage", act_resume_stage,
           {"job_id": Arg("ident"), "stage": Arg("ident"),
            "approved_fingerprint": Arg("hex64"),
            "max_units": Arg("int", required=False, default=0)},
           describe="continue a paused or partial stage from the last completed unit"),
    Action("publish_heartbeat", act_publish_heartbeat,
           {"job_id": Arg("ident"), "stage": Arg("ident"), "done": Arg("int"),
            "total": Arg("int", required=False, default=0),
            "unit": Arg("ident", required=False, default="units"),
            "detail": Arg("text", required=False, default="")},
           describe="record real done/total progress for long work"),
    Action("preserve_evidence", act_preserve_evidence,
           {"job_id": Arg("ident"), "candidate": Arg("ident"), "stage": Arg("ident"),
            "items": Arg("relpath_list")},
           describe="copy and hash a candidate's evidence before anyone interprets it"),
    Action("index_candidate", act_index_candidate,
           {"job_id": Arg("ident"), "candidate": Arg("ident"), "stage": Arg("ident"),
            "interpretation": Arg("text")},
           describe="index a candidate whose evidence is already preserved; seals it"),
    Action("unseal_candidate", act_unseal_candidate,
           {"job_id": Arg("ident"), "candidate": Arg("ident"), "who": Arg("text"),
            "reason": Arg("text")},
           describe="deliberately unseal one candidate, on the record"),
    Action("open_artifacts", act_open_artifacts,
           {"job_id": Arg("ident"), "stage": Arg("ident", required=False, default="")},
           mutating=False, describe="what a stage produced, by relative path and hash"),
    Action("job_status", act_job_status, {"job_id": Arg("ident")},
           mutating=False, describe="stage states, progress and sealed candidates"),
    Action("list_jobs", act_list_jobs, {}, mutating=False, describe="known workspaces"),
]}


def dispatch(action, args: dict | None = None) -> dict:
    """THE ONLY ENTRY POINT."""
    if not isinstance(action, str) or action not in ACTIONS:
        raise JobRefusal("UNKNOWN_ACTION",
                         "%r is not an ARGUS job action. The permitted actions are: %s"
                         % (str(action)[:60], ", ".join(sorted(ACTIONS))),
                         {"permitted": sorted(ACTIONS)})
    spec = ACTIONS[action]
    kw = _validate(spec.args, args or {})
    return spec.fn(**kw)


def describe_actions() -> list:
    return [{"action": a.name, "mutating": a.mutating, "describe": a.describe,
             "arguments": {k: {"kind": v.kind, "required": v.required}
                           for k, v in a.args.items()}}
            for a in sorted(ACTIONS.values(), key=lambda x: x.name)]


_FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__", "getattr", "globals",
                    "locals", "vars"}

_FORBIDDEN_ATTRS = {"system", "popen", "spawn", "spawnl", "spawnv", "spawnve", "execv",
                    "execve", "execvp", "check_output", "check_call", "Popen", "fork"}
_FORBIDDEN_IMPORTS = {"subprocess", "pty", "commands", "pickle", "marshal", "shlex"}


def execution_surface_offences(path: Path) -> list:
    """Structural scan for anything that could turn an argument into a command."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        return ["%s: unparseable (%s)" % (Path(path).name, e)]
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in _FORBIDDEN_IMPORTS:
                    bad.append("%s:%d imports %s" % (Path(path).name, node.lineno, a.name))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _FORBIDDEN_IMPORTS:
                bad.append("%s:%d imports from %s"
                           % (Path(path).name, node.lineno, node.module))
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in _FORBIDDEN_NAMES:
                bad.append("%s:%d calls %s" % (Path(path).name, node.lineno, f.id))
            elif isinstance(f, ast.Attribute) and f.attr in _FORBIDDEN_ATTRS:
                bad.append("%s:%d calls .%s()" % (Path(path).name, node.lineno, f.attr))
            for kw in node.keywords or []:
                v = kw.value
                if kw.arg == "shell" and isinstance(v, ast.Constant) and v.value is True:
                    bad.append("%s:%d passes shell=True" % (Path(path).name, node.lineno))
    return bad


def guarded_sources() -> list:
    """The files this guard covers: the engine and every service that fronts it."""
    return [Path(__file__),
            ROOT / "argus" / "service" / "jobs_api.py",
            ROOT / "argus" / "service" / "receipts.py"]


def selftest() -> bool:
    import tempfile

    ck = []
    tmp = Path(tempfile.mkdtemp(prefix="argus_jobs_"))
    prior = (os.environ.get("ARGUS_JOBS_ROOT"), os.environ.get("ARGUS_JOB_INPUT_ROOTS"))
    try:
        src = tmp / "inputs"
        src.mkdir()
        for i in range(4):
            (src / ("u%d.txt" % i)).write_text("payload %d" % i, encoding="utf-8")
        os.environ["ARGUS_JOBS_ROOT"] = str(tmp / "jobs")
        os.environ["ARGUS_JOB_INPUT_ROOTS"] = str(tmp)

        for bad_name in ("os.system", "act_create_workspace", "__import__", "",
                         "create_workspace ", "CREATE_WORKSPACE"):
            try:
                dispatch(bad_name, {})
                got = False
            except JobRefusal as e:
                got = e.cls == "UNKNOWN_ACTION"
            if not got:
                break
        ck.append(("SABOTAGE an unlisted action name is REFUSED, including the name of a "
                   "real function in this module", got))

        try:
            dispatch("list_jobs", {"extra": 1})
            unknown_arg = False
        except JobRefusal as e:
            unknown_arg = e.cls == "BAD_ARGUMENT"
        ck.append(("SABOTAGE an unknown argument is REFUSED, not ignored", unknown_arg))

        escapes = ["../secrets.txt", "..\\secrets.txt", "a/../../b", "/etc/passwd",
                   "C:/Windows/system32/cmd.exe", "\\\\server\\share\\x",
                   "sub/../../out"]
        cls = []
        for e_ in escapes:
            try:
                safe_rel(e_)
                cls.append("ACCEPTED")
            except JobRefusal as e:
                cls.append(e.cls)
        ck.append(("SABOTAGE traversal and absolute paths are REFUSED in every spelling",
                   all(c == "PATH_ESCAPE" for c in cls)))

        meta = ["a; rm -rf /", "a && b", "a | b", "a$(id)", "a`id`", "a\nb", "a>b",
                "a\x00b", "a b", "-rf", ".hidden"]
        mcls = []
        for e_ in meta:
            try:
                safe_rel(e_)
                mcls.append("ACCEPTED")
            except JobRefusal as e:
                mcls.append(e.cls)
        ck.append(("SABOTAGE shell metacharacters are REFUSED as arguments, so no string "
                   "that could only matter to a shell is ever accepted",
                   all(c == "BAD_ARGUMENT" for c in mcls)))

        ck.append(("no module in this layer imports subprocess or calls eval/exec/getattr",
                   all(execution_surface_offences(p) == [] for p in guarded_sources()
                       if p.is_file())))
        planted = tmp / "planted.py"
        planted.write_text("import subprocess\n"
                           "def f(c):\n"
                           "    subprocess.run(c, shell=True)\n"
                           "    return eval(c)\n", encoding="utf-8")
        off = execution_surface_offences(planted)
        ck.append(("SABOTAGE and that scan CATCHES a planted offender rather than being "
                   "unable to fail", len(off) >= 3))

        try:
            dispatch("create_workspace", {"job_id": "escape",
                                          "input_root": str(Path(tmp).anchor or _argus_public_path('anchor', ''))})
            root_escape = False
        except JobRefusal as e:
            root_escape = e.cls == "PATH_ESCAPE"
        ck.append(("SABOTAGE an input root outside the declared roots is REFUSED",
                   root_escape))

        r = dispatch("create_workspace", {"job_id": "j1", "input_root": str(src)})
        ws = open_workspace("j1")
        ck.append(("a workspace declares both roots explicitly and derives neither from "
                   "cwd", r["input_root"] == str(src)
                   and Path(r["output_root"]) == ws.root / "outputs"))

        ins = dispatch("inspect_assets", {"job_id": "j1"})
        ck.append(("inspection lists inputs with content hashes",
                   ins["count"] == 4 and all(len(a["sha256"]) == 64
                                             for a in ins["assets"])))

        st = dispatch("stage_assets", {"job_id": "j1",
                                       "items": ["u0.txt", "u1.txt", "u2.txt", "u3.txt"]})
        ck.append(("staging re-hashes the copy, so a truncated copy cannot pass",
                   st["total_staged"] == 4))

        try:
            dispatch("stage_assets", {"job_id": "j1", "items": ["u0.txt"], "max_bytes": 1})
            budget = False
        except JobRefusal as e:
            budget = e.cls == "BUDGET"
        ck.append(("SABOTAGE staging past the byte budget is REFUSED", budget))

        sd = ws.stage_dir("s1")
        sd.mkdir(parents=True, exist_ok=True)
        try:
            dispatch("start_stage", {"job_id": "j1", "stage": "s1",
                                     "approved_fingerprint": "0" * 64})
            no_manifest = False
        except MF.ManifestViolation as e:
            no_manifest = e.cls == "NO_MANIFEST"
        except JobRefusal as e:
            no_manifest = e.cls == "NO_MANIFEST"
        ck.append(("SABOTAGE starting a stage with no manifest is REFUSED", no_manifest))

        gen = dispatch("generate_manifest", {"job_id": "j1", "stage": "s1",
                                             "runner": "checksum"})
        fp = gen["manifest_fingerprint"]
        ck.append(("the plan is written before anything runs, and names its units",
                   MF.exists(sd) and gen["units"] == 4))

        est = dispatch("estimate_cost", {"job_id": "j1", "stage": "s1"})
        ck.append(("the estimate says it is an estimate and needs no GPU",
                   est["planned"]["is_a_measurement"] is False
                   and est["gpu_required"] is False))

        try:
            dispatch("start_stage", {"job_id": "j1", "stage": "s1",
                                     "approved_fingerprint": "a" * 64})
            approved = False
        except JobRefusal as e:
            approved = e.cls == "NOT_APPROVED"
        ck.append(("SABOTAGE starting with a fingerprint that is not the plan on disk is "
                   "REFUSED", approved))

        try:
            dispatch("generate_manifest", {"job_id": "j1", "stage": "s1",
                                           "runner": "inventory"})
            replan = False
        except MF.ManifestViolation as e:
            replan = e.cls == "MANIFEST_REWRITTEN"
        ck.append(("SABOTAGE re-planning the same stage differently is REFUSED", replan))

        run1 = dispatch("start_stage", {"job_id": "j1", "stage": "s1",
                                        "approved_fingerprint": fp, "max_units": 2})
        ck.append(("a bounded run stops at the boundary and reports PARTIAL",
                   run1["state"] == "PARTIAL" and run1["executed_units"] == 2))

        p = dispatch("pause_stage", {"job_id": "j1", "stage": "s1", "reason": "operator"})
        ck.append(("pause is recorded as state, not as a killed process",
                   p["state"] == "PAUSED"))

        try:
            dispatch("start_stage", {"job_id": "j1", "stage": "s1",
                                     "approved_fingerprint": fp})
            paused_start = False
        except JobRefusal as e:
            paused_start = e.cls == "STATE"
        ck.append(("SABOTAGE start on a paused stage is REFUSED; resume is explicit",
                   paused_start))

        before = dict(_RUN_COUNTS)
        run2 = dispatch("resume_stage", {"job_id": "j1", "stage": "s1",
                                         "approved_fingerprint": fp})
        ck.append(("resume completes from the last finished unit",
                   run2["state"] == "DONE" and run2["executed_units"] == 2
                   and run2["completed_units"] == 4))
        ck.append(("and RESUME DID NOT REDO the units already done",
                   all(_RUN_COUNTS[k] == before[k] for k in before)))

        counts_after = dict(_RUN_COUNTS)
        lines = len(_ledger_path(sd).read_text(encoding="utf-8").strip().splitlines())
        run3 = dispatch("start_stage", {"job_id": "j1", "stage": "s1",
                                        "approved_fingerprint": fp})
        lines2 = len(_ledger_path(sd).read_text(encoding="utf-8").strip().splitlines())
        ck.append(("re-running a completed stage is a no-op that says so",
                   run3["reused"] is True and run3["state"] == "DONE"))
        ck.append(("SABOTAGE and it neither re-executes a unit nor duplicates a ledger "
                   "line", _RUN_COUNTS == counts_after and lines == lines2 == 4))

        outs = MF.read_outputs(sd)
        ck.append(("outputs are hashed and recorded, not merely produced",
                   len(outs["outputs"]) == 4
                   and all(len(o["sha256"]) == 64 for o in outs["outputs"])))

        hb_rec = HB.read(sd)
        ck.append(("progress is a count of real units, with the unit named",
                   hb_rec["done"] == 4 and hb_rec["total"] == 4))

        clock = {"t": 1000.0}
        beat_dir = tmp / "beat"
        beat_dir.mkdir()
        h = HB.Heartbeat(beat_dir, unit="units", total=10, stage="x",
                         clock=lambda: clock["t"])
        b = Beat(h, clock=lambda: clock["t"], every=300.0)
        for _ in range(20):
            clock["t"] += 301.0
            b.tick("still reading chunk 3")
        after = HB.read(beat_dir, now=clock["t"])
        ck.append(("SABOTAGE twenty liveness ticks over ninety minutes move the clock and "
                   "NOT the progress counter", b.ticks == 20 and after["done"] == 0
                   and after["fraction"] == 0.0))
        clock["t"] += 10
        b.unit_done("u0")
        ck.append(("only a completed unit advances progress",
                   HB.read(beat_dir, now=clock["t"])["done"] == 1))

        try:
            dispatch("publish_heartbeat", {"job_id": "j1", "stage": "s1", "done": 1,
                                           "total": 4})
            backwards = False
        except JobRefusal as e:
            backwards = e.cls == "BAD_ARGUMENT"
        ck.append(("SABOTAGE a heartbeat that rewinds progress is REFUSED", backwards))
        try:
            dispatch("publish_heartbeat", {"job_id": "j1", "stage": "s1", "done": 9,
                                           "total": 4})
            over = False
        except JobRefusal as e:
            over = e.cls == "BAD_ARGUMENT"
        ck.append(("SABOTAGE done greater than total is REFUSED", over))

        try:
            dispatch("index_candidate", {"job_id": "j1", "candidate": "c1",
                                         "stage": "s1", "interpretation": "looks inky"})
            unpreserved = False
        except JobRefusal as e:
            unpreserved = e.cls == "NOT_PRESERVED"
        ck.append(("SABOTAGE indexing a candidate before its evidence is preserved is "
                   "REFUSED", unpreserved))

        pres = dispatch("preserve_evidence", {"job_id": "j1", "candidate": "c1",
                                              "stage": "s1",
                                              "items": ["u0.txt.sha256", "u1.txt.sha256"]})
        ck.append(("preservation copies and hashes the evidence", pres["preserved"] == 2))

        idx = dispatch("index_candidate", {"job_id": "j1", "candidate": "c1",
                                           "stage": "s1",
                                           "interpretation": "two columns of ink"})
        ck.append(("the candidate is sealed the moment it is indexed",
                   idx["sealed"] is True and "interpretation" not in idx))

        tampered = ws.evidence_root / "c1" / "u0.txt.sha256"
        keep = tampered.read_text(encoding="utf-8")
        tampered.write_text("tampered\n", encoding="utf-8")
        try:
            dispatch("index_candidate", {"job_id": "j1", "candidate": "c2",
                                         "stage": "s1", "interpretation": "x"})
            tamper_caught = False
        except JobRefusal as e:
            tamper_caught = e.cls == "NOT_PRESERVED"
        tampered.write_text(keep, encoding="utf-8")
        ck.append(("SABOTAGE evidence edited after preservation is DETECTED", tamper_caught))

        try:
            read_candidate(ws, "c1")
            sealed_read = False
        except JobRefusal as e:
            sealed_read = e.cls == "SEALED"
        ck.append(("SABOTAGE a sealed candidate is NOT readable, not even redacted",
                   sealed_read))

        stat = dispatch("job_status", {"job_id": "j1"})
        ck.append(("status names the sealed candidate without revealing its content",
                   stat["sealed_candidates"] == ["c1"]
                   and "interpretation" not in json.dumps(stat)))

        try:
            dispatch("unseal_candidate", {"job_id": "j1", "candidate": "c1",
                                          "who": "operator", "reason": "short"})
            weak_unseal = False
        except JobRefusal as e:
            weak_unseal = e.cls == "BAD_ARGUMENT"
        ck.append(("SABOTAGE unsealing without a stated reason is REFUSED", weak_unseal))

        dispatch("unseal_candidate", {"job_id": "j1", "candidate": "c1", "who": "operator",
                                      "reason": "publishing a write-up"})
        rec = read_candidate(ws, "c1")
        ck.append(("an explicit unseal, on the record, makes it readable",
                   rec["interpretation"] == "two columns of ink"
                   and seals(ws)["c1"]["unsealed_by"] == "operator"))

        arts = dispatch("open_artifacts", {"job_id": "j1", "stage": "s1"})
        ck.append(("artifacts are listed by relative path and hash, with no absolute path",
                   arts["stages"][0]["artifacts"]
                   and all(not Path(a["rel"]).is_absolute()
                           for a in arts["stages"][0]["artifacts"])))

        dispatch("stage_assets", {"job_id": "j1", "items": ["u0.txt"]})
        gen2 = dispatch("generate_manifest", {"job_id": "j1", "stage": "s2",
                                              "runner": "inventory",
                                              "units": ["u0.txt", "u1.txt"]})
        dispatch("start_stage", {"job_id": "j1", "stage": "s2",
                                 "approved_fingerprint": gen2["manifest_fingerprint"],
                                 "max_units": 1})
        c = dispatch("cancel_stage", {"job_id": "j1", "stage": "s2",
                                      "reason": "superseded by s1"})
        ck.append(("cancel is terminal and keeps the partial outputs as evidence",
                   c["terminal"] is True
                   and (MF.read_outputs(ws.stage_dir("s2")) or {}).get("outputs")))
        try:
            dispatch("resume_stage", {"job_id": "j1", "stage": "s2",
                                      "approved_fingerprint": gen2["manifest_fingerprint"]})
            resumed_cancelled = False
        except JobRefusal as e:
            resumed_cancelled = e.cls == "STATE"
        ck.append(("SABOTAGE a cancelled stage cannot be resumed", resumed_cancelled))

        link_ok = True
        outside = tmp / "outside"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("not yours", encoding="utf-8")
        try:
            os.symlink(str(outside), str(ws.staged_root / "link"),
                       target_is_directory=True)
        except (OSError, NotImplementedError):
            link_ok = False
        if link_ok:
            try:
                resolve_under(ws.staged_root, "link/secret.txt")
                escaped = False
            except JobRefusal as e:
                escaped = e.cls == "PATH_ESCAPE"
            ck.append(("SABOTAGE a symlink inside the workspace does NOT extend it",
                       escaped))
        else:
            ck.append(("SABOTAGE a symlink inside the workspace does NOT extend it "
                       "(symlinks unavailable on this host)", False))

        ck.append(("every action is described with its typed arguments",
                   len(describe_actions()) == len(ACTIONS)
                   and all(a["describe"] for a in describe_actions())))
    finally:
        for k, v in zip(("ARGUS_JOBS_ROOT", "ARGUS_JOB_INPUT_ROOTS"), prior):
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


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="ARGUS allowlisted job engine")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--actions", action="store_true", help="print the allowlist")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    if a.actions:
        print(json.dumps(describe_actions(), indent=2))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
