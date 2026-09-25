"""Every recorded block gets an immutable identity, so a duplicated id stops being a collision."""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

from argus.core import paths

CONTRACT = "argus-record-uid-v1"

UNIQUE = "UNIQUE"
AMBIGUOUS = "AMBIGUOUS_LEGACY_REFERENCE"
ABSENT = "NO_SUCH_RECORD"

HEADING = re.compile(
    r"^(#{2,4})[ \t]+(N-\d{3,5}[a-z]?)(?![\da-z])((?:\.\.N-\d{3,5}[a-z]?)?(?:/\d+)*)"
    r"[ \t]*([·|\-–—])?[ \t]*(.*)$",
    re.M)

PRESERVED_ORIGINAL = re.compile(r"\(as originally written\)", re.I)

MAPPING_PATH = paths.repo("corpus", "record_resolutions.json")


class RecordUidError(ValueError):
    """Raised rather than returning a best guess."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sort_key(legacy_id: str) -> tuple:
    """Numeric part first, letter suffix second."""
    m = re.match(r"^N-(\d+)([a-z]?)$", legacy_id)
    return (int(m.group(1)), m.group(2)) if m else (10 ** 9, legacy_id)


def uid(legacy_id: str, document: str, span_start: int, block_sha256: str) -> str:
    """The immutable identity of one recorded block."""
    basis = "%s|%s|%d|%s" % (legacy_id, document, span_start, block_sha256)
    return "%s@%s" % (legacy_id, hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12])


def records(path) -> list:
    """Every block in one document, in order, each with its uid and provenance."""
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    doc = p.name
    ms = list(HEADING.finditer(text))
    out = []
    for i, m in enumerate(ms):
        start = m.start()
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        body = text[start:end]
        sha = _sha(body)
        out.append({
          "legacy_id": m.group(2),
          "id_suffix": m.group(3) or "",
          "heading_level": len(m.group(1)),
          "separator": m.group(4) or "",
          "title": (m.group(5) or "").strip(),
          "document": doc,
          "span_start": start,
          "span_end": end,
          "bytes": end - start,
          "block_sha256": sha,
          "record_uid": uid(m.group(2), doc, start, sha),
        })
    return out


def index(documents=None) -> dict:
    """The whole record, keyed both ways: by uid, and by legacy id to a list of uids."""
    documents = documents or ()
    by_uid, by_legacy = {}, {}
    for d in documents:
        p = pathlib.Path(d)
        if not p.is_file():
            continue
        for r in records(p):
            by_uid[r["record_uid"]] = r
            by_legacy.setdefault(r["legacy_id"], []).append(r["record_uid"])
    return {"by_uid": by_uid, "by_legacy_id": by_legacy}


def _mapping() -> dict:
    if not MAPPING_PATH.is_file():
        return {}
    try:
        doc = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecordUidError("citation mapping exists but could not be read: %s" % exc)
    return doc.get("resolutions", {}) if isinstance(doc, dict) else {}


def resolve(legacy_id: str, *, document: str | None = None, idx: dict | None = None) -> dict:
    """What does a bare citation to this id mean?"""
    idx = idx or index()
    uids = list(idx["by_legacy_id"].get(legacy_id, ()))
    if document:
        uids = [u for u in uids if idx["by_uid"][u]["document"] == document]

    if not uids:
        return {"legacy_id": legacy_id, "status": ABSENT, "candidates": [],
                "why": "no record with this identifier exists in the documents searched."}

    per_doc = {}
    for u in uids:
        per_doc.setdefault(idx["by_uid"][u]["document"], []).append(u)
    primary = sorted(per_doc)[0]
    in_primary = per_doc[primary]

    if len(in_primary) > 1:
        live = [u for u in in_primary
                if not PRESERVED_ORIGINAL.search(idx["by_uid"][u]["title"])]
        if len(live) == 1:
            (only_live,) = live
            return {"legacy_id": legacy_id, "status": UNIQUE, "record_uid": only_live,
                    "candidates": uids, "primary_document": primary,
                    "resolved_by": "the other record is the preserved original of a retracted "
                                   "entry, kept beside its retraction and marked as such."}
        in_primary = live or in_primary

    if len(in_primary) == 1:
        (only,) = in_primary
        return {"legacy_id": legacy_id, "status": UNIQUE, "record_uid": only,
                "candidates": uids, "primary_document": primary,
                "mirrored_in": sorted(d for d in per_doc if d != primary)}
    uids = in_primary

    decided = _mapping().get(legacy_id)
    if decided and decided.get("record_uid") in uids:
        return {"legacy_id": legacy_id, "status": UNIQUE,
                "record_uid": decided["record_uid"], "candidates": uids,
                "resolved_by": "human review",
                "reviewer": decided.get("reviewer"), "utc": decided.get("utc"),
                "why": decided.get("why")}

    return {
      "legacy_id": legacy_id,
      "status": AMBIGUOUS,
      "candidates": uids,
      "candidate_detail": [{k: idx["by_uid"][u][k] for k in
                            ("record_uid", "document", "heading_level", "bytes",
                             "block_sha256", "title")} for u in uids],
      "why": "this identifier names more than one record. Nothing here selects between them: "
             "picking the first, the last or the longest would be a machine deciding what a "
             "citation meant.",
      "how_to_resolve": "add a human-reviewed entry to %s under 'resolutions', naming the "
                        "record_uid, the reviewer and the reason."
                        % MAPPING_PATH.as_posix(),
    }


def duplicates(idx: dict | None = None) -> dict:
    """Legacy ids naming more than one record, with proof the records really differ."""
    idx = idx or index()
    out = []
    for legacy, uids in sorted(idx["by_legacy_id"].items(), key=lambda kv: _sort_key(kv[0])):
        if len(uids) < 2:
            continue
        rows = [idx["by_uid"][u] for u in uids]
        per_doc = {}
        for r in rows:
            per_doc.setdefault(r["document"], []).append(r)
        colliding = {d: rs for d, rs in per_doc.items() if len(rs) > 1}
        if not colliding:
            continue
        out.append({
          "legacy_id": legacy,
          "documents": {d: [{"record_uid": r["record_uid"], "heading_level": r["heading_level"],
                             "bytes": r["bytes"], "block_sha256": r["block_sha256"],
                             "title": r["title"][:110]} for r in rs]
                        for d, rs in colliding.items()},
          "identical_text": all(len({r["block_sha256"] for r in rs}) == 1
                                for rs in colliding.values()),
          "resolution": resolve(legacy, idx=idx)["status"],
        })
    return {"count": len(out), "ids": [r["legacy_id"] for r in out], "rows": out}


def as_record() -> dict:
    idx = index()
    d = duplicates(idx)
    return {
      "contract": CONTRACT,
      "records": len(idx["by_uid"]),
      "legacy_ids": len(idx["by_legacy_id"]),
      "colliding_ids": d["count"],
      "colliding": d["ids"],
      "mapping_path": MAPPING_PATH.as_posix(),
      "mapping_present": MAPPING_PATH.is_file(),
      "unresolved_ambiguous": [r["legacy_id"] for r in d["rows"]
                               if r["resolution"] == AMBIGUOUS],
      "no_byte_of_history_changed": "this module only reads. Identity is DERIVED from the "
                                    "document's own bytes, so nothing had to be renumbered.",
    }
