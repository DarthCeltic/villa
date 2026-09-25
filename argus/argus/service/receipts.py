"""Sanitized receipts for the interface: a declared key set, and an evidence-tree index."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException

from argus.adapters.vigiles_decision import BLINDING_MARKERS
from argus.core import feed as F
from argus.core import heartbeat as HB
from argus.core import jobs as J
from argus.core import manifest as MF
from argus.core import paths
from argus.core import scroll_ids as SI

RECEIPTS_SCHEMA = "argus-receipts-v1"
EVIDENCE_SCHEMA = "argus-evidence-index-v1"
SCROLL_IDS_SCHEMA = "argus-scroll-ids-v1"

RECEIPTS_TTL_S = 30.0
INDEX_TTL_S = 300.0

CONTENT_CAP_BYTES = 2 * 1024 * 1024

MAX_RUNS = 4000
MAX_ARTIFACTS_PER_RUN = 200
HASH_CAP_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ReceiptKey:
    """One declared receipt."""

    key: str
    root: str
    rel: str
    produced_by: str
    scripts: tuple = ()

    def resolve(self) -> Path:
        base = paths.repo() if self.root == "repo" else paths.artifacts()
        return J.resolve_under(base, self.rel, field="receipt key %s" % self.key)


KEYS: tuple = (
    ReceiptKey("upstream_lock", "repo", "argus/upstream.lock.json",
               "pinned upstream checkouts"),
    ReceiptKey("asset_catalogue", "repo", "artifacts/registries/assets.json",
               "asset catalogue: stores found once, never hand-probed again"),
    ReceiptKey("model_registry", "repo", "artifacts/registries/models.json",
               "model registry: read from the checkpoint, never from its name"),
    ReceiptKey("dataset_registry", "repo", "artifacts/registries/datasets.json",
               "dataset registry"),
    ReceiptKey("upstream_registry", "repo", "artifacts/registries/upstream.json",
               "upstream registry: capabilities probed against the pinned upstream commit"),
    ReceiptKey("failure_registry", "repo", "artifacts/registries/failures.json",
               "failure registry: failure modes with runnable fixtures"),
    ReceiptKey("progress_video", "repo", "artifacts/receipts/progress_video.json",
               "optional progress-video receipt; none ships with the public release"),
    ReceiptKey("first_letters_acquisition", "repo",
               "artifacts/receipts/first_letters_acquisition.json",
               "optional First Letters acquisition receipt; none ships with the public release"),
    ReceiptKey("grand_prize_chain", "repo", "artifacts/receipts/grand_prize_chain.json",
               "optional Grand Prize route receipt; none ships with the public release"),
    ReceiptKey("cross_scroll_gate", "repo", "artifacts/receipts/cross_scroll_gate.json",
               "optional cross-scroll gate receipt; none ships with the public release"),
    ReceiptKey("diary_evidence", "repo", "artifacts/receipts/diary_evidence.json",
               "optional diary evidence manifest; none ships with the public release"),
)

RECEIPT_FIELDS = ("run_id", "collection", "schema", "category", "terminal",
                  "refusal_class", "refusal_reason", "stage", "operational_state",
                  "highest_certified_stage", "created_at", "updated_at",
                  "receipt_bytes", "receipt_sha256", "sealed", "marker", "why",
                  "progress", "artifacts", "artifact_count", "truncated", "foreign",
                  "note", "public_run")

PROGRESS_FIELDS = ("present", "state", "done", "total", "unit", "fraction", "age_s",
                   "stale", "why")

SECRET_HINTS = ("token", "secret", "password", "passwd", "credential", "api_key",
                "apikey", "authorization", "cookie", "private_key", "session_id")

_TOKEN = re.compile(
    r"\b(sk|hf|ghp|gho|ghs|ghu|glpat|xox[baprs]|AKIA|ya29)[-_][A-Za-z0-9_\-]{8,}", re.I)

_ABSOLUTE = re.compile(
    r"(?<![A-Za-z])(?:[A-Za-z]:[\\/])|(?:^\\\\)|(?:^//)|(?:^/(?!api/)[A-Za-z])"
    r"|(?:^\\[A-Za-z][^\\/:*?\"<>|]{0,60}\\)")

_WITHHELD = "<withheld: absolute path>"
_REDACTED = "<withheld: credential-shaped>"

_ROOT_LABELS = {"ARGUS_REPO": "repo", "ARGUS_LEGACY_ROOT": "legacy",
                "ARGUS_CACHE_ROOT": "cache", "ARGUS_UPSTREAM_ROOT": "upstream",
                "ARGUS_RUNTIME_ROOT": "runtime"}

_ROOTS_CACHE: dict = {}


def _declared_roots() -> list:
    """(label, realpath) for each declared root, longest path first."""
    key = tuple(os.environ.get(v, "") for v in sorted(_ROOT_LABELS))
    hit = _ROOTS_CACHE.get(key)
    if hit is None:
        hit = []
        for var, label in _ROOT_LABELS.items():
            try:
                rp = os.path.realpath(str(paths.root(var)))
            except Exception:
                continue
            hit.append((label, rp.replace("\\", "/").rstrip("/")))
        hit.sort(key=lambda t: len(t[1]), reverse=True)
        _ROOTS_CACHE[key] = hit
    return hit


def relativize(v: str) -> str | None:
    """`<absolute repo>/artifacts/x.json` -> `<repo>/artifacts/x.json`, or None."""
    t = v.replace("\\", "/")
    low = t.lower()
    for label, root in _declared_roots():
        if not root:
            continue
        r = root.lower()
        if low == r:
            return "<%s>" % label
        if low.startswith(r + "/"):
            return "<%s>/%s" % (label, t[len(root) + 1:])
    return None


class LeakGuard(RuntimeError):
    """Raised when a payload about to be returned holds something it must not."""


def sanitize_value(v):
    """One value, made safe to send."""
    if isinstance(v, str):
        if _TOKEN.search(v):
            return _REDACTED
        if _ABSOLUTE.search(v):
            return relativize(v) or _WITHHELD
        return v[:4000]
    if isinstance(v, dict):
        return {k: sanitize_value(x) for k, x in v.items()
                if not any(h in str(k).lower() for h in SECRET_HINTS)}
    if isinstance(v, (list, tuple)):
        return [sanitize_value(x) for x in v[:2000]]
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return sanitize_value(str(v))


def leaks(payload) -> list:
    """Everything in `payload` that must not leave the service."""
    bad = []

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if any(h in str(k).lower() for h in SECRET_HINTS):
                    bad.append("%s.%s is a credential-shaped field name" % (where, k))
                walk(v, "%s.%s" % (where, k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (where, i))
        elif isinstance(node, str):
            if node in (_WITHHELD, _REDACTED) or node.startswith("<"):
                return
            if _TOKEN.search(node):
                bad.append("%s holds a credential shape" % where)
            elif _ABSOLUTE.search(node):
                bad.append("%s holds an absolute path (%s)" % (where, node[:60]))

    walk(payload, "$")
    return bad


def content_stats(node) -> tuple:
    """(withheld, relativized) over an already-sanitized structure."""
    withheld = relativized = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            stack.extend(n.values())
        elif isinstance(n, (list, tuple)):
            stack.extend(n)
        elif isinstance(n, str):
            if n in (_WITHHELD, _REDACTED):
                withheld += 1
            elif n.startswith("<") and "%s" % n[1:9].split(">")[0] in _ROOT_LABELS.values():
                relativized += 1
    return withheld, relativized


def guarded(payload):
    """The last thing every route calls."""
    bad = leaks(payload)
    if bad:
        raise LeakGuard("; ".join(bad[:5]))
    return payload


def evidence_roots() -> list:
    """THE ONLY source of the tree roots: the path contract, never a caller."""
    return paths.artifact_roots()


def _utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def read_declared(k: ReceiptKey) -> dict:
    """One declared receipt: metadata always, content only when it is safe to send."""
    rec = {"key": k.key, "relpath": k.rel, "root": k.root, "present": False,
           "bytes": None, "mtime_utc": None, "sha256_16": None,
           "produced_by": k.produced_by, "produced_by_scripts": list(k.scripts),
           "sealed": False, "withheld_reason": None, "content": None,
           "missing_reason": None, "content_redactions": 0,
           "content_paths_relativized": 0}
    try:
        p = k.resolve()
    except J.JobRefusal as e:
        rec["missing_reason"] = "the declared path is not resolvable inside its root: %s" \
                                % e.detail
        return rec
    if not p.is_file():
        rec["missing_reason"] = ("no file at this relpath under the %s root; the stage "
                                 "that writes it has not run, or has not written it yet"
                                 % k.root)
        return rec
    st = p.stat()
    rec.update({"present": True, "bytes": st.st_size, "mtime_utc": _utc(st.st_mtime),
                "sha256_16": MF.sha256_file(p)[:16]})

    seal = F.sealed_by(p)
    if seal:
        rec["sealed"] = True
        rec["withheld_reason"] = ("this receipt is under an active blinding marker (%s); "
                                  "its metadata is shown and its content is withheld by "
                                  "the service" % Path(seal).name)
        return rec
    if st.st_size > CONTENT_CAP_BYTES:
        rec["withheld_reason"] = ("%d bytes exceeds the %d-byte content cap for this "
                                  "polled route; read it as an artifact instead"
                                  % (st.st_size, CONTENT_CAP_BYTES))
        return rec
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        rec["withheld_reason"] = "the receipt is not readable as JSON: %s" % str(e)[:120]
        return rec
    san = sanitize_value(raw)
    withheld, relativized = content_stats(san)
    rec["content"] = san
    rec["content_redactions"] = withheld
    rec["content_paths_relativized"] = relativized
    return rec


def build_receipts(keys=None) -> dict:
    ks = tuple(keys) if keys is not None else KEYS
    t0 = time.time()
    out = {k.key: read_declared(k) for k in ks}
    return {"schema": RECEIPTS_SCHEMA, "generated_at": time.time(),
            "build_seconds": round(time.time() - t0, 3), "receipts": out}


def build_scroll_ids() -> dict:
    """The identity rules, COMPUTED from argus.core.scroll_ids rather than restated."""
    canonical = tuple(SI.CANONICAL)
    return {"schema": SCROLL_IDS_SCHEMA,
            "generated_at": time.time(),
            "canonical": sorted(canonical),
            "canonical_count": len(canonical),
            "aliases": dict(sorted(SI.ALIASES.items())),
            "placeholders": sorted(SI.PLACEHOLDERS),
            "confusable_pairs": [sorted(p) for p in SI.confusable_pairs(canonical)],
            "note": "computed from argus.core.scroll_ids on every build; a reader that "
                    "reimplements any of these rules will drift from the one the engine "
                    "actually applies"}


def _marker_in(d: Path) -> str | None:
    for m in BLINDING_MARKERS:
        if (d / m).is_file():
            return m
    return None


def _sealed_above(d: Path) -> str | None:
    """A marker at or above `d`, checked once per collection and then inherited: the marker sits at the experiment root and the runs sit under it."""
    for cur in [d, *d.parents]:
        m = _marker_in(cur)
        if m:
            return m
        if cur == cur.parent:
            break
    return None


def _progress(d: Path) -> dict:
    r = HB.read(d)
    return {k: r.get(k) for k in PROGRESS_FIELDS}



RECEIPT_FILENAMES = ("run.json", "RUN_RECEIPT.json", "RECEIPT.json", "OFFLINE_SUITE_RUN.json",
                     "PIPELINE_RUN_RECEIPT.json")

SCHEMA_SCAN_MAX_FILES = 20
SCHEMA_SCAN_MAX_BYTES = 64 * 1024

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

SCHEMA_CATEGORIES: tuple = (
    ("argus-hecate-control-run", "SCIENTIFIC_CONTROL"),
    ("argus-hecate-runtime-receipt", "RUNTIME_VALIDATION"),
    ("argus-surface-status", "SURFACE_STATUS"),
    ("argus-qualification-decision", "QUALIFICATION_PREFLIGHT"),
    ("argus-run-receipt", "QUALIFICATION_PREFLIGHT"),
    ("argus-evidence-gate", "EVIDENCE_GATE"),
    ("argus-public-pipeline-run", "PUBLIC_PIPELINE_RUN"),
    ("argus-run", "SCIENTIFIC_RUN"),
)

FILENAME_CATEGORIES: dict = {"OFFLINE_SUITE_RUN.json": "TEST_STATUS"}

CATEGORIES = ("SCIENTIFIC_RUN", "SCIENTIFIC_CONTROL", "RUNTIME_VALIDATION", "TEST_STATUS",
              "UI_VERIFICATION", "QUALIFICATION_PREFLIGHT", "SURFACE_STATUS", "EVIDENCE_GATE",
              "PUBLIC_PIPELINE_RUN", "UNCLASSIFIED")


def classify_schema(schema: str | None) -> str:
    """schema string -> category."""
    if not schema:
        return "UNCLASSIFIED"
    best = None
    for prefix, cat in SCHEMA_CATEGORIES:
        if schema.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, cat)
    return best[1] if best else "UNCLASSIFIED"


def _read_schema(p: Path) -> str | None:
    """The top-level \"schema\" string of one json file, or None -- absent, unparseable, not an object, not a string, or too large to be worth reading just to answer this."""
    try:
        if p.stat().st_size > SCHEMA_SCAN_MAX_BYTES:
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    s = raw.get("schema")
    return str(s)[:80] if isinstance(s, str) else None


