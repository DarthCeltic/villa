"""The manifest: what a stage WILL do, written to disk BEFORE it does any of it."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_SCHEMA = "argus-manifest-v1"
OUTPUTS_SCHEMA = "argus-outputs-v1"

FILENAME = "manifest.json"
OUTPUTS_FILENAME = "outputs.json"


class ManifestViolation(RuntimeError):
    """A refusal."""

    CLASSES = ("NO_MANIFEST", "MANIFEST_REWRITTEN", "UNREADABLE", "BAD_MANIFEST",
               "INPUT_MISSING", "OUTPUT_MISSING", "HASH_MISMATCH")

    def __init__(self, cls: str, detail: str, evidence: dict | None = None):
        if cls not in self.CLASSES:
            raise ValueError("unknown manifest violation class %r" % cls)
        super().__init__("%s: %s" % (cls, detail))
        self.cls, self.detail, self.evidence = cls, detail, evidence or {}

    def as_record(self) -> dict:
        return {"ok": False, "terminal": "ARGUS_MANIFEST_VIOLATION", "class": self.cls,
                "detail": self.detail, "evidence": self.evidence}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    """Streamed, because staged assets are volume slices and do not fit in memory."""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj) -> str:
    """One spelling per value, so a fingerprint is stable across processes and platforms."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      default=str)


@dataclass(frozen=True)
class Asset:
    """One file, identified by content rather than by name."""

    rel: str
    sha256: str
    bytes: int

    def as_record(self) -> dict:
        return {"rel": self.rel, "sha256": self.sha256, "bytes": self.bytes}

    @staticmethod
    def of(root: Path, rel: str) -> "Asset":
        p = Path(root) / rel
        if not p.is_file():
            raise ManifestViolation("INPUT_MISSING",
                                    "declared input %r is not a file under the input root"
                                    % rel, {"rel": rel})
        return Asset(rel, sha256_file(p), p.stat().st_size)


@dataclass
class Manifest:
    """The plan: everything a later reader needs to decide what should exist, and why."""

    job_id: str
    stage: str
    action: str
    input_root: str
    output_root: str
    units: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    planned_outputs: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    code: dict = field(default_factory=dict)
    estimate: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    schema: str = MANIFEST_SCHEMA

    def fingerprint_material(self) -> dict:
        """The parts that determine the OUTPUTS."""
        return {"action": self.action, "stage": self.stage, "job_id": self.job_id,
                "units": list(self.units),
                "inputs": sorted([a.rel, a.sha256] for a in self.inputs),
                "planned_outputs": sorted(self.planned_outputs),
                "params": self.params, "code": self.code}

    def fingerprint(self) -> str:
        return sha256_bytes(canonical_json(self.fingerprint_material()).encode("utf-8"))

    def as_record(self) -> dict:
        return {"schema": self.schema, "job_id": self.job_id, "stage": self.stage,
                "action": self.action, "input_root": self.input_root,
                "output_root": self.output_root, "units": list(self.units),
                "inputs": [a.as_record() for a in self.inputs],
                "planned_outputs": list(self.planned_outputs), "params": self.params,
                "code": self.code, "estimate": self.estimate,
                "created_at": self.created_at, "fingerprint": self.fingerprint()}

    @staticmethod
    def from_record(r: dict) -> "Manifest":
        if str(r.get("schema", "")) != MANIFEST_SCHEMA:
            raise ManifestViolation("BAD_MANIFEST",
                                    "not an ARGUS manifest (schema %r)" % r.get("schema"))
        return Manifest(job_id=r["job_id"], stage=r["stage"], action=r["action"],
                        input_root=r["input_root"], output_root=r["output_root"],
                        units=list(r.get("units", [])),
                        inputs=[Asset(a["rel"], a["sha256"], int(a["bytes"]))
                                for a in r.get("inputs", [])],
                        planned_outputs=list(r.get("planned_outputs", [])),
                        params=dict(r.get("params", {})), code=dict(r.get("code", {})),
                        estimate=dict(r.get("estimate", {})),
                        created_at=float(r.get("created_at", 0.0)))


