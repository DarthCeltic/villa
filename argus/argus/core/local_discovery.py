"""Bounded discovery of scroll-related material already present on this machine."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Iterable

from argus.core import paths
from argus.core import scroll_ids

SCHEMA = "argus-local-discovery-v1"
MAX_FILES_PER_ROOT = 12000
MAX_DEPTH = 9
MAX_DIRS_PER_ROOT = 1800
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_HASH_BYTES = 256 * 1024 * 1024
TTL_S = 90.0
_SCAN_LOCK = threading.Lock()

_SKIP = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "cache", "caches", "models", "data", "chunks", "zarr", "dist", "build",
})
_SCROLL_RE = re.compile(r"PHerc[0-9A-Za-z]+", re.IGNORECASE)
_ALT_SCROLL_RE = re.compile(
    r"(PHerc[0-9A-Za-z]+)\s*[_-]\s*(?:or|vs|and)\s*[_-]?\s*(PHerc)?([0-9A-Za-z]+)",
    re.IGNORECASE,
)
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})
_MESH_EXTS = frozenset({".ply", ".obj", ".glb", ".gltf"})
_DOC_EXTS = frozenset({".json", ".md", ".txt", ".yaml", ".yml"})
_INDEX_NAMES = frozenset({
    "images_index.json", "integration_import_manifest.json",
})
_WORKTREE_HINTS = ("release", "rc", "argus", "villa")
_CACHE: tuple[float, dict] | None = None
_HASHES: dict[tuple[str, int, int], str] = {}
_PATHS: dict[str, Path] = {}
TRUSTED_EXTERNAL_SERVE_POLICIES = frozenset({"HASH_BOUND_PRIVATE_INDEX", "HASHED_UPSTREAM_STATIC"})


def _sha(path: Path) -> str:
    st = path.stat()
    key = (str(path), int(st.st_size), int(st.st_mtime_ns))
    if key in _HASHES:
        return _HASHES[key]
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(block)
    value = h.hexdigest()
    _HASHES[key] = value
    return value


def _canonical(value: str) -> str | None:
    try:
        return scroll_ids.resolve(value)
    except (KeyError, TypeError):
        return None


def _scrolls(values: Iterable[str]) -> tuple[list[str], list[str]]:
    resolved, unresolved = [], []
    for raw in values:
        text = str(raw)
        hits = list(_SCROLL_RE.findall(text))
        for first, prefix, suffix in _ALT_SCROLL_RE.findall(text):
            hits.append(first)
            hits.append(f"PHerc{suffix}" if not prefix else f"{prefix}{suffix}")
        for hit in hits:
            item = _canonical(hit)
            if item and item not in resolved:
                resolved.append(item)
            elif not item and hit not in unresolved:
                unresolved.append(hit)
    return sorted(resolved), sorted(unresolved)


def _roots() -> list[tuple[str, Path]]:
    raw = os.environ.get("ARGUS_DISCOVERY_ROOTS", "")
    extra = os.environ.get("ARGUS_DISCOVERY_EXTRA_ROOTS", "")
    candidates: list[tuple[str, Path]] = []
    if raw:
        for i, entry in enumerate(raw.split(os.pathsep)):
            if entry.strip():
                candidates.append((f"configured-{i + 1}", Path(entry.strip())))
    else:
        repo = paths.root("ARGUS_REPO")
        candidates.append(("argus-worktrees", repo.parent / "worktrees"))
        candidates.append(("argus-artifacts", repo / "artifacts"))
        candidates.append(("argus-user-data", paths.user_data_root()))
        candidates.append(("argus-runs", paths.root("ARGUS_RUNS_ROOT")))
        candidates.append(("argus-science-data", paths.root("ARGUS_SCIENCE_DATA_ROOT")))
    for i, entry in enumerate(extra.split(os.pathsep)):
        if entry.strip():
            candidates.append((f"extra-{i + 1}", Path(entry.strip())))
    seen: set[str] = set()
    out = []
    for label, path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            out.append((label, resolved))
    return out


def _display(label: str, root: Path, path: Path) -> str:
    try:
        return f"{label}/{path.relative_to(root).as_posix()}"
    except ValueError:
        return f"{label}/{path.name}"


def _record(path: Path, *, label: str, root: Path, scrolls: list[str], unresolved: list[str],
            kind: str, source: str | None = None, expected_sha: str | None = None,
            note: str | None = None, serve_policy: str | None = None) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    actual = None
    status = "LOCAL_UNVERIFIED"
    if st.st_size <= MAX_HASH_BYTES:
        try:
            actual = _sha(path)
        except OSError:
            status = "LOCAL_UNREADABLE"
    if expected_sha:
        status = "LOCAL_VERIFIED" if actual == expected_sha else "HASH_MISMATCH"
    elif actual:
        status = "LOCAL_HASHED"
    key = f"{path}|{actual or expected_sha or st.st_size}"
    asset_id = hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:32]
    _PATHS[asset_id] = path.resolve()
    identity = "RESOLVED" if len(scrolls) == 1 and not unresolved else (
        "AMBIGUOUS" if len(scrolls) > 1 else "UNRESOLVED")
    declared = _within_any(path, paths.serve_roots())
    trusted_external = serve_policy in TRUSTED_EXTERNAL_SERVE_POLICIES
    viewable = bool(kind == "RENDER" and status in {"LOCAL_VERIFIED", "LOCAL_HASHED"}
                    and (declared or trusted_external))
    return {
        "asset_id": asset_id,
        "physical_scroll": scrolls[0] if len(scrolls) == 1 and not unresolved else None,
        "identity_state": identity,
        "identity_candidates": scrolls,
        "unresolved_tokens": unresolved,
        "kind": kind,
        "status": status,
        "name": path.name,
        "bytes": int(st.st_size),
        "sha256": actual or expected_sha,
        "display_path": _display(label, root, path),
        "source": source,
        "note": note,
        "viewable": viewable,
        "preview_url": f"/api/discovery/file/{asset_id}" if viewable else None,
        "serve_policy": "DECLARED_ROOT" if declared else serve_policy,
    }


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _within_any(path: Path, roots: Iterable[Path]) -> bool:
    return any(_within(path, root) for root in roots)


def _walk(root: Path) -> tuple[list[Path], bool]:
    out: list[Path] = []
    truncated = False
    directories = 0
    if not root.is_dir():
        return out, False
    root_depth = len(root.parts)
    for current, dirs, files in os.walk(root, topdown=True):
        directories += 1
        if directories > MAX_DIRS_PER_ROOT:
            return out, True
        if Path(current).resolve() == root.resolve() and root.name.lower() == "worktrees":
            dirs[:] = [d for d in dirs if any(h in d.lower() for h in _WORKTREE_HINTS)]
            dirs[:] = sorted(
                dirs,
                key=lambda d: (
                    0 if "villa" in d.lower() else
                    1 if "argus" in d.lower() else 2,
                    d.lower(),
                ),
            )
        dirs[:] = [
            d for d in dirs
            if (d not in _SKIP or (
                d.lower() == "data"
                and Path(current).name.lower() == "img"
                and Path(current).parent.name.lower() == "static"
            ))
            and not d.startswith(".")
        ]
        if len(Path(current).parts) - root_depth > MAX_DEPTH:
            dirs[:] = []
        for name in files:
            if len(out) >= MAX_FILES_PER_ROOT:
                truncated = True
                return out, truncated
            p = Path(current) / name
            if p.suffix.lower() in {".zarr", ".ckpt", ".pth", ".pt", ".safetensors", ".npz", ".nrrd"}:
                continue
            lower = name.lower()
            context = f"{current} {name}".lower()
            if (not _SCROLL_RE.search(context) and lower not in _INDEX_NAMES
                    and not lower.endswith("_render_index.json")
                    and not any(token in context for token in ("images_index",))):
                continue
            out.append(p)
    return out, truncated


def _json_refs(path: Path, *, label: str, root: Path) -> list[dict]:
    if path.suffix.lower() != ".json" or path.stat().st_size > MAX_INDEX_BYTES:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    trusted_index = raw.get("schema") == "argus-private-images-index-v1"
    images = raw.get("images")
    if not isinstance(images, dict):
        return []
    out = []
    for key, ref in images.items():
        if not isinstance(ref, dict) or not ref.get("private_path") or not ref.get("sha256"):
            continue
        scrolls, unresolved = _scrolls([key, str(ref.get("private_path")), str(path)])
        target = Path(str(ref["private_path"]))
        if not target.is_absolute():
            continue
        item = _record(target, label="indexed-private", root=target.anchor and Path(target.anchor) or target.parent,
                       scrolls=scrolls, unresolved=unresolved, kind="RENDER", source=_display(label, root, path),
                       expected_sha=str(ref["sha256"]), note="indexed by a local hash-bound image manifest",
                       serve_policy="HASH_BOUND_PRIVATE_INDEX" if trusted_index else None)
        if item:
            item["source"] = _display(label, root, path)
            item["panels"] = ref.get("panels")
            item["shape"] = ref.get("shape")
            out.append(item)
    return out


def scan(*, force: bool = False) -> dict:
    global _CACHE
    now = time.monotonic()
    if not force and _CACHE and now - _CACHE[0] < TTL_S:
        return _CACHE[1]
    with _SCAN_LOCK:
        now = time.monotonic()
        if not force and _CACHE and now - _CACHE[0] < TTL_S:
            return _CACHE[1]
        _PATHS.clear()
        assets: list[dict] = []
        seen_ids: set[str] = set()
        roots = []
        skipped = []
        for label, root in _roots():
            if not root.is_dir():
                skipped.append({"label": label, "reason": "ROOT_NOT_FOUND"})
                continue
            files, truncated = _walk(root)
            roots.append({"label": label, "display_path": label, "files_considered": len(files), "truncated": truncated})
            for path in files:
                token_text = f"{path.name} {path.parent}"
                raw_scrolls, raw_unknown = _scrolls([token_text])
                refs = _json_refs(path, label=label, root=root)
                for item in refs:
                    if item["asset_id"] not in seen_ids:
                        seen_ids.add(item["asset_id"])
                        assets.append(item)
                if not raw_scrolls and not raw_unknown:
                    continue
                ext = path.suffix.lower()
                kind = "RENDER" if ext in _IMAGE_EXTS else (
                    "MESH" if ext in _MESH_EXTS else ("SCIENCE_RECORD" if ext in _DOC_EXTS else "OTHER")
                )
                if kind == "OTHER":
                    continue
                item = _record(path, label=label, root=root, scrolls=raw_scrolls, unresolved=raw_unknown,
                               kind=kind, note="discovered by bounded local metadata scan",
                               serve_policy=("HASHED_UPSTREAM_STATIC" if kind == "RENDER" and
                                   "/scrollprize.org/static/img/" in path.as_posix().lower() else None))
                if item and item["asset_id"] not in seen_ids:
                    seen_ids.add(item["asset_id"])
                    assets.append(item)
        assets.sort(key=lambda x: (x.get("physical_scroll") or "~", x["kind"], x["display_path"]))
        by_scroll: dict[str, int] = {}
        for item in assets:
            if item.get("physical_scroll"):
                by_scroll[item["physical_scroll"]] = by_scroll.get(item["physical_scroll"], 0) + 1
        result = {
            "schema": SCHEMA,
            "read_only": True,
            "scanned_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "roots": roots,
            "skipped_roots": skipped,
            "limits": {"max_files_per_root": MAX_FILES_PER_ROOT, "max_dirs_per_root": MAX_DIRS_PER_ROOT, "max_depth": MAX_DEPTH,
                        "raw_arrays_skipped": True},
            "assets": assets,
            "by_scroll": by_scroll,
            "counts": {
                "assets": len(assets),
                "renders": sum(1 for x in assets if x["kind"] == "RENDER"),
                "verified_renders": sum(1 for x in assets if x["kind"] == "RENDER" and x["status"] == "LOCAL_VERIFIED"),
                "viewable_renders": sum(1 for x in assets if x["kind"] == "RENDER" and x.get("viewable")),
                "resolved_scrolls": len(by_scroll),
                "unresolved": sum(1 for x in assets if x["identity_state"] != "RESOLVED"),
            },
            "next": "Select a resolved scroll to open its discovered assets in Workbench; identity-ambiguous files require operator confirmation.",
        }
        _CACHE = (now, result)
        return result


def file_for(asset_id: str) -> dict | None:
    inventory = scan()
    item = next((row for row in inventory.get("assets", [])
                 if row.get("asset_id") == asset_id and row.get("kind") == "RENDER"), None)
    path = _PATHS.get(asset_id)
    if not item or not path:
        return None
    try:
        policy_ok = item.get("serve_policy") in TRUSTED_EXTERNAL_SERVE_POLICIES
        if ((_within_any(path, paths.serve_roots()) or policy_ok) and path.is_file()
                and _sha(path) == item.get("sha256")):
            return {"path": path, "record": item}
    except OSError:
        return None
    return None