def _find_receipt_file(d: Path) -> Path | None:
    """The one file in `d` that counts as its receipt, or None."""
    for name in RECEIPT_FILENAMES:
        p = d / name
        if p.is_file():
            return p
    try:
        candidates = sorted(p for p in d.iterdir() if p.is_file() and p.suffix == ".json")
    except OSError:
        return None
    for p in candidates[:SCHEMA_SCAN_MAX_FILES]:
        if _read_schema(p) is not None:
            return p
    return None


def _is_image_only_dir(d: Path) -> bool:
    """True when `d` holds one or more files, ALL images, and no subdirectories -- the shape of a screenshot-only verification run: evidence with no JSON receipt at all."""
    try:
        entries = [p for p in d.iterdir() if not p.is_symlink()]
    except OSError:
        return False
    files = [p for p in entries if p.is_file()]
    if not files or any(p.is_dir() for p in entries):
        return False
    return all(p.suffix.lower() in _IMAGE_EXTS for p in files)


def _looks_like_receipt(d: Path) -> bool:
    """True when `d` is a leaf the evidence index should surface as one entry."""
    return ((d / HB.FILENAME).is_file()
            or _find_receipt_file(d) is not None
            or _is_image_only_dir(d))


def _receipt_fields(d: Path) -> dict:
    """The allowlisted contents of one receipt, its category, or a stated absence."""
    out = {"schema": None, "terminal": None, "refusal_class": None,
           "refusal_reason": None, "stage": None, "operational_state": None,
           "highest_certified_stage": None, "created_at": None, "receipt_bytes": None,
           "receipt_sha256": None, "foreign": False, "note": None,
           "category": "UNCLASSIFIED"}
    p = _find_receipt_file(d)
    if p is None:
        if _is_image_only_dir(d):
            out["category"] = "UI_VERIFICATION"
            out["note"] = ("no declared receipt schema; classified as UI-verification "
                           "evidence because this directory holds only image files")
        else:
            out["note"] = "no run receipt; this run is described by its heartbeat alone"
        return out
    try:
        size = p.stat().st_size
        out["receipt_bytes"] = size
        out["receipt_sha256"] = MF.sha256_file(p) if size <= HASH_CAP_BYTES else None
        r = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        out["note"] = "the run receipt is unreadable: %s" % str(e)[:120]
        return out
    if not isinstance(r, dict):
        out["note"] = "the run receipt is not an object"
        return out
    schema = r.get("schema")
    schema = str(schema)[:80] if isinstance(schema, str) else None
    out["schema"] = schema
    filename_cat = FILENAME_CATEGORIES.get(p.name)
    if filename_cat is None and (schema is None or not schema.startswith("argus-")):
        out["foreign"] = True
        out["note"] = "not an ARGUS run receipt; reported as present, not as a run"
        return out
    out["category"] = filename_cat or classify_schema(schema)
    for k in ("terminal", "refusal_class", "refusal_reason", "stage",
              "operational_state", "highest_certified_stage", "created_at"):
        if k in r:
            out[k] = sanitize_value(r[k])
    if out["category"] == "PUBLIC_PIPELINE_RUN":
        out["public_run"] = _public_run_fields(r)
        out["terminal"] = out["terminal"] or out["public_run"]["outcome"]
        out["created_at"] = out["created_at"] or out["public_run"]["finished_utc"]
    return out