def path_for(stage_dir: Path) -> Path:
    return Path(stage_dir) / FILENAME


def write(stage_dir: Path, m: Manifest) -> Path:
    """Write the plan atomically, and refuse to overwrite a DIFFERENT plan."""
    d = Path(stage_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = path_for(d)
    if p.is_file():
        prior = read(d)
        if prior.fingerprint() != m.fingerprint():
            raise ManifestViolation(
                "MANIFEST_REWRITTEN",
                "a different manifest already exists for stage %r; a plan is not edited "
                "after the fact. Start a new stage instead." % m.stage,
                {"existing": prior.fingerprint(), "incoming": m.fingerprint()})
        return p
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(m.as_record(), indent=2, sort_keys=True))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(p)
    return p


def read(stage_dir: Path) -> Manifest:
    p = path_for(stage_dir)
    if not p.is_file():
        raise ManifestViolation("NO_MANIFEST", "no manifest at %s" % p.name)
    try:
        r = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise ManifestViolation("UNREADABLE", str(e)[:200], {"file": p.name})
    return Manifest.from_record(r)


def exists(stage_dir: Path) -> bool:
    return path_for(stage_dir).is_file()


def require(stage_dir: Path, *, why: str = "execution") -> Manifest:
    """THE INVARIANT."""
    if not exists(stage_dir):
        raise ManifestViolation(
            "NO_MANIFEST",
            "%s was requested for %r but no manifest was written first. The manifest is "
            "written BEFORE work begins, so that an interrupted run can be told apart "
            "from one that never started." % (why, Path(stage_dir).name),
            {"stage": Path(stage_dir).name})
    return read(stage_dir)


def record_outputs(stage_dir: Path, output_root: Path, rels, *,
                   strict: bool = True) -> dict:
    """Hash what was actually produced and write it beside the plan."""
    root = Path(output_root)
    got, missing = [], []
    for rel in rels:
        p = root / rel
        if p.is_file():
            got.append(Asset(rel, sha256_file(p), p.stat().st_size).as_record())
        else:
            missing.append(rel)
    if strict and missing:
        raise ManifestViolation("OUTPUT_MISSING",
                                "planned outputs were not produced: %s"
                                % ", ".join(sorted(missing)[:8]), {"missing": missing})
    rec = {"schema": OUTPUTS_SCHEMA, "outputs": got, "missing": missing,
           "recorded_at": time.time()}
    Path(stage_dir).mkdir(parents=True, exist_ok=True)
    p = Path(stage_dir) / OUTPUTS_FILENAME
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, indent=2, sort_keys=True))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(p)
    return rec


def read_outputs(stage_dir: Path) -> dict | None:
    p = Path(stage_dir) / OUTPUTS_FILENAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def verify_inputs(m: Manifest, input_root: Path) -> list:
    """Re-hash the declared inputs."""
    bad = []
    for a in m.inputs:
        p = Path(input_root) / a.rel
        if not p.is_file():
            bad.append("%s: declared input is gone" % a.rel)
            continue
        got = sha256_file(p)
        if got != a.sha256:
            bad.append("%s: content changed since the plan (%s -> %s)"
                       % (a.rel, a.sha256[:12], got[:12]))
    return bad


