"""The operator's user-data cache, and the release chop that keeps it out of every export."""
from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import time

from argus.core import paths
from argus.core.safe_names import is_plain_file_name

CONTRACT = "argus-user-data-cache-v1"
DETACH_SCHEMA = "argus-user-data-detach-v1"
MANIFEST_NAME = "USER_DATA_MANIFEST.json"
POINTERS_NAME = "CREDENTIAL_POINTERS.json"
LIVE_WINDOW_S = 12 * 3600
MIN_HASH_BYTES = 16
CRLF, LF = bytes((13, 10)), bytes((10,))
DETACH_CONFIRMATION = "DETACH LOCAL USER DATA"


def mode_path() -> pathlib.Path:
    """Machine policy only."""
    return paths.argus_home("state", "USER_DATA_MODE.json")


def mode_state() -> dict:
    p = mode_path()
    if not p.is_file():
        return {"schema": DETACH_SCHEMA, "mode": "CONNECTED", "reason": "default", "path": str(p)}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"schema": DETACH_SCHEMA, "mode": "REFUSED", "reason": "mode file unreadable: %s" % exc,
                "path": str(p)}
    mode = doc.get("mode")
    if mode not in ("CONNECTED", "DETACHING", "DETACHED"):
        return {"schema": DETACH_SCHEMA, "mode": "REFUSED", "reason": "unknown mode", "path": str(p)}
    return dict(doc, schema=DETACH_SCHEMA, path=str(p))


USER_DATA_CATEGORIES = {
  "operator_notes": ("notes",
    "the operator's own working notes: a human account of the work, "
    "not evidence, and written for the operator rather than for a public reader.",
    ("roadmap",)),
  "grail_annotations": ("annotations",
    "notes written on the Grail Diary. Unverified by construction (kind OPERATOR_NOTE).",
    ("grail_annotations",)),
  "strategy": ("strategy",
    "operator strategy notes: private planning and prioritisation. Publishing them would disclose the operator's plans.",
    ("prize_strategy", "roadmap", "metadata_rank")),
  "research_lane": ("research_lane",
    "operator research-lane registers and internals: private, and they never leave.",
    ("research_lane",)),
  "acquisition": ("acquisition",
    "acquisition order, target-acquisition packets and research shortlists.",
    ("acquisition_order", "shortlist")),
  "candidate_maps": ("candidates",
    "candidate maps and candidate-mining output: where ARGUS thinks ink might be.",
    ("candidate_map",)),
  "identity": ("identity",
    "personal identity and bookkeeping names: collaborators, accounts, handoffs to named people.",
    ()),
  "credential_pointers": ("credentials",
    "WHERE credentials live and whether they are configured. Never a value: values stay in the OS "
    "keyring or their own files, and pointer-only items are never copied.",
    ("credential",)),
  "private_receipts": ("receipts",
    "receipts about private surfaces that are not needed to reproduce the public demo.",
    ()),
}

PUBLIC_CATEGORIES = {
  "official_scans": "official CT volumes, segments and surface volumes, and the chunk cache that "
                    "holds their bytes (paths.cache / public_cache_root).",
  "catalogue_metadata": "public catalogue and registry metadata fetched from upstream.",
  "upstream_checkouts": "pinned external checkouts such as Villa (paths.upstream).",
  "public_models": "released upstream checkpoints, governed by their own licences.",
  "code": "ARGUS source, tests and contracts.",
  "public_evidence": "receipts named one by one in release_exporter.ALLOW.",
}



class UserDataError(RuntimeError):
    """A refusal."""


class UserDataMissing(UserDataError):
    """Neither the user-data copy nor the legacy original exists."""


class UserDataDiverged(UserDataError):
    """Both the copy and the original changed after migration."""


@dataclasses.dataclass(frozen=True)
class Item:
    id: str
    category: str
    base: str
    rel: str
    kind: str = "file"
    writer: str | None = None
    pointer_only: bool = False
    switched_readers: tuple = ()
    tokens: tuple = ()
    note: str = ""