def _public_run_fields(r: dict) -> dict:
    """The publishable face of an `argus run` receipt (schema `argus-public-pipeline-run-v1`)."""
    def _s(v, n=600):
        return sanitize_value(v)[:n] if isinstance(v, str) else None

    def _b(v):
        return v if isinstance(v, bool) else None

    rc = r.get("result_class") if isinstance(r.get("result_class"), dict) else {}
    stages = []
    scroll = None
    for st in r.get("stages") or []:
        if not isinstance(st, dict):
            continue
        sec = st.get("seconds")
        stages.append({"stage": _s(st.get("stage"), 40), "state": _s(st.get("state"), 40),
                       "seconds": sec if isinstance(sec, (int, float)) else None,
                       "uses": _s(st.get("uses"), 200)})
        d = st.get("detail")
        if scroll is None and st.get("stage") == "identify" and isinstance(d, dict):
            scroll = _s(d.get("scroll"), 40)
    outs = {}
    for name, o in (r.get("outputs") or {}).items():
        if isinstance(o, dict) and isinstance(o.get("sha256"), str):
            outs[str(name)[:80]] = {"bytes": o.get("bytes") if isinstance(o.get("bytes"), int) else None,
                                    "sha256": o["sha256"][:64]}
    crop = r.get("sealed_crop") if isinstance(r.get("sealed_crop"), dict) else {}
    launch = r.get("launch") if isinstance(r.get("launch"), dict) else {}
    env = r.get("environment") if isinstance(r.get("environment"), dict) else {}
    terms = r.get("terms") if isinstance(r.get("terms"), dict) else {}
    score = rc.get("score")
    return {
        "target": _s(r.get("target"), 120), "scroll": scroll, "run_id": _s(r.get("run_id"), 40),
        "outcome": _s(r.get("outcome"), 40),
        "started_utc": _s(r.get("started_utc"), 40), "finished_utc": _s(r.get("finished_utc"), 40),
        "manifest_sha256": _s(r.get("manifest_sha256"), 64),
        "source_commit": _s(env.get("source_commit"), 40),
        "crop_manifest_sha256": _s(crop.get("acquire_manifest_sha256"), 64),
        "launch_ceiling": _s(launch.get("ceiling"), 60),
        "result_class": {
            "presentation": _s(rc.get("presentation"), 80), "banner": _s(rc.get("banner"), 400),
            "public_name": _s(rc.get("target"), 200), "target_class": _s(rc.get("target_class"), 60),
            "exposure_basis": _s(rc.get("exposure_basis"), 60), "detector": _s(rc.get("detector"), 200),
            "detector_cross_scroll_qualified": _b(rc.get("detector_cross_scroll_qualified")),
            "acquisition": _s(rc.get("acquisition"), 200), "metric": _s(rc.get("metric"), 80),
            "score": score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
            "may_claim_discovery": _b(rc.get("may_claim_discovery")),
            "may_claim_ink_found": _b(rc.get("may_claim_ink_found")),
            "not_established": [_s(x, 300) for x in (rc.get("not_established") or [])[:12]
                                if isinstance(x, str)],
        },
        "stages": stages[:24], "outputs": outs,
        "terms": {str(k)[:40]: _s(v, 500) for k, v in list(terms.items())[:12] if isinstance(v, str)},
        "limits": [_s(x, 400) for x in (r.get("limits") or [])[:12] if isinstance(x, str)],
    }