def selftest() -> bool:
    import shutil
    import tempfile

    ck = []
    tmp = Path(tempfile.mkdtemp(prefix="argus_manifest_"))
    try:
        src, out, stage = tmp / "in", tmp / "out", tmp / "stage"
        src.mkdir()
        out.mkdir()
        (src / "a.txt").write_text("alpha", encoding="utf-8")
        (src / "b.txt").write_text("beta", encoding="utf-8")

        def build(units=("u1", "u2"), params=None) -> Manifest:
            return Manifest(job_id="j1", stage="s1", action="checksum",
                            input_root=str(src), output_root=str(out),
                            units=list(units),
                            inputs=[Asset.of(src, "a.txt"), Asset.of(src, "b.txt")],
                            planned_outputs=["u1.sha256", "u2.sha256"],
                            params=params or {"algo": "sha256"},
                            code={"runner": "checksum", "source_sha256": "0" * 64})

        m = build()
        ck.append(("an input is identified by content, not by path",
                   all(len(a.sha256) == 64 for a in m.inputs)))

        try:
            require(stage)
            refused = False
        except ManifestViolation as e:
            refused = e.cls == "NO_MANIFEST"
        ck.append(("SABOTAGE executing with no manifest is REFUSED", refused))

        write(stage, m)
        ck.append(("the manifest is on disk before anything runs", exists(stage)))
        ck.append(("and reads back to the same fingerprint",
                   read(stage).fingerprint() == m.fingerprint()))

        write(stage, build())
        ck.append(("rewriting the IDENTICAL plan is allowed (the retry path)",
                   read(stage).fingerprint() == m.fingerprint()))

        try:
            write(stage, build(units=("u1", "u2", "u3")))
            rewritten = False
        except ManifestViolation as e:
            rewritten = e.cls == "MANIFEST_REWRITTEN"
        ck.append(("SABOTAGE writing a DIFFERENT plan over it is REFUSED", rewritten))

        moved = build()
        moved.input_root = "Z:/elsewhere"
        moved.output_root = "Z:/elsewhere/out"
        moved.created_at = m.created_at + 10_000
        ck.append(("the fingerprint ignores roots and clocks, so a moved workspace is "
                   "still the same work", moved.fingerprint() == m.fingerprint()))
        ck.append(("but a changed parameter changes the fingerprint",
                   build(params={"algo": "sha512"}).fingerprint() != m.fingerprint()))

        (out / "u1.sha256").write_text("x", encoding="utf-8")
        try:
            record_outputs(stage, out, m.planned_outputs)
            missing_refused = False
        except ManifestViolation as e:
            missing_refused = e.cls == "OUTPUT_MISSING"
        ck.append(("SABOTAGE a planned output that was never produced is REFUSED",
                   missing_refused))

        (out / "u2.sha256").write_text("y", encoding="utf-8")
        rec = record_outputs(stage, out, m.planned_outputs)
        ck.append(("outputs are hashed too, not only inputs",
                   len(rec["outputs"]) == 2
                   and all(len(o["sha256"]) == 64 for o in rec["outputs"])))
        ck.append(("and the output record reads back",
                   (read_outputs(stage) or {}).get("schema") == OUTPUTS_SCHEMA))

        ck.append(("unchanged inputs verify clean", verify_inputs(m, src) == []))
        (src / "a.txt").write_text("ALPHA CHANGED", encoding="utf-8")
        bad = verify_inputs(m, src)
        ck.append(("SABOTAGE an input edited after the plan is DETECTED",
                   len(bad) == 1 and "content changed" in bad[0]))

        try:
            Manifest.from_record({"schema": "something-else"})
            foreign = False
        except ManifestViolation as e:
            foreign = e.cls == "BAD_MANIFEST"
        ck.append(("a foreign json file is not read as a manifest", foreign))

        ck.append(("no input path in a manifest record is absolute",
                   all(not Path(a["rel"]).is_absolute()
                       for a in m.as_record()["inputs"])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    for msg, good in ck:
        print("  %s %s" % ("PASS" if good else "FAIL", msg))
        ok &= bool(good)
    print("selftest: %d/%d passed" % (sum(1 for _, g in ck if g), len(ck)))
    return ok


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="ARGUS stage manifests")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--show", metavar="STAGE_DIR", help="print one manifest")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    if a.show:
        print(json.dumps(read(Path(a.show)).as_record(), indent=2, sort_keys=True))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