BUILTIN_ITEMS = (
  Item("grail_annotations", "grail_annotations", "home", "state/grail_annotations.json",
       writer="argus.core.annotations (POST /ui/annotations on the live service)",
       switched_readers=("argus/core/annotations.py",), tokens=("_annotations.json",)),
  Item("state_private", "operator_notes", "home", "state/private", kind="dir",
       note="the declared private state directory"),
  Item("secret_status", "credential_pointers", "home", "state/secret_status.json",
       writer="argus.core.secrets (set/validate/remove)",
       switched_readers=("argus/core/secrets.py",),
       note="presence, length and validation time per provider. Carries no value."),
  Item("command_token", "credential_pointers", "home", "state/command_token", pointer_only=True,
       note="the local command-service token. A credential value: pointer only."),
  Item("rclone_conf_backups", "credential_pointers", "home", "state/rclone_conf_backups",
       kind="dir", pointer_only=True, note="rclone configs carry OAuth tokens: pointer only."),
  Item("side_work_permit", "operator_notes", "home", "state/side_work_permit.json",
       note="an operator decision record read by the side-work gate."),
)


REGISTRY_NAME = "ITEM_REGISTRY.json"
_REG_CACHE: dict = {}


def registry_paths() -> list:
    """Where machine-specific item declarations are read from, user-data root first."""
    return [paths.user_data_root(REGISTRY_NAME, check=False),
            paths.artifact_write_root() / "user_data_cache" / REGISTRY_NAME]


def _load_registry(p: pathlib.Path) -> tuple:
    try:
        st = p.stat()
    except OSError:
        return ()
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key in _REG_CACHE:
        return _REG_CACHE[key]
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
        items = tuple(Item(**{k: (tuple(v) if isinstance(v, list) else v)
                              for k, v in r.items()}) for r in rows)
    except (OSError, ValueError, TypeError) as exc:
        raise UserDataError("the user-data item registry %s cannot be read: %s" % (p, exc))
    for k in [k for k in _REG_CACHE if k[0] == key[0]]:
        del _REG_CACHE[k]
    _REG_CACHE[key] = items
    return items


def _items() -> tuple:
    out, seen = list(BUILTIN_ITEMS), {i.id for i in BUILTIN_ITEMS}
    for rp in registry_paths():
        for it in _load_registry(rp):
            if it.id not in seen:
                seen.add(it.id)
                out.append(it)
    return tuple(out)


def base_root(base: str) -> pathlib.Path:
    if base == "home":
        return paths.argus_home()
    if base == "repo":
        return paths.repo()
    if base == "legacy":
        return paths.legacy()
    raise UserDataError("unknown item base %r" % base)


def _is_glob(rel: str) -> bool:
    return any(c in rel for c in "*?[")


@dataclasses.dataclass(frozen=True)
class Entry:
    """One concrete item: a declared Item, with any glob expanded to a single path."""
    item: Item
    rel: str

    @property
    def id(self) -> str:
        return self.item.id if not _is_glob(self.item.rel) else "%s:%s" % (self.item.id, self.rel)

    @property
    def source(self) -> pathlib.Path:
        return base_root(self.item.base).joinpath(*self.rel.split("/"))

    @property
    def destination(self) -> pathlib.Path:
        sub = USER_DATA_CATEGORIES[self.item.category][0]
        return paths.user_data_root(sub, self.item.base, *self.rel.split("/"))

    def dest_rel(self) -> str:
        return "/".join((USER_DATA_CATEGORIES[self.item.category][0], self.item.base, self.rel))


def expand(items=None) -> list:
    """Every concrete entry."""
    out = []
    for it in (items if items is not None else _items()):
        if it.category not in USER_DATA_CATEGORIES:
            raise UserDataError("item %s names category %r, which is not a USER_DATA category"
                                % (it.id, it.category))
        if _is_glob(it.rel):
            base = base_root(it.base)
            for p in sorted(base.glob(it.rel)):
                out.append(Entry(it, p.relative_to(base).as_posix()))
        else:
            out.append(Entry(it, it.rel))
    return out


def entry(item_id: str) -> Entry:
    for it in (*BUILTIN_ITEMS, *_items()):
        if it.id == item_id and not _is_glob(it.rel):
            if it.category not in USER_DATA_CATEGORIES:
                raise UserDataError("item %s names a non-USER_DATA category" % it.id)
            return Entry(it, it.rel)
    for e in expand():
        if e.id == item_id or (e.item.id == item_id and not _is_glob(e.item.rel)):
            return e
    raise UserDataError("no user-data item %r" % item_id)