def _artifact_names(d: Path) -> tuple:
    """Artifact NAMES only, relative to the run directory."""
    out, n = [], 0
    try:
        for p in sorted(d.iterdir()):
            if p.is_symlink() or not p.is_file():
                continue
            n += 1
            if len(out) >= MAX_ARTIFACTS_PER_RUN:
                continue
            out.append({"rel": p.name, "bytes": p.stat().st_size})
    except OSError:
        return [], 0
    return out, n


def _entry(d: Path, collection: str, seal: str | None) -> dict:
    """One run, as the browser may see it."""
    if seal:
        return {"run_id": d.name, "collection": collection, "sealed": True,
                "marker": Path(seal).name,
                "why": "this run is under an active blinding marker; its receipt fields, "
                       "progress and artifact names are withheld by the service",
                "progress": None, "artifacts": [], "artifact_count": None,
                "terminal": None, "operational_state": None}
    arts, n = _artifact_names(d)
    rec = {"run_id": d.name, "collection": collection, "sealed": False,
           "progress": _progress(d), "artifacts": arts, "artifact_count": n,
           "truncated": n > len(arts)}
    rec.update(_receipt_fields(d))
    return {k: sanitize_value(v) for k, v in rec.items() if k in RECEIPT_FIELDS}


def _root_label(r: Path) -> str:
    """A safe, short label for a root that appears in a served payload -- never the raw path."""
    if r == paths.artifact_write_root():
        return "canonical"
    if r == paths.legacy("artifacts"):
        return "legacy"
    return relativize(str(r)) or "other"


def build_index(roots=None) -> dict:
    """Walk every evidence root ONCE each and return one merged, sanitized index."""
    t0 = time.time()
    bases = [Path(r) for r in roots] if roots is not None else evidence_roots()
    entries: list = []
    seen_ids: set = set()
    scanned = 0
    truncated = False
    roots_status = []
    for base in bases:
        present = base.is_dir()
        roots_status.append({"root": _root_label(base), "present": present})
        if not present or len(entries) >= MAX_RUNS:
            continue
        for top in sorted(p for p in base.iterdir() if p.is_dir()):
            scanned += 1
            if len(entries) >= MAX_RUNS:
                truncated = True
                break
            seal = _sealed_above(top)
            if _looks_like_receipt(top):
                if top.name not in seen_ids:
                    seen_ids.add(top.name)
                    entries.append(_entry(top, top.name, seal))
                continue
            try:
                subs = sorted(p for p in top.iterdir() if p.is_dir())
            except OSError:
                continue
            for sub in subs:
                scanned += 1
                if len(entries) >= MAX_RUNS:
                    truncated = True
                    break
                if not _looks_like_receipt(sub):
                    continue
                key = (top.name, sub.name)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                entries.append(_entry(sub, top.name, seal or _marker_in(sub)))
    return {"schema": EVIDENCE_SCHEMA, "built_at": time.time(),
            "scan_seconds": round(time.time() - t0, 2), "dirs_scanned": scanned,
            "runs": len(entries), "truncated": truncated,
            "sealed_runs": sum(1 for e in entries if e["sealed"]),
            "root_present": any(r["present"] for r in roots_status),
            "roots": roots_status, "entries": entries}


