"""Operator notes on the Grail Diary: the user's own writing, kept rigorously apart."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import os
import pathlib
import re
import time
import uuid
from importlib import import_module

STORE = pathlib.Path(_argus_public_path('home', 'state/grail_annotations.json'))

_DEFAULT_STORE = STORE

_ATTACH_DIRNAME = "grail_attachments"


def _attach_dir() -> pathlib.Path:
    return _store().parent / _ATTACH_DIRNAME


def _store() -> pathlib.Path:
    """Where notes are read and written right now."""
    if STORE != _DEFAULT_STORE:
        return STORE
    try:
        from argus.core import paths as _paths
        try:
            _ud = import_module("argus.core.user_data")
        except ModuleNotFoundError as exc:
            if exc.name == "argus.core.user_data":
                return _DEFAULT_STORE
            raise
        try:
            return _ud.resolve("grail_annotations", allow_absent=True).path
        except (_ud.UserDataError, _paths.PathContractViolation) as exc:
            raise AnnotationError("the annotation store cannot be resolved: %s" % exc)
    except _paths.PathContractViolation as exc:
        raise AnnotationError("the annotation store cannot be resolved: %s" % exc)


KIND = "OPERATOR_NOTE"
MAX_TEXT = 4000
MAX_TARGET = 300
MAX_NOTES = 5000

_TARGET_OK = re.compile(r"^[A-Za-z0-9 ._/\-]{1,%d}$" % MAX_TARGET)

ALLOWED_ATTACHMENT_MIME = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
    "video/mp4": ".mp4", "video/webm": ".webm",
}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 2000
_FILENAME_OK = re.compile(r"[^A-Za-z0-9 ._\-]")
MAX_FILENAME = 200


class AnnotationError(ValueError):
    """Refused."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read() -> dict:
    store = _store()
    if not store.is_file():
        return {"schema": "argus-grail-annotations-v1", "notes": [], "attachments": []}
    try:
        doc = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AnnotationError("the annotation store exists but could not be read; refusing to "
                              "overwrite it. Move %s aside to start fresh." % store)
    if not isinstance(doc, dict) or not isinstance(doc.get("notes"), list):
        raise AnnotationError("the annotation store is not in the expected shape; refusing to "
                              "overwrite it.")
    if not isinstance(doc.get("attachments"), list):
        doc["attachments"] = []
    return doc


def _write(doc: dict) -> None:
    """Atomic: write beside, fsync, replace."""
    store = _store()
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, store)


def _clean(text: str) -> str:
    """Plain text only, normalised."""
    if not isinstance(text, str):
        raise AnnotationError("note text must be a string")
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not t:
        raise AnnotationError("an empty note is not a note")
    if len(t) > MAX_TEXT:
        raise AnnotationError("note is %d characters; the limit is %d" % (len(t), MAX_TEXT))
    return "".join(c for c in t if c == "\n" or c == "\t" or ord(c) >= 32)


def _check_target(target: str) -> str:
    if not isinstance(target, str) or not _TARGET_OK.match(target or ""):
        raise AnnotationError(
          "target must be a surface path or item id of at most %d characters, using letters, "
          "digits, space, dot, underscore, slash or hyphen" % MAX_TARGET)
    if ".." in target:
        raise AnnotationError("a target may not contain '..'")
    return target


def add(target: str, text: str, *, author: str = "operator",
        supersedes: str | None = None, attachment_ids: list | None = None) -> dict:
    """Append one note."""
    target = _check_target(target)
    body = _clean(text)
    doc = _read()
    if len(doc["notes"]) >= MAX_NOTES:
        raise AnnotationError("the annotation store holds %d notes, which is the ceiling"
                              % MAX_NOTES)
    ids = list(attachment_ids or [])
    if ids:
        known = {a["id"] for a in doc["attachments"]}
        missing = [i for i in ids if i not in known]
        if missing:
            raise AnnotationError("unknown attachment id(s): %s" % ", ".join(missing))
    rec = {
      "id": uuid.uuid4().hex[:12],
      "kind": KIND,
      "target": target,
      "text": body,
      "author": str(author)[:120] or "operator",
      "utc": _now(),
      "hidden": False,
      "supersedes": None,
      "attachment_ids": ids,
      "not_evidence": "an operator note. Unverified by construction; never a finding, a "
                      "receipt, or an input to any derivation.",
    }
    if supersedes:
        prior = next((n for n in doc["notes"] if n.get("id") == supersedes), None)
        if prior is None:
            raise AnnotationError("cannot supersede %r: no such note" % supersedes)
        prior["superseded_by"] = rec["id"]
        rec["supersedes"] = supersedes
    doc["notes"].append(rec)
    doc["updated_utc"] = _now()
    _write(doc)
    return _resolve_attachments([rec], doc["attachments"])[0]