def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def digests(p) -> set:
    """The sha256 of the bytes, plus -- for UTF-8 text -- of the text with CRLF folded to LF."""
    raw = pathlib.Path(p).read_bytes() if pathlib.Path(p).stat().st_size <= 64 << 20 else None
    if raw is None:
        return {sha256_file(p)}
    out = {hashlib.sha256(raw).hexdigest()}
    if CRLF in raw:
        try:
            raw.decode("utf-8")
            out.add(hashlib.sha256(raw.replace(CRLF, LF)).hexdigest())
        except UnicodeDecodeError:
            pass
    return out


def _files(p: pathlib.Path) -> list:
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(x for x in p.rglob("*") if x.is_file())
    return []


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(obj, p: pathlib.Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(obj, indent=1, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def manifest_path() -> pathlib.Path:
    return paths.user_data_root(MANIFEST_NAME)


def load_manifest() -> dict:
    p = manifest_path()
    if not p.is_file():
        return {"schema": "argus-user-data-manifest-v1", "contract": CONTRACT,
                "entries": {}, "pointers": {}, "runs": []}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UserDataError("the user-data manifest %s exists but cannot be read: %s" % (p, exc))
    for k, v in (("entries", {}), ("pointers", {}), ("runs", [])):
        doc.setdefault(k, v)
    return doc




@dataclasses.dataclass(frozen=True)
class Resolution:
    item_id: str
    path: pathlib.Path
    source: str
    user_copy: pathlib.Path
    legacy: pathlib.Path

    def as_record(self) -> dict:
        return {"item": self.item_id, "path": str(self.path), "source": self.source,
                "user_copy": str(self.user_copy), "legacy": str(self.legacy)}


def _changed_since(entries: list, side: str) -> bool:
    """Has any file recorded in `entries` changed on `side` ('source'|'destination')?"""
    root = paths.user_data_root()
    for rec in entries:
        p = pathlib.Path(rec["source"]) if side == "source" else root.joinpath(
            *rec["destination"].split("/"))
        if not p.is_file():
            return True
        st = p.stat()
        if side == "source" and st.st_size == rec.get("source_size") and \
                st.st_mtime_ns == rec.get("source_mtime_ns"):
            continue
        if sha256_file(p) != rec["sha256"]:
            return True
    return False


def resolve(item_id: str, *, allow_absent: bool = False) -> Resolution:
    """Prefer the user-data copy, fall back to the legacy original, refuse when both are gone."""
    e = entry(item_id)
    if e.item.pointer_only:
        raise UserDataError("%s is a credential pointer; it is never copied, so there is nothing "
                            "to resolve here. Read it where it lives." % item_id)
    copy, legacy = e.destination, e.source
    mode = mode_state().get("mode")
    if mode != "CONNECTED":
        if allow_absent:
            return Resolution(e.id, copy, "ABSENT", copy, legacy)
        raise UserDataMissing("%s is unavailable because the user-data layer is %s; legacy "
                              "fallback is disabled in that mode" % (item_id, mode))
    recs = [r for r in load_manifest()["entries"].values() if r.get("item") == e.id]
    if copy.exists() and legacy.exists() and recs:
        legacy_changed = _changed_since(recs, "source")
        copy_changed = _changed_since(recs, "destination")
        if legacy_changed and copy_changed:
            raise UserDataDiverged(
                "%s changed in BOTH places after migration (copy %s, original %s). Neither is "
                "chosen automatically; reconcile by hand." % (item_id, copy, legacy))
        if legacy_changed:
            return Resolution(e.id, legacy, "LEGACY_CHANGED_AFTER_MIGRATION", copy, legacy)
    if copy.exists():
        return Resolution(e.id, copy, "USER_DATA", copy, legacy)
    if legacy.exists():
        return Resolution(e.id, legacy, "LEGACY_FALLBACK", copy, legacy)
    if allow_absent:
        return Resolution(e.id, copy if recs else legacy, "ABSENT", copy, legacy)
    raise UserDataMissing("%s exists neither as a user-data copy (%s) nor at its original "
                          "location (%s)" % (item_id, copy, legacy))



_READER_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".mjs", ".ps1", ".sh")
_SELF_FILES = ("argus/core/user_data.py", "argus/cli/cmd_userdata.py",
               "scripts/user_data_cache.py")


def _reader_roots() -> list:
    r = paths.repo()
    return [("repo", r / "argus"), ("repo", r / "scripts"), ("repo", r / "tests"),
            ("home", paths.argus_home("scripts"))]


def _tokens(e: Entry) -> tuple:
    name = pathlib.PurePosixPath(e.rel).name
    return tuple(t for t in (name, *e.item.tokens) if len(t) >= 6)


def find_readers(entries: list) -> dict:
    """entry id -> files that mention it."""
    want = {e.id: _tokens(e) for e in entries if not e.item.pointer_only}
    hits = {k: [] for k in want}
    repo = paths.repo()
    for label, root in _reader_roots():
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in ("node_modules", "dist", "__pycache__", ".git")]
            for fn in filenames:
                if not fn.endswith(_READER_SUFFIXES):
                    continue
                fp = pathlib.Path(dirpath, fn)
                try:
                    if fp.stat().st_size > 2 << 20:
                        continue
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                try:
                    shown = fp.relative_to(repo).as_posix()
                except ValueError:
                    shown = fp.as_posix()
                if shown in _SELF_FILES:
                    continue
                for k, toks in want.items():
                    if any(t in text for t in toks):
                        hits[k].append(shown)
    return hits