class Cache:
    """One built value, its age, and how many times it was actually built."""

    def __init__(self, fn, ttl: float, stamp: str):
        self.fn, self.ttl, self.stamp = fn, ttl, stamp
        self.value: dict | None = None
        self.builds = 0
        self.lock = threading.Lock()

    def _fresh(self, v) -> bool:
        return v is not None and (time.time() - v[self.stamp]) < self.ttl

    FORCE_MIN_AGE_S = 15.0

    def get(self, *, force: bool = False, min_force_age_s: float = 0.0, **kw) -> dict:
        v = self.value
        if force and v is not None and (time.time() - v[self.stamp]) < min_force_age_s:
            force = False
        if not force and self._fresh(v):
            return v
        with self.lock:
            if not force and self._fresh(self.value):
                return self.value
            v = self.value
            if force and v is not None and (time.time() - v[self.stamp]) < min_force_age_s:
                return v
            built = self.fn(**kw)
            self.builds += 1
            self.value = built
            return built

    def age(self) -> float | None:
        v = self.value
        return round(time.time() - v[self.stamp], 2) if v else None

    def status(self) -> dict:
        v = self.value
        return {"built": v is not None, "builds": self.builds, "ttl_s": self.ttl,
                "age_s": self.age(),
                "note": "the disk is read on a build, not on a request"}


RECEIPTS = Cache(build_receipts, RECEIPTS_TTL_S, "generated_at")
INDEX = Cache(build_index, INDEX_TTL_S, "built_at")
SCROLL_IDS = Cache(build_scroll_ids, RECEIPTS_TTL_S, "generated_at")


def reset_caches(receipts_ttl: float = RECEIPTS_TTL_S,
                 index_ttl: float = INDEX_TTL_S) -> None:
    """Drop both caches."""
    global RECEIPTS, INDEX, SCROLL_IDS
    _ROOTS_CACHE.clear()
    RECEIPTS = Cache(build_receipts, receipts_ttl, "generated_at")
    INDEX = Cache(build_index, index_ttl, "built_at")
    SCROLL_IDS = Cache(build_scroll_ids, receipts_ttl, "generated_at")


router = APIRouter()


def _bounded(v, default: int, hi: int, *, field: str) -> int:
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > hi:
        raise HTTPException(422, "%s must be an integer in 0..%d" % (field, hi))
    return v


@router.get("/api/receipts")
def receipts(key: str = "", refresh: bool = False):
    """The declared receipts, served from cache."""
    if key:
        try:
            key = J.ident(key, field="key")
        except J.JobRefusal as e:
            raise HTTPException(422, e.detail)
        if key not in {k.key for k in KEYS}:
            raise HTTPException(404, "no declared receipt %r; the declared keys are %s"
                                     % (key, ", ".join(sorted(k.key for k in KEYS))))
    built = RECEIPTS.get(force=bool(refresh), min_force_age_s=Cache.FORCE_MIN_AGE_S)
    rows = built["receipts"]
    if key:
        rows = {key: rows[key]}
    return guarded({"schema": RECEIPTS_SCHEMA, "generated_at": built["generated_at"],
                    "index_age_s": RECEIPTS.age(), "ttl_s": RECEIPTS_TTL_S,
                    "declared_keys": sorted(k.key for k in KEYS),
                    "index": RECEIPTS.status(), "receipts": rows})


@router.get("/api/scroll-ids")
def scroll_ids(refresh: bool = False):
    """The canonical set, the alias map, the placeholders and the confusable pairs."""
    built = SCROLL_IDS.get(force=bool(refresh), min_force_age_s=Cache.FORCE_MIN_AGE_S)
    out = dict(built)
    out["index_age_s"] = SCROLL_IDS.age()
    out["ttl_s"] = SCROLL_IDS.ttl
    return guarded(out)


@router.get("/api/evidence-index")
def evidence_index(limit: int = 200, offset: int = 0, collection: str = "",
                   sealed: str = "all", refresh: bool = False, category: str = "",
                   scroll: str = ""):
    """The run tree under every declared evidence root (canonical, then legacy), sanitized, merged and paged."""
    limit = _bounded(limit, 200, 1000, field="limit")
    offset = _bounded(offset, 0, 10 ** 6, field="offset")
    if sealed not in ("all", "only", "exclude"):
        raise HTTPException(422, "sealed must be one of: all, only, exclude")
    if collection:
        try:
            collection = J.ident(collection, field="collection")
        except J.JobRefusal as e:
            raise HTTPException(422, e.detail)
    idx = INDEX.get(force=bool(refresh), min_force_age_s=Cache.FORCE_MIN_AGE_S)
    rows = idx["entries"]
    if collection:
        rows = [r for r in rows if r.get("collection") == collection]
    if category:
        if category not in CATEGORIES:
            raise HTTPException(422, "category must be one of: %s" % ", ".join(CATEGORIES))
        rows = [r for r in rows if r.get("category") == category]
    if scroll:
        try:
            scroll = J.ident(scroll, field="scroll")
        except J.JobRefusal as e:
            raise HTTPException(422, e.detail)
        rows = [r for r in rows
                if str((r.get("public_run") or {}).get("scroll") or "").lower() == scroll.lower()]
    if sealed == "only":
        rows = [r for r in rows if r["sealed"]]
    elif sealed == "exclude":
        rows = [r for r in rows if not r["sealed"]]
    page = rows[offset:offset + limit]
    return guarded({"schema": EVIDENCE_SCHEMA, "built_at": idx["built_at"],
                    "index_age_s": INDEX.age(), "root_present": idx["root_present"],
                    "roots": idx["roots"],
                    "total": len(rows), "offset": offset, "limit": limit,
                    "returned": len(page), "truncated": idx["truncated"],
                    "sealed_runs": idx["sealed_runs"], "index": INDEX.status(),
                    "runs": page})


