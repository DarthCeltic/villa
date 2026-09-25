"""Download each immutable hash once."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import contextlib
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import shutil
import threading
import time
import uuid

CONTRACT = "argus-content-store-v1"

ROOT = pathlib.Path(_argus_public_path('home', 'store'))
INDEX = ROOT / "index.json"

UPSTREAM = "UPSTREAM_REFETCHABLE"
DERIVED = "DERIVED_HERE"
KINDS = (UPSTREAM, DERIVED)


class StoreRefusal(RuntimeError):
    """Raised rather than silently deleting or silently re-downloading."""


def _sha(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


_INDEX_LOCK = threading.RLock()


def _index_present() -> bool:
    for attempt in range(250):
        try:
            return INDEX.is_file()
        except PermissionError:
            time.sleep(0.02)
    return True


def _index() -> dict:
    if not _index_present():
        return {"contract": CONTRACT, "objects": {}}
    try:
        for attempt in range(250):
            try:
                return json.loads(INDEX.read_text(encoding="utf-8"))
            except PermissionError:
                if attempt == 249:
                    raise
                time.sleep(0.02)
    except ValueError as exc:
        raise StoreRefusal("the content index exists but is unreadable (%s). Refusing to "
                           "rebuild it blind: the DERIVED_HERE entries cannot be recovered from "
                           "upstream." % exc)


def _write_index(doc: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_name("%s.%d.%s.tmp" % (INDEX.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(250):
            try:
                os.replace(tmp, INDEX)
                return
            except PermissionError:
                if attempt == 249:
                    raise
                time.sleep(0.02)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


_FULL_SHA = re.compile(r"[0-9a-f]{64}")


def path_for(sha256: str) -> pathlib.Path:
    """Sharded by the first two characters, so one directory never holds ten thousand files."""
    if not isinstance(sha256, str) or not _FULL_SHA.fullmatch(sha256):
        raise StoreRefusal("an object is named by its full lower-case sha256")
    return ROOT / "objects" / sha256[:2] / sha256


def _holds(path: pathlib.Path, sha256: str) -> bool:
    """Is a complete object with exactly this content at `path`?"""
    for attempt in range(250):
        try:
            return path.is_file() and _sha(path) == sha256
        except PermissionError:
            time.sleep(0.02)
    return False


def has(sha256: str) -> bool:
    if not isinstance(sha256, str) or not _FULL_SHA.fullmatch(sha256):
        return False
    p = path_for(sha256)
    return p.is_file() and p.stat().st_size > 0


def put(src, *, kind: str, source: str = "", revision: str = "",
        licence: str = "", expected_sha256: str | None = None, move: bool = False) -> dict:
    """Store bytes once."""
    if kind not in KINDS:
        raise StoreRefusal("kind must be one of %s; %r is not a decision this store can make "
                           "for you." % (list(KINDS), kind))
    src = pathlib.Path(src)
    if not src.is_file():
        raise StoreRefusal("no such file: %s" % src)

    got = _sha(src)
    if expected_sha256:
        wanted = expected_sha256.strip().lower()
        if not _FULL_SHA.fullmatch(wanted):
            raise StoreRefusal("expected_sha256 must be the full 64 hex characters; a prefix or a non-hex value would accept files it should not")
        expected_sha256 = wanted
    if expected_sha256 and got != expected_sha256:
        raise StoreRefusal(
          "content does not match the expected hash. Expected prefix %s, got %s. A revision "
          "says which version was ASKED for; the hash says which was RECEIVED, and those "
          "differ more often than anyone expects." % (expected_sha256, got))

    dest = path_for(got)
    if has(got):
        with _INDEX_LOCK:
            idx = _index()
            entry = idx["objects"].setdefault(got, {})
            entry.setdefault("kind", kind)
            entry.setdefault("first_seen_utc", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            entry["references"] = sorted(set(entry.get("references", []) + [str(src)]))
            _write_index(idx)
        return {"sha256": got, "path": str(dest), "already_held": True, "entry": entry}

    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in dest.parent.glob(dest.name + ".*.part"):
        with contextlib.suppress(OSError):
            if time.time() - stale.stat().st_ctime > 3600:
                stale.unlink()
    part = dest.with_name("%s.%d.%s.part" % (dest.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        shutil.copy2(str(src), str(part))
        if _sha(part) != got:
            raise StoreRefusal("the stored copy does not hash to the source. Nothing is trusted "
                               "that was not verified after the write.")
        try:
            os.replace(str(part), str(dest))
        except OSError:
            if not _holds(dest, got):
                raise
    finally:
        part.unlink(missing_ok=True)
    if move:
        src.unlink(missing_ok=True)

    with _INDEX_LOCK:
        idx = _index()
        idx["objects"][got] = {
          "kind": kind, "bytes": dest.stat().st_size, "source": source, "revision": revision,
          "licence": licence, "references": [str(src)],
          "first_seen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _write_index(idx)
    return {"sha256": got, "path": str(dest), "already_held": False,
            "entry": idx["objects"][got]}


def hydrate(sha256: str, dest, *, link: bool = True) -> dict:
    """Materialise held bytes at a path a tool expects, without a second copy where possible."""
    if not has(sha256):
        raise StoreRefusal("object %s is not held. Fetch it through the conveyor first: this "
                           "store does not reach the network." % sha256[:12])
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = path_for(sha256)
    if dest.exists():
        return {"sha256": sha256, "dest": str(dest), "created": False,
                "verified": _sha(dest) == sha256}
    method = "copy"
    if link:
        try:
            os.link(str(src), str(dest))
            method = "hardlink"
        except OSError:
            shutil.copy2(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))
    return {"sha256": sha256, "dest": str(dest), "created": True, "method": method,
            "verified": _sha(dest) == sha256,
            "why_a_link": "the same directory shape for zero additional bytes, instead of a second "
                          "copy made only to satisfy a folder convention."}


def evictable() -> list:
    """What may safely be deleted."""
    from argus.core import owned_process_tree as OPT
    idx = _index()
    leased = set()
    try:
        for row in OPT.audit().get("rows", []):
            if row.get("lease") and not row.get("clean"):
                leased.add(row.get("job"))
    except Exception:
        pass

    out = []
    for sha, e in idx["objects"].items():
        if e.get("kind") != UPSTREAM:
            continue
        if not (e.get("source") and e.get("revision")):
            continue
        out.append({"sha256": sha, "bytes": e.get("bytes"), "source": e.get("source"),
                    "revision": e.get("revision")})
    return sorted(out, key=lambda r: -(r.get("bytes") or 0))


def _citing_findings(sha256: str) -> list:
    """Every recorded finding whose own text cites this exact hash."""
    from argus.core import paths, record_uid as RU
    hits = set()
    for rel in tuple(r for r in os.environ.get("ARGUS_FINDINGS_DOCS", "").split(os.pathsep) if r):
        p = paths.repo(rel)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for r in RU.records(p):
            if sha256 in text[r["span_start"]:r["span_end"]]:
                hits.add(r["legacy_id"])
    return sorted(hits)


def _tombstoned_findings(sha256: str) -> set:
    """finding_ids whose `raw` field already carries an append-only correction naming this hash -- a real tombstone, not a guess that one exists."""
    from argus.core import field_corrections as FC
    try:
        corrections = FC.load().get("corrections", [])
    except FC.CorrectionRefusal:
        return set()
    return {c["finding_id"] for c in corrections
            if c.get("field") == "raw" and sha256 in (c.get("original_value") or "")}


def evict(sha256: str) -> dict:
    with _INDEX_LOCK:
        return _evict_locked(sha256)


def _evict_locked(sha256: str) -> dict:
    idx = _index()
    e = idx["objects"].get(sha256)
    if e is None:
        raise StoreRefusal("object %s is not in the index" % sha256[:12])
    citing = _citing_findings(sha256)
    uncovered = [i for i in citing if i not in _tombstoned_findings(sha256)]
    if uncovered:
        raise StoreRefusal(
          "object %s is cited as raw evidence by %s with no append-only correction (tombstone) "
          "recorded for its raw field yet. Record one (argus.core.field_corrections) explaining "
          "the eviction before evicting, so the citation is corrected on purpose rather than "
          "going dangling." % (sha256[:12], ", ".join(uncovered)))
    if e.get("kind") == DERIVED:
        raise StoreRefusal(
          "object %s was produced HERE. It has no upstream to re-download it from, and the "
          "standing rule is that no local source is deleted until a remote copy is verified. "
          "Evicting it trades a recoverable problem for an unrecoverable one." % sha256[:12])
    if not (e.get("source") and e.get("revision")):
        raise StoreRefusal("object %s records no source and revision, so it cannot be "
                           "re-fetched. Refusing to evict something that cannot come back."
                           % sha256[:12])
    p = path_for(sha256)
    freed = p.stat().st_size if p.is_file() else 0
    p.unlink(missing_ok=True)
    e["evicted_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    e["present"] = False
    _write_index(idx)
    return {"sha256": sha256, "freed_bytes": freed, "refetchable_from": e.get("source"),
            "revision": e.get("revision")}


def as_record() -> dict:
    idx = _index()
    objs = idx.get("objects", {})
    held = {k: v for k, v in objs.items() if has(k)}
    return {
      "contract": CONTRACT, "root": str(ROOT),
      "objects_indexed": len(objs), "objects_held": len(held),
      "bytes_held": sum(v.get("bytes", 0) for v in held.values()),
      "derived_here": sum(1 for v in held.values() if v.get("kind") == DERIVED),
      "upstream_refetchable": sum(1 for v in held.values() if v.get("kind") == UPSTREAM),
      "evictable_bytes": sum(r["bytes"] or 0 for r in evictable()),
      "the_store_never_reaches_the_network": "fetching is the conveyor's job, with its consent "
                                             "modes. This module holds bytes and hands them "
                                             "out.",
    }