def hide(note_id: str) -> dict:
    """Hide a note from the panel."""
    doc = _read()
    rec = next((n for n in doc["notes"] if n.get("id") == note_id), None)
    if rec is None:
        raise AnnotationError("no note with id %r" % note_id)
    rec["hidden"] = True
    rec["hidden_utc"] = _now()
    doc["updated_utc"] = _now()
    _write(doc)
    return rec


def save_attachment(raw: bytes, mime: str, original_filename: str, *,
                     author: str = "operator") -> dict:
    """Store one photo/clip for the notebook."""
    if not isinstance(mime, str) or mime not in ALLOWED_ATTACHMENT_MIME:
        raise AnnotationError(
          "attachment type %r is not accepted; allowed: %s"
          % (mime, ", ".join(sorted(ALLOWED_ATTACHMENT_MIME))))
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise AnnotationError("attachment has no content")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise AnnotationError("attachment is %d bytes; the limit is %d"
                              % (len(raw), MAX_ATTACHMENT_BYTES))
    doc = _read()
    if len(doc["attachments"]) >= MAX_ATTACHMENTS:
        raise AnnotationError("the attachment store holds %d files, which is the ceiling"
                              % MAX_ATTACHMENTS)
    name = _FILENAME_OK.sub("_", str(original_filename or "attachment"))[:MAX_FILENAME] or "attachment"
    ext = ALLOWED_ATTACHMENT_MIME[mime]
    att_id = uuid.uuid4().hex[:16]
    path = _attach_dir()
    path.mkdir(parents=True, exist_ok=True)
    dest = path / (att_id + ext)
    tmp = path / (att_id + ext + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(bytes(raw))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, dest)
    rec = {
      "id": att_id,
      "mime": mime,
      "ext": ext,
      "size": len(raw),
      "filename": name,
      "author": str(author)[:120] or "operator",
      "utc": _now(),
    }
    doc["attachments"].append(rec)
    doc["updated_utc"] = _now()
    _write(doc)
    return rec


def attachment_meta(attachment_id: str) -> dict | None:
    return next((a for a in _read()["attachments"] if a.get("id") == attachment_id), None)


def read_attachment(attachment_id: str) -> tuple[bytes, str] | None:
    """The stored bytes and mime type, or None if the id is unknown."""
    meta = attachment_meta(attachment_id)
    if meta is None:
        return None
    p = _attach_dir() / (meta["id"] + meta["ext"])
    if not p.is_file():
        return None
    return p.read_bytes(), meta["mime"]


def _resolve_attachments(notes: list, atts: list) -> list:
    """Each note keeps only `attachment_ids`; the panel needs filename/mime/size to render a thumbnail or a link, so the full metadata (never the bytes) is joined in here, at read time, rather than..."""
    by_id = {a["id"]: a for a in atts}
    out = []
    for n in notes:
        m = dict(n)
        m["attachments"] = [by_id[i] for i in n.get("attachment_ids", []) if i in by_id]
        out.append(m)
    return out


def for_target(target: str) -> list:
    doc = _read()
    notes = [n for n in doc["notes"]
             if n.get("target") == target and not n.get("hidden")
             and not n.get("superseded_by")]
    return _resolve_attachments(notes, doc["attachments"])


def all_notes(*, include_hidden: bool = False) -> list:
    notes = _read()["notes"]
    return notes if include_hidden else [n for n in notes if not n.get("hidden")]


def grouped() -> dict:
    """Notes by target, which is the shape the panel renders."""
    doc = _read()
    out: dict = {}
    for n in all_notes():
        if n.get("superseded_by"):
            continue
        out.setdefault(n["target"], []).append(n)
    return {t: _resolve_attachments(ns, doc["attachments"]) for t, ns in out.items()}


def summary() -> dict:
    notes = all_notes()
    doc = _read()
    return {
      "schema": "argus-grail-annotations-v1",
      "store": str(_store()),
      "store_is_outside_the_repo": True,
      "count": len(notes),
      "targets": len({n["target"] for n in notes}),
      "attachments": len(doc["attachments"]),
      "kind": KIND,
      "what_these_are_not": "evidence. An operator note is a human sentence, unverified by "
                            "construction, stored outside every tree the receipt, corpus and "
                            "release machinery reads, so it cannot be mistaken for a finding.",
    }