@router.get("/api/evidence-index/status")
def evidence_index_status():
    """How old each index is, and how many times it has been built."""
    return guarded({"schema": EVIDENCE_SCHEMA, "receipts": RECEIPTS.status(),
                    "evidence_index": INDEX.status(),
                    "refresh_with": "GET /api/receipts?refresh=true or "
                                    "GET /api/evidence-index?refresh=true"})


@router.get("/api/evidence-index/{run_id}")
def evidence_run(run_id: str):
    """One run."""
    try:
        run_id = J.ident(run_id, field="run_id")
    except J.JobRefusal as e:
        raise HTTPException(422, e.detail)
    idx = INDEX.get()
    hit = next((r for r in idx["entries"] if r["run_id"] == run_id), None)
    if hit is None:
        raise HTTPException(404, "no run %r in the evidence index" % run_id)
    if hit["sealed"]:
        raise HTTPException(423, "run %r is under an active blinding marker; sealed "
                                 "receipts do not leave the service" % run_id)
    return guarded(dict(hit))


def _fixture(tmp: Path) -> tuple:
    """A repo-shaped root with one open receipt, one sealed one, and one absent one."""
    arts = tmp / "artifacts"
    op = arts / "fixture_open"
    op.mkdir(parents=True)
    (op / "registry.json").write_text(json.dumps({
        "models": ["model-A"], "value": 1,
        "api_token": "hf" + "_" + "AAAABBBBCCCCDDDDEEEEFFFF11112222",
        "written_from": "C" + r":\Users\someone\argus\run.py"}), encoding="utf-8")
    (op / "run.json").write_text(json.dumps({
        "schema": "argus-run-v1", "terminal": "CERTIFIED_SURFACE", "stage": "geometry",
        "operational_state": "COMPLETE", "score": 0.5}), encoding="utf-8")
    sealed = arts / "fixture_sealed"
    sealed.mkdir(parents=True)
    (sealed / BLINDING_MARKERS[0]).write_text("blinded", encoding="utf-8")
    (sealed / "verdict.json").write_text(json.dumps({
        "verdict": "SYNTHETIC SEALED VERDICT", "candidate_count": 12}), encoding="utf-8")
    (sealed / "run.json").write_text(json.dumps({
        "schema": "argus-run-v1", "terminal": "CERTIFIED_INK_CANDIDATE",
        "verdict": "SYNTHETIC SEALED VERDICT", "stage": "decision"}), encoding="utf-8")
    (sealed / "preview_ink.png").write_bytes(b"png")
    control = arts / "fixture_control"
    control.mkdir(parents=True)
    (control / "RUN_RECEIPT.json").write_text(json.dumps({
        "schema": "argus-hecate-control-run-v1", "run_id": "fixture-control",
        "terminal": "COMPLETE"}), encoding="utf-8")
    runtime = arts / "fixture_runtime"
    runtime.mkdir(parents=True)
    (runtime / "RECEIPT.json").write_text(json.dumps({
        "schema": "argus-hecate-runtime-receipt-v1", "state": "STRICT_LOAD_PASSED"}),
        encoding="utf-8")
    preflight = arts / "fixture_preflight"
    preflight.mkdir(parents=True)
    (preflight / "RUN_RECEIPT.json").write_text(json.dumps({
        "schema": "argus-run-receipt-v1", "state": "NOT_READY", "executed": False}),
        encoding="utf-8")
    offline = arts / "fixture_offline_suite"
    offline.mkdir(parents=True)
    (offline / "OFFLINE_SUITE_RUN.json").write_text(json.dumps({
        "id": "fixture-offline-suite", "raw_counts": {"passed": 1, "failed": 0}}), encoding="utf-8")
    ui_verify = arts / "fixture_ui_verify"
    ui_verify.mkdir(parents=True)
    (ui_verify / "masthead.png").write_bytes(b"png")
    keys = (ReceiptKey("open_one", "repo", "artifacts/fixture_open/registry.json",
                       "the fixture's open receipt"),
            ReceiptKey("sealed_one", "repo", "artifacts/fixture_sealed/verdict.json",
                       "the fixture's sealed receipt"),
            ReceiptKey("absent_one", "repo", "artifacts/fixture_absent/nothing.json",
                       "a receipt whose stage has not run"))
    return arts, keys


