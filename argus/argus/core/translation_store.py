"""Durable, append-only record of translation CANDIDATES a person drafted -- proposals, never a reading."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from argus.core import translation_plan as TP

FILENAME = "TRANSLATION_CANDIDATES.jsonl"
SCHEMA = "argus-translation-candidate-record-v1"
LABEL = "PROPOSAL"
HUMAN_ROLE = "HUMAN_JUDGMENT"
HUMAN_CLASSES = ("OPERATOR", "SPECIALIST", "TRUSTED_COLLABORATOR", "COMMUNITY")

NOT_A_READING = ("a proposal drafted by a named person from human-agreed letters on accepted regions. "
                 "It is not a reading of an unread scroll, not a detector result and not prize "
                 "evidence; it says nothing beyond the regions it cites.")


class TranslationStoreError(ValueError):
    pass


def candidates_path(target_dir) -> Path:
    return Path(target_dir) / FILENAME


def load_candidates(target_dir) -> list:
    p = candidates_path(target_dir)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    superseded = {c.get("supersedes") for c in out if c.get("supersedes")}
    for c in out:
        c["superseded"] = c.get("id") in superseded
    return out


def record_candidate(target_dir, *, plan: dict, source_token_ids: list, text: str,
                     alternatives: list | None, proposed_by: str, proposed_by_class: str,
                     board_sha256: str | None = None, supersedes: str | None = None) -> dict:
    """Validate through `propose_candidate`, then append."""
    if not str(proposed_by or "").strip():
        raise TranslationStoreError("a translation candidate is attributed to a named person")
    if proposed_by_class not in HUMAN_CLASSES:
        raise TranslationStoreError(
            "%r cannot draft a translation candidate: only a person can (%s). There is no machine "
            "translation of Ancient Greek or Latin in this system, and a model's text does not "
            "become HUMAN_JUDGMENT by being pasted in." % (proposed_by_class, ", ".join(HUMAN_CLASSES)))
    cand = TP.propose_candidate(plan, source_token_ids=source_token_ids, text=text,
                                alternatives=alternatives)
    if cand.get("state") != "PROPOSED":
        raise TranslationStoreError(cand.get("why") or "the candidate was refused")
    prior = load_candidates(target_dir)
    if supersedes and not any(c.get("id") == supersedes for c in prior):
        raise TranslationStoreError("supersedes %r names no recorded candidate" % supersedes)
    body = {
        "schema": SCHEMA, "label": LABEL, "evidence_role": HUMAN_ROLE, "origin": "HUMAN_DRAFT",
        "candidate": cand, "language": plan.get("language"),
        "transcription_id": plan.get("transcription_id"), "board_sha256": board_sha256,
        "proposed_by": str(proposed_by).strip(), "proposed_by_class": proposed_by_class,
        "supersedes": supersedes, "is_a_reading_of_an_unread_scroll": False,
        "what_this_is": NOT_A_READING,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    body["id"] = "TC-" + hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    p = candidates_path(target_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, (json.dumps(body, sort_keys=True, default=str) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return body
