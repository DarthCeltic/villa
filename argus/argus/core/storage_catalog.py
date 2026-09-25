"""Read-only resolution of material owned by the ARGUS storage conveyor."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import os
import sqlite3
import time
from pathlib import Path
from threading import RLock


CATALOG_DB = Path(os.environ.get(
    "ARGUS_STORAGE_CATALOG", _argus_public_path('home', 'state/storage/catalog.db')))
_LOCAL_STATES = frozenset({
    "HOT_PINNED", "LOCAL_UNARCHIVED", "ARCHIVING", "ARCHIVED_UNVERIFIED",
    "CLOUD_VERIFIED", "EVICTABLE", "HYDRATING", "LOCAL_VERIFIED",
    "BLOCKED_SECRET", "BLOCKED_ACTIVE", "BLOCKED_UNKNOWN",
})
_CACHE_TTL_S = 2.0
_lock = RLock()
_cache: tuple[float, tuple[dict, ...]] = (0.0, ())


def _rows(db_path: Path | None = None) -> tuple[dict, ...]:
    """Return a short-lived snapshot without taking a writer lock."""
    global _cache
    path = Path(db_path or CATALOG_DB)
    now = time.monotonic()
    with _lock:
        if db_path is None and now - _cache[0] < _CACHE_TTL_S:
            return _cache[1]
        if not path.is_file():
            rows: tuple[dict, ...] = ()
        else:
            conn = sqlite3.connect(
                "file:%s?mode=ro" % path.as_posix(), uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = tuple(dict(row) for row in conn.execute(
                    "select asset_id, logical_name, original_path, state, local_state, "
                    "logical_bytes, file_count, pinned, remote_folder, hydrate_cmd, "
                    "content_sha256, updated from assets"))
            finally:
                conn.close()
        if db_path is None:
            _cache = (now, rows)
        return rows


def asset(logical_name: str, *, db_path: Path | None = None) -> dict | None:
    """One exact logical asset record, or ``None`` when this install has no catalogue."""
    return next((dict(row) for row in _rows(db_path)
                 if row.get("logical_name") == logical_name), None)


def describe(logical_name: str, *, fallback: Path | None = None,
             db_path: Path | None = None) -> dict:
    """Resolve one asset without pretending an evicted path is available."""
    row = asset(logical_name, db_path=db_path)
    declared = Path(row["original_path"]) if row and row.get("original_path") else \
        (Path(fallback) if fallback is not None else None)
    present = bool(declared and declared.exists())
    return {
        "logical_name": logical_name,
        "catalogued": row is not None,
        "asset_id": row.get("asset_id") if row else None,
        "state": row.get("state") if row else ("UNTRACKED_LOCAL" if present else "ABSENT"),
        "path": str(declared) if declared else None,
        "present": present,
        "bytes": int(row.get("logical_bytes") or 0) if row else None,
        "files": int(row.get("file_count") or 0) if row else None,
        "pinned": bool(row.get("pinned")) if row else False,
        "remote": row.get("remote_folder") if row else None,
        "hydrate_cmd": row.get("hydrate_cmd") if row else None,
        "restorable": bool(row and row.get("hydrate_cmd")),
        "content_sha256": row.get("content_sha256") if row else None,
        "updated": row.get("updated") if row else None,
    }


def path(logical_name: str, *, fallback: Path | None = None,
         db_path: Path | None = None) -> Path:
    """Return the declared location, catalog first and path-contract fallback second."""
    record = describe(logical_name, fallback=fallback, db_path=db_path)
    if record["path"]:
        return Path(record["path"])
    raise KeyError("no catalogue or fallback path for %s" % logical_name)


def group(prefix: str, *, fallback_root: Path | None = None,
          db_path: Path | None = None) -> list[dict]:
    """All members of a logical material family, including absent/restorable members."""
    normalized = prefix.rstrip("/") + "/"
    found: dict[str, dict] = {}
    for row in _rows(db_path):
        logical = str(row.get("logical_name") or "")
        if not logical.startswith(normalized):
            continue
        leaf = logical[len(normalized):]
        if not leaf or "/" in leaf or "\\" in leaf:
            continue
        found[leaf] = describe(logical, db_path=db_path)
    root = Path(fallback_root) if fallback_root is not None else None
    if root and root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and child.name not in found:
                found[child.name] = describe(
                    normalized + child.name, fallback=child, db_path=db_path)
    return [dict(record, member=name) for name, record in sorted(found.items())]


def local_group_paths(prefix: str, *, fallback_root: Path | None = None,
                      db_path: Path | None = None) -> dict[str, Path]:
    """Present directories in a material family, keyed by logical leaf name."""
    out: dict[str, Path] = {}
    for record in group(prefix, fallback_root=fallback_root, db_path=db_path):
        p = Path(record["path"]) if record.get("path") else None
        if record.get("present") and p and p.is_dir() and \
                (record.get("state") in _LOCAL_STATES or
                 record.get("state") == "UNTRACKED_LOCAL"):
            out[str(record["member"])] = p
    return out


def fragments(*, fallback_root: Path | None = None,
              db_path: Path | None = None) -> list[dict]:
    """Return fragment assets across both catalogue naming generations."""
    found: dict[str, dict] = {}
    for row in _rows(db_path):
        logical = str(row.get("logical_name") or "")
        if logical.startswith("cold_input/fragments/"):
            member = logical.removeprefix("cold_input/fragments/")
        elif logical.startswith("cold_input/fragments_"):
            member = logical.removeprefix("cold_input/fragments_")
        else:
            continue
        if not member or "/" in member or "\\" in member:
            continue
        found[member] = dict(describe(logical, db_path=db_path), member=member)
    root = Path(fallback_root) if fallback_root is not None else None
    if root and root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and child.name not in found:
                found[child.name] = dict(describe(
                    "cold_input/fragments/%s" % child.name,
                    fallback=child, db_path=db_path), member=child.name)
    return [found[name] for name in sorted(found)]


def local_fragment_paths(*, fallback_root: Path | None = None,
                         db_path: Path | None = None) -> dict[str, Path]:
    """Present fragment directories, keyed by fragment identity."""
    out: dict[str, Path] = {}
    for record in fragments(fallback_root=fallback_root, db_path=db_path):
        p = Path(record["path"]) if record.get("path") else None
        if record.get("present") and p and p.is_dir() and \
                (record.get("state") in _LOCAL_STATES or
                 record.get("state") == "UNTRACKED_LOCAL"):
            out[str(record["member"])] = p
    return out


def clear_cache() -> None:
    """Testing hook; production callers rely on the two-second catalogue snapshot."""
    global _cache
    with _lock:
        _cache = (0.0, ())