def selftest() -> bool:
    import os
    import shutil
    import tempfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    ck = []
    tmp = Path(tempfile.mkdtemp(prefix="argus_receipts_"))
    prior = {k: os.environ.get(k) for k in ("ARGUS_REPO", "ARGUS_LEGACY_ROOT")}
    try:
        arts, keys = _fixture(tmp)
        os.environ["ARGUS_REPO"] = str(tmp)
        os.environ["ARGUS_LEGACY_ROOT"] = str(tmp)

        built = build_receipts(keys)
        r = built["receipts"]
        ck.append(("the roots come from the path contract, not a literal in this file, "
                   "and both being the same tmp dir here dedups to a single root",
                   evidence_roots() == [arts]))
        ck.append(("every declared key is present in the payload, answered or not",
                   set(r) == {"open_one", "sealed_one", "absent_one"}))
        ck.append(("ABSENCE IS DATA: a missing receipt is present=false with a reason, "
                   "not an omission and not an error",
                   r["absent_one"]["present"] is False
                   and "has not run" in r["absent_one"]["missing_reason"]
                   and r["absent_one"]["relpath"].endswith("nothing.json")))
        ck.append(("an open receipt carries content, size, mtime and a short hash",
                   r["open_one"]["present"] and r["open_one"]["content"]["value"] == 1
                   and len(r["open_one"]["sha256_16"]) == 16
                   and r["open_one"]["mtime_utc"].endswith("Z")))

        ck.append(("SABOTAGE a sealed receipt keeps its metadata and LOSES its content",
                   r["sealed_one"]["sealed"] is True
                   and r["sealed_one"]["content"] is None
                   and r["sealed_one"]["present"] is True
                   and "blinding marker" in r["sealed_one"]["withheld_reason"]))
        ck.append(("SABOTAGE and the sealed verdict text is nowhere in the payload",
                   "SYNTHETIC SEALED" not in json.dumps(built)
                   and "candidate_count" not in json.dumps(built)))

        ck.append(("SABOTAGE a credential inside receipt content does not survive",
                   "hf_AAAABBBB" not in json.dumps(built)
                   and "api_token" not in json.dumps(built)))
        ck.append(("SABOTAGE an absolute path OUTSIDE the declared roots does not survive",
                   "someone" not in json.dumps(built)
                   and r["open_one"]["content"]["written_from"] == _WITHHELD))
        ck.append(("but a path INSIDE a declared root is rewritten root-relative rather "
                   "than blanked, so the interface keeps the information it is entitled to",
                   relativize(str(arts / "fixture_open")) == "<repo>/artifacts/fixture_open"))
        ck.append(("SABOTAGE and the rewrite is case-insensitive, because these are "
                   "Windows paths", relativize(str(arts).upper() + "/x") is not None))
        ck.append(("a path under no declared root is not rewritten at all",
                   relativize(_argus_public_path('home', 'data/store')) is None))
        ck.append(("SABOTAGE the drive-relative spelling is caught too; it is absolute on "
                   "Windows and names the operator's directories just as plainly",
                   sanitize_value(r"\Users\someone\secrets") == _WITHHELD
                   and leaks({"p": r"\Users\someone\secrets"})))
        ck.append(("but a regular-expression fragment is NOT mistaken for a path, or the "
                   "failure registry's fixtures would arrive blanked",
                   sanitize_value(r"\d+ files") == r"\d+ files"
                   and sanitize_value(r"^\w+$") == r"^\w+$"
                   and leaks({"re": r"\d+"}) == []))
        ck.append(("and both kinds of loss are reported rather than done silently: the "
                   "path is withheld and counted, the credential FIELD is dropped whole",
                   r["open_one"]["content_redactions"] == 1
                   and "api_token" not in r["open_one"]["content"]))
        ck.append(("SABOTAGE the redaction count counts LOSSES, not rewrites: a path "
                   "rewritten root-relative lost nothing and must not inflate it",
                   content_stats({"a": _WITHHELD, "b": "<repo>/x", "c": "plain"})
                   == (1, 1)))

        reset_caches()
        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)

        j = c.get("/api/receipts").json()
        ck.append(("the route answers with the agreed envelope",
                   j["schema"] == RECEIPTS_SCHEMA
                   and isinstance(j["generated_at"], float)
                   and isinstance(j["index_age_s"], float)
                   and isinstance(j["receipts"], dict)))
        ck.append(("the five declared keys are the ones served",
                   set(j["receipts"]) == {k.key for k in KEYS}))
        one = sorted(k.key for k in KEYS)[0]
        ck.append(("a single key can be asked for, in the same envelope shape",
                   set(c.get("/api/receipts", params={"key": one})
                       .json()["receipts"]) == {one}))
        ck.append(("an unknown key is 404, and says what the declared keys are",
                   c.get("/api/receipts", params={"key": "nope"}).status_code == 404))
        ck.append(("SABOTAGE a key that is a path or a shell string is refused by the "
                   "same validator the engine uses",
                   c.get("/api/receipts", params={"key": "../../etc"}).status_code == 422
                   and c.get("/api/receipts",
                             params={"key": "a;rm -rf /"}).status_code == 422))

        builds = RECEIPTS.builds
        for _ in range(20):
            c.get("/api/receipts")
        ck.append(("SABOTAGE twenty polls do not re-read the disk twenty times",
                   RECEIPTS.builds == builds))
        floor, Cache.FORCE_MIN_AGE_S = Cache.FORCE_MIN_AGE_S, 0.0
        c.get("/api/receipts", params={"refresh": "true"})
        Cache.FORCE_MIN_AGE_S = floor
        ck.append(("an explicit refresh does rebuild, so the counter is real and not "
                   "stuck", RECEIPTS.builds == builds + 1))
        reset_caches(receipts_ttl=0.0)
        c.get("/api/receipts")
        c.get("/api/receipts")
        ck.append(("and a zero TTL rebuilds each time, so the cache is a TTL and not a "
                   "one-shot", RECEIPTS.builds == 2))
        reset_caches()

        sid = c.get("/api/scroll-ids").json()
        ck.append(("the canonical set is served whole, with its own schema and count",
                   sid["schema"] == SCROLL_IDS_SCHEMA
                   and sid["canonical_count"] == len(SI.CANONICAL)
                   and len(sid["canonical"]) == sid["canonical_count"]))
        ck.append(("SABOTAGE every field is COMPUTED from argus.core.scroll_ids, so a "
                   "change there moves this payload rather than leaving it behind",
                   sid["confusable_pairs"]
                   == [sorted(p) for p in SI.confusable_pairs(tuple(SI.CANONICAL))]
                   and sorted(sid["placeholders"]) == sorted(SI.PLACEHOLDERS)
                   and sid["aliases"] == dict(SI.ALIASES)))
        ck.append(("and the pairs are drawn from the SAME list the payload serves, not "
                   "from a default bound at import time",
                   all(set(p) <= set(sid["canonical"]) for p in sid["confusable_pairs"])))
        ck.append(("the confusable pairs are the transposition hazards, not an anagram "
                   "rule restated here", all(len(p) == 2 for p in sid["confusable_pairs"])
                   and any("PHerc0814" in p and "PHerc0841" in p
                           for p in sid["confusable_pairs"])))
        ck.append(("no placeholder is mistaken for a canonical id",
                   not (set(sid["placeholders"]) & set(sid["canonical"]))))
        ck.append(("and every alias target is itself canonical",
                   set(sid["aliases"].values()) <= set(sid["canonical"])))
        ck.append(("it is cached like everything else",
                   isinstance(sid["index_age_s"], float) and sid["ttl_s"] > 0))

        e = c.get("/api/evidence-index").json()
        ck.append(("the evidence index lists the tree with its own schema",
                   e["schema"] == EVIDENCE_SCHEMA and e["total"] >= 1))
        body = json.dumps(e)
        ck.append(("SABOTAGE a sealed run in the tree is reduced to its existence",
                   "SYNTHETIC SEALED" not in body and "preview_ink" not in body
                   and "verdict.json" not in body))
        srow = next((x for x in e["runs"] if x["sealed"]), None)
        ck.append(("and its marker is named by filename, never by path",
                   srow is not None and srow["marker"] in BLINDING_MARKERS
                   and srow["artifacts"] == []))

        by_id = {x["run_id"]: x for x in e["runs"]}
        ck.append(("a RUN_RECEIPT.json Hecate control run is SCIENTIFIC_CONTROL, not "
                   "lumped in with every other kind of run",
                   by_id["fixture_control"]["category"] == "SCIENTIFIC_CONTROL"
                   and by_id["fixture_control"]["schema"] == "argus-hecate-control-run-v1"))
        ck.append(("a RECEIPT.json runtime-validation receipt is RUNTIME_VALIDATION, not "
                   "SCIENTIFIC_RUN -- it confirms an install, not a scientific result",
                   by_id["fixture_runtime"]["category"] == "RUNTIME_VALIDATION"))
        ck.append(("SABOTAGE a preflight RUN_RECEIPT.json (schema argus-run-receipt-v1) is "
                   "QUALIFICATION_PREFLIGHT and must NOT fall through to the generic "
                   "'argus-run' -> SCIENTIFIC_RUN prefix match",
                   by_id["fixture_preflight"]["category"] == "QUALIFICATION_PREFLIGHT"))
        ck.append(("OFFLINE_SUITE_RUN.json, which carries no schema field at all, is still "
                   "classified -- TEST_STATUS, from the filename, not guessed as UNCLASSIFIED",
                   by_id["fixture_offline_suite"]["category"] == "TEST_STATUS"
                   and by_id["fixture_offline_suite"]["schema"] is None))
        ck.append(("a directory of screenshots with no JSON receipt at all is discovered "
                   "and classified UI_VERIFICATION, not silently dropped from the index",
                   by_id["fixture_ui_verify"]["category"] == "UI_VERIFICATION"))
        ck.append(("the historical run.json family is still SCIENTIFIC_RUN: broadening "
                   "recognition did not reclassify what already worked",
                   by_id["fixture_open"]["category"] == "SCIENTIFIC_RUN"
                   and by_id["fixture_open"]["schema"] == "argus-run-v1"))
        ck.append(("an unrecognized category is never in the served payload: only the "
                   "declared table's names appear", {x["category"] for x in e["runs"]
                                                     if not x["sealed"]} <= set(CATEGORIES)))

        ck.append(("SABOTAGE the per-run route REFUSES a sealed run with 423",
                   c.get("/api/evidence-index/fixture_sealed").status_code == 423))
        ck.append(("an unknown run is 404",
                   c.get("/api/evidence-index/nope").status_code == 404))
        ck.append(("SABOTAGE traversal and absolute paths in a run id are refused",
                   c.get("/api/evidence-index/..").status_code in (404, 422)
                   and c.get("/api/evidence-index/C:%5CWindows").status_code == 422))
        ck.append(("a nonsense sealed filter is refused",
                   c.get("/api/evidence-index",
                         params={"sealed": "maybe"}).status_code == 422))
        ck.append(("an out-of-range limit is refused",
                   c.get("/api/evidence-index",
                         params={"limit": 99999}).status_code == 422))
        idx_builds = INDEX.builds
        for _ in range(10):
            c.get("/api/evidence-index")
        ck.append(("SABOTAGE ten index requests do not walk the tree ten times",
                   INDEX.builds == idx_builds))

        st = c.get("/api/evidence-index/status").json()
        ck.append(("status reports both caches with their build counts and ages",
                   set(st["receipts"]) >= {"builds", "age_s", "ttl_s"}
                   and set(st["evidence_index"]) >= {"builds", "age_s", "ttl_s"}))

        ck.append(("the leak scan passes a clean payload", leaks({"a": ["b", 1]}) == []))
        ck.append(("SABOTAGE and it CATCHES a planted absolute path and credential",
                   len(leaks({"p": "C" + r":\Users\someone", "t": "gh" + "p_" + "ABCDEFGH12345678",
                              "auth_token": "x"})) >= 3))
        raised = False
        try:
            guarded({"leaked": _argus_public_path('repo', 'secret')})
        except LeakGuard:
            raised = True
        ck.append(("SABOTAGE and a route that tried to return one would RAISE, not send "
                   "it", raised))

        ck.append(("this service has no subprocess, eval, exec or shell surface",
                   J.execution_surface_offences(Path(__file__)) == []))
        verbs = set()
        for r_ in router.routes:
            try:
                verbs |= set(r_.methods)
            except AttributeError:
                pass
        ck.append(("no route here mutates anything; every one is a GET",
                   bool(verbs) and verbs <= {"GET", "HEAD", "OPTIONS"}))
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reset_caches()
        shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    for msg, good in ck:
        print("  %s %s" % ("PASS" if good else "FAIL", msg))
        ok &= bool(good)
    print("selftest: %d/%d passed" % (sum(1 for _, g in ck if g), len(ck)))
    return ok


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="ARGUS sanitized receipts")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(0 if selftest() else 1)
    print(json.dumps(build_receipts()["receipts"], indent=2)[:4000])