def _classify_refs(e: Entry, refs: list) -> dict:
    prefix = pathlib.PurePosixPath(e.rel).name.split("_")[0].lower()
    tests, generators, switched, blocking = [], [], [], []
    for f in refs:
        name = pathlib.PurePosixPath(f).name.lower()
        if f in e.item.switched_readers:
            switched.append(f)
        elif "/tests/" in "/" + f or name.startswith("test_") or f.startswith("tests/"):
            tests.append(f)
        elif prefix.startswith("n") and prefix[1:].isdigit() and name.startswith(prefix + "_"):
            generators.append(f)
        else:
            blocking.append(f)
    return {"switched": switched, "tests": tests, "generator_scripts": generators,
            "unswitched_readers": blocking}


def inventory(*, readers: bool = True) -> dict:
    """Read-only."""
    entries = expand()
    refs = find_readers(entries) if readers else {}
    now = time.time()
    rows = []
    for e in entries:
        src = e.source
        row = {"id": e.id, "category": e.item.category, "base": e.item.base, "rel": e.rel,
               "source": str(src), "destination": str(e.destination), "kind": e.item.kind,
               "exists": src.exists(), "pointer_only": e.item.pointer_only,
               "declared_writer": e.item.writer, "note": e.item.note}
        if e.item.pointer_only:
            row.update({"bytes": None, "sha256": None, "files": None,
                        "why_no_hash": "credential-bearing: never hashed to disk, never sized"})
            row["eligible"], row["blocked_by"] = False, ["POINTER_ONLY"]
            rows.append(row)
            continue
        files = _files(src)
        row["files"] = len(files)
        row["bytes"] = sum(f.stat().st_size for f in files)
        row["newest_mtime_utc"] = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(
            max(f.stat().st_mtime for f in files))) if files else None)
        row["sha256"] = (sha256_file(src) if src.is_file() else
                         hashlib.sha256("".join(
                             "%s %s\n" % (f.relative_to(src).as_posix(), sha256_file(f))
                             for f in files).encode()).hexdigest() if files else None)
        blocked = []
        if not files:
            blocked.append("NOTHING_TO_COPY")
        if e.item.writer:
            blocked.append("DECLARED_LIVE_WRITER")
        if files and now - max(f.stat().st_mtime for f in files) < LIVE_WINDOW_S:
            blocked.append("MODIFIED_WITHIN_%dH" % (LIVE_WINDOW_S // 3600))
        if readers:
            row["readers"] = _classify_refs(e, refs.get(e.id, []))
            if row["readers"]["unswitched_readers"]:
                blocked.append("UNSWITCHED_READERS")
        else:
            blocked.append("READERS_NOT_SCANNED")
        row["eligible"], row["blocked_by"] = not blocked, blocked
        rows.append(row)
    by_cat = {}
    for r in rows:
        c = by_cat.setdefault(r["category"], {"items": 0, "bytes": 0, "present": 0})
        c["items"] += 1
        c["present"] += 1 if r["exists"] else 0
        c["bytes"] += r["bytes"] or 0
    return {"contract": CONTRACT, "utc": _utc(), "user_data_root": str(paths.user_data_root()),
            "public_cache_roots": [str(p) for p in paths.public_cache_roots()],
            "items": rows, "by_category": by_cat,
            "count": len(rows), "present": sum(1 for r in rows if r["exists"]),
            "bytes": sum(r["bytes"] or 0 for r in rows),
            "private_items_note": "private items are declared by path only; no private content was opened."}




def _under(p: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        rp, rr = p.resolve(), root.resolve()
    except OSError:
        return False
    return rp == rr or rr in rp.parents


def _refuse_public(e: Entry) -> None:
    for pub in paths.public_cache_roots():
        if _under(e.source, pub):
            raise UserDataError("%s lives under the public store %s; public data is never placed "
                                "in user data" % (e.id, pub))


def _copy_verified(src: pathlib.Path, dst: pathlib.Path) -> str:
    before = sha256_file(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".userdata_tmp")
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            fo.write(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    got, after = sha256_file(tmp), sha256_file(src)
    if not (before == got == after):
        raise UserDataError("copy of %s did not verify (source changed during copy or bytes "
                            "differ); the temporary copy is left at %s" % (src, tmp))
    os.replace(tmp, dst)
    if sha256_file(dst) != before:
        raise UserDataError("destination %s does not hash to its source after replace" % dst)
    return before


def migrate(*, dry_run: bool = True, only_eligible: bool = True, inv: dict | None = None,
            ids=None, actor: str = "argus userdata") -> dict:
    """Copy items into the cache."""
    inv = inv if inv is not None else inventory(readers=True)
    rows = {r["id"]: r for r in inv["items"]}
    man = load_manifest()
    plan, refused, copied, identical = [], [], [], []
    for e in expand():
        if ids is not None and e.id not in ids:
            continue
        row = rows.get(e.id) or {}
        if e.item.pointer_only:
            if not dry_run:
                man["pointers"][e.id] = {"category": e.item.category, "location": str(e.source),
                                         "exists": e.source.exists(), "never_copied": True,
                                         "recorded_utc": _utc()}
            continue
        if only_eligible and not row.get("eligible"):
            refused.append({"id": e.id, "blocked_by": row.get("blocked_by") or ["NOT_INVENTORIED"]})
            continue
        _refuse_public(e)
        src = e.source
        for f in _files(src):
            rel_inside = "" if src.is_file() else f.relative_to(src).as_posix()
            dest_rel = e.dest_rel() + ("/" + rel_inside if rel_inside else "")
            dst = paths.user_data_root(*dest_rel.split("/"))
            action = {"id": e.id, "category": e.item.category, "source": str(f),
                      "destination": str(dst), "bytes": f.stat().st_size}
            prev = man["entries"].get(dest_rel)
            if dst.is_file():
                d_sha, s_sha = sha256_file(dst), sha256_file(f)
                if d_sha == s_sha:
                    identical.append(action)
                    continue
                if not (prev and prev.get("sha256") == d_sha):
                    refused.append(dict(action, blocked_by=["DESTINATION_DIFFERS_FROM_SOURCE_AND_"
                                                            "MANIFEST"]))
                    continue
            plan.append(action)
            if dry_run:
                continue
            sha = _copy_verified(f, dst)
            st = f.stat()
            man["entries"][dest_rel] = {
                "item": e.id, "category": e.item.category, "source": str(f),
                "destination": dest_rel, "sha256": sha, "bytes": st.st_size,
                "source_size": st.st_size, "source_mtime_ns": st.st_mtime_ns,
                "copied_utc": _utc(), "verified": True, "actor": actor}
            copied.append(dict(action, sha256=sha))
    result = {"contract": CONTRACT, "dry_run": dry_run, "utc": _utc(),
              "user_data_root": str(paths.user_data_root()),
              "would_copy" if dry_run else "copied": plan if dry_run else copied,
              "already_identical": identical, "refused": refused,
              "bytes": sum(a["bytes"] for a in plan), "deleted": 0}
    if not dry_run:
        man["runs"].append({"utc": result["utc"], "copied": len(copied),
                            "already_identical": len(identical), "refused": len(refused),
                            "actor": actor})
        man["user_data_root"] = str(paths.user_data_root())
        _write_json(man, manifest_path())
        _write_json({"contract": CONTRACT, "pointers": man["pointers"],
                     "rule": "locations only; no value, no hash, no size"},
                    paths.user_data_root(USER_DATA_CATEGORIES["credential_pointers"][0],
                                         POINTERS_NAME))
    return result




def _norm(p) -> str:
    s = str(p).replace("\\", "/")
    while s.startswith("./") or s.startswith("/"):
        s = s[1:] if s.startswith("/") else s[2:]
    return s.lower()


def _rule_match(rel: str, e_rel: str, kind: str) -> bool:
    rel, e_rel = rel.lower(), e_rel.lower()
    if kind == "dir":
        return rel == e_rel or rel.startswith(e_rel.rstrip("/") + "/")
    return rel == e_rel or fnmatch.fnmatchcase(rel, e_rel)


def classify_path(path) -> dict | None:
    """The USER_DATA verdict for one path, or None."""
    raw = str(path)
    s = raw.replace("\\", "/")
    if pathlib.PurePosixPath(s).name.lower() in (MANIFEST_NAME.lower(), POINTERS_NAME.lower(),
                                                  REGISTRY_NAME.lower()):
        return {"category": "private_receipts", "rule": "USER_DATA:MANIFEST_FILE", "item": None}
    is_abs = pathlib.PureWindowsPath(raw).is_absolute() or s.startswith("/")
    candidates = []
    if is_abs:
        p = pathlib.Path(raw)
        if _under(p, paths.user_data_root(check=False)):
            return {"category": "user_data_root", "rule": "USER_DATA:UNDER_ROOT", "item": None}
        for base in ("repo", "legacy", "home"):
            try:
                candidates.append((base, p.resolve().relative_to(
                    base_root(base).resolve()).as_posix()))
            except (ValueError, OSError):
                pass
    else:
        rel = _norm(s)
        candidates = [("repo", rel), ("legacy", rel), ("home", rel)]
    for it in _items():
        for base, rel in candidates:
            if base == it.base and _rule_match(rel, it.rel, it.kind):
                return {"category": it.category, "rule": "USER_DATA:ITEM:%s" % it.id,
                        "item": it.id}
    return None


def user_data_hashes() -> dict:
    """sha256 -> item id, for every non-credential user-data file: manifest copies AND originals."""
    out = {}
    root = paths.user_data_root(check=False)
    for rec in load_manifest()["entries"].values():
        if rec.get("bytes", 0) >= MIN_HASH_BYTES:
            out[rec["sha256"]] = rec["item"]
            dst = root.joinpath(*rec["destination"].split("/"))
            if dst.is_file():
                for d in digests(dst):
                    out.setdefault(d, rec["item"])
    for e in expand():
        if e.item.pointer_only:
            continue
        for f in _files(e.source):
            if f.stat().st_size >= MIN_HASH_BYTES:
                for d in digests(f):
                    out.setdefault(d, e.id)
    return out


def release_exclusions() -> dict:
    """What every public export MUST exclude."""
    return {
      "contract": CONTRACT,
      "user_data_root": str(paths.user_data_root(check=False)),
      "path_rules": [{"base": it.base, "rel": it.rel, "kind": it.kind, "category": it.category,
                      "item": it.id} for it in _items()],
      "manifest_files": [MANIFEST_NAME, POINTERS_NAME, REGISTRY_NAME],
      "sha256": user_data_hashes(),
      "min_hash_bytes": MIN_HASH_BYTES,
      "how_to_apply": "refuse a source whose classify_path() is not None; after writing, run "
                      "verify_release(out) and refuse the build unless it reports clean.",
    }


def _credential_digests() -> set:
    """In-memory only."""
    out = set()
    for e in expand():
        if not e.item.pointer_only:
            continue
        for f in _files(e.source):
            try:
                if f.stat().st_size >= MIN_HASH_BYTES:
                    out |= digests(f)
            except OSError:
                continue
    return out


def verify_release(export_dir) -> dict:
    """Fail if any user-data file -- by path, by root, or by sha256 even when renamed -- is in it."""
    root = pathlib.Path(export_dir)
    if not root.is_dir():
        raise UserDataError("export directory %s does not exist" % root)
    hashes = user_data_hashes()
    creds = _credential_digests()
    findings, scanned = [], 0
    for f in _files(root):
        if ".git" in f.relative_to(root).parts:
            continue
        scanned += 1
        rel = f.relative_to(root).as_posix()
        hit = classify_path(rel)
        if hit is None and _under(f, paths.user_data_root(check=False)):
            hit = {"category": "user_data_root", "rule": "USER_DATA:UNDER_ROOT", "item": None}
        if hit:
            findings.append({"path": rel, "by": "PATH", **hit})
            continue
        if f.stat().st_size < MIN_HASH_BYTES:
            continue
        ds = digests(f)
        hit_d = next((d for d in ds if d in hashes), None)
        if hit_d:
            findings.append({"path": rel, "by": "SHA256", "item": hashes[hit_d],
                             "rule": "USER_DATA:CONTENT_HASH"})
        elif ds & creds:
            findings.append({"path": rel, "by": "SHA256", "item": "credential",
                             "rule": "USER_DATA:CREDENTIAL_CONTENT"})
    return {"contract": CONTRACT, "export_dir": str(root), "files_scanned": scanned,
            "user_data_hashes_known": len(hashes), "findings": findings,
            "clean": not findings, "utc": _utc()}


def status() -> dict:
    out = {"contract": CONTRACT, "mode": mode_state()}
    try:
        out["user_data_root"] = str(paths.user_data_root())
        out["root_ok"] = True
    except paths.PathContractViolation as exc:
        out["user_data_root"] = str(exc.path)
        out["root_ok"], out["root_refusal"] = False, exc.detail
    out["root_exists"] = pathlib.Path(out["user_data_root"]).is_dir()
    out["public_cache_roots"] = [str(p) for p in paths.public_cache_roots()]
    if out["root_ok"]:
        man = load_manifest()
        out["manifest"] = {"entries": len(man["entries"]), "pointers": len(man["pointers"]),
                           "runs": len(man["runs"]),
                           "bytes": sum(r.get("bytes", 0) for r in man["entries"].values())}
    out["categories"] = {k: {"subdir": v[0], "rationale": v[1]}
                         for k, v in USER_DATA_CATEGORIES.items()}
    out["public_categories"] = PUBLIC_CATEGORIES
    out["declared_items"] = len(_items())
    return out



def _detach_inventory(root: pathlib.Path) -> tuple[list[dict], str]:
    rows = []
    if root.is_dir():
        for p in sorted(x for x in root.rglob("*") if x.is_file()):
            st = p.stat()
            rows.append({"relative": p.relative_to(root).as_posix(), "bytes": st.st_size,
                         "mtime_ns": st.st_mtime_ns})
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return rows, hashlib.sha256(raw).hexdigest()


def detach_plan(params: dict | None = None) -> dict:
    """A stable, read-only plan for removing the personal layer from active ARGUS use."""
    params = params or {}
    unknown = sorted(set(params) - {"approved_plan_sha256", "confirmation"})
    root = paths.user_data_root()
    rows, fingerprint = _detach_inventory(root)
    quarantine = paths.argus_home("detached_user_data", fingerprint[:16])
    current = mode_state()
    ready = root.is_dir() and current.get("mode") == "CONNECTED" and not quarantine.exists() and not unknown
    plan = {
        "schema": DETACH_SCHEMA, "ready": ready, "read_only": True,
        "root": str(root), "quarantine": str(quarantine), "tree_fingerprint": fingerprint,
        "files": len(rows), "bytes": sum(r["bytes"] for r in rows),
        "changes": [
            "sets the private-data reader mode to DETACHING (legacy fallback closes first)",
            "atomically renames the active user-data root into a recoverable local quarantine",
            "sets the reader mode to DETACHED; public scans, caches, models, code and evidence are untouched",
        ],
        "keeps": ["public scan/cache bytes", "models and runtimes", "ARGUS source",
                  "historical evidence outside the user-data root"],
        "cost": {"gpu": "none", "network": "none", "seconds": "< 5",
                 "bytes_written": "one small mode marker; user-data bytes are renamed, not copied"},
        "deletes": 0, "network": "none", "confirmation_required": DETACH_CONFIRMATION,
        "may_refuse": ["UNKNOWN_PARAMETER", "NOT_CONNECTED", "ROOT_NOT_FOUND",
                       "QUARANTINE_EXISTS", "PLAN_CHANGED", "CONFIRMATION_REQUIRED"],
        "note": "This detaches personal material from ARGUS but does not erase it. Permanent deletion remains a separate operator decision.",
    }
    if unknown:
        plan["refusal"] = {"code": "UNKNOWN_PARAMETER", "why": "unknown parameters: %s" % unknown}
    elif current.get("mode") != "CONNECTED":
        plan["refusal"] = {"code": "NOT_CONNECTED", "why": "user-data mode is %s" % current.get("mode")}
    elif not root.is_dir():
        plan["refusal"] = {"code": "ROOT_NOT_FOUND", "why": "the active user-data root does not exist"}
    elif quarantine.exists():
        plan["refusal"] = {"code": "QUARANTINE_EXISTS", "why": "the exact quarantine target already exists"}
    hash_body = {k: v for k, v in plan.items() if k != "plan_sha256"}
    plan["plan_sha256"] = hashlib.sha256(json.dumps(hash_body, sort_keys=True,
                                                     separators=(",", ":")).encode()).hexdigest()
    return plan


def detach_execute(params: dict, *, actor: str) -> dict:
    plan = detach_plan(params)
    if not plan["ready"]:
        refusal = plan.get("refusal") or {"code": "NOT_READY", "why": "detach plan is not ready"}
        raise UserDataError("%s: %s" % (refusal["code"], refusal["why"]))
    if params.get("confirmation") != DETACH_CONFIRMATION:
        raise UserDataError("CONFIRMATION_REQUIRED: confirmation must exactly equal %r" % DETACH_CONFIRMATION)
    if params.get("approved_plan_sha256") != plan["plan_sha256"]:
        raise UserDataError("PLAN_CHANGED: approved_plan_sha256 does not equal the current detach plan")
    root, quarantine = pathlib.Path(plan["root"]), pathlib.Path(plan["quarantine"])
    marker = {"schema": DETACH_SCHEMA, "mode": "DETACHING", "actor": actor, "utc": _utc(),
              "tree_fingerprint": plan["tree_fingerprint"]}
    _write_json(marker, mode_path())
    try:
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        os.replace(root, quarantine)
    except OSError:
        _write_json(dict(marker, mode="CONNECTED", reason="rename failed; reader restored"), mode_path())
        raise
    done = dict(marker, mode="DETACHED", quarantine=str(quarantine), files=plan["files"],
                bytes=plan["bytes"], deleted=0,
                note="personal data is quarantined and recoverable; nothing was deleted")
    _write_json(done, mode_path())
    return {"status": "OK", **done}



IMPORTED_SEGMENTS_DIR = "imported_segments"
IMPORT_RECORD_NAME = "SEGMENT_IMPORT.json"
IMPORT_RECEIPT_NAME = "SEGMENT_IMPORT_RECEIPT.json"
_KEY_RE = re.compile(r"^[0-9a-f]{20}$")
_SCROLL_RE = re.compile(r"^[A-Za-z0-9]{1,40}$")


def imported_segments_root() -> pathlib.Path:
    return paths.user_data_root(IMPORTED_SEGMENTS_DIR)


def segment_import_inbox() -> pathlib.Path:
    """A folder the operator may drop segments into; always an allowed import root."""
    return paths.user_data_root("inbox", "segments")


def imported_segment_path(scroll: str, key: str) -> pathlib.Path:
    if not (_SCROLL_RE.fullmatch(str(scroll)) and _KEY_RE.fullmatch(str(key)) and is_plain_file_name(str(scroll)) and is_plain_file_name(str(key))):
        raise UserDataError("not a scroll id and segment key: %r %r" % (scroll, key))
    return imported_segments_root() / scroll / key / IMPORT_RECORD_NAME


def register_imported_segment(record: dict, receipt: dict) -> dict:
    """Write one record and its receipt."""
    rec_p = imported_segment_path(record["scroll"], record["segment_key"])
    rcp_p = rec_p.with_name(IMPORT_RECEIPT_NAME)
    if rec_p.exists() or rcp_p.exists():
        raise UserDataError("segment %s is already registered; records are append-only"
                            % record["segment_key"])
    _write_json(record, rec_p)
    _write_json(receipt, rcp_p)
    rel = lambda p: p.relative_to(paths.user_data_root()).as_posix()
    return {"record": rel(rec_p), "receipt": rel(rcp_p)}


def list_imported_segments() -> list:
    root = imported_segments_root()
    out = []
    for p in sorted(root.glob("*/*/" + IMPORT_RECORD_NAME)) if root.is_dir() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: r.get(k) for k in ("segment_key", "scroll", "registered_utc",
                                          "registered_by", "plan_sha256")}
                   | {"identity_basis": (r.get("identity") or {}).get("basis")})
    return out
