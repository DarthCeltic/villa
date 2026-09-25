"""A human-gated translation worksheet, downstream of accepted transcription only."""
from __future__ import annotations

import hashlib

SCHEMA = "argus-translation-plan-v1"
CANDIDATE_SCHEMA = "argus-translation-candidate-v1"


def build(*, transcription: dict | None = None, language: str | None = None) -> dict:
    """Return a translation handoff without inventing unread text."""
    base = {"schema": SCHEMA, "read_only": True, "state": "INPUT_REQUIRED",
            "language": language, "translation": None,
            "controls": ["accepted-input-only", "language identification", "human review",
                          "source-to-phrase citations"]}
    why = _unpromoted_reason(transcription)
    if why:
        base.update(state="REFUSED", why=why,
                    next="a sealed review task is consumed only after two named people have reviewed it")
        return base
    if not isinstance(transcription, dict):
        base.update(why="an accepted transcription with provenance is required",
                    next="accept glyphs independently in Review before opening translation")
        return base
    glyphs = transcription.get("glyphs")
    accepted = transcription.get("accepted") is True
    provenance = transcription.get("provenance")
    if not accepted or not isinstance(glyphs, list) or not glyphs or not provenance:
        base.update(state="REFUSED",
                    why="translation accepts only a non-empty independently accepted glyph "
                        "sequence with provenance; no text was inferred",
                    next="complete independent glyph acceptance and attach evidence links")
        return base
    if not language or not str(language).strip():
        base.update(state="INPUT_REQUIRED", why="target language must be declared",
                    next="declare the language before a reviewer drafts a translation")
        return base
    token_ids = [_token_id(g, i) for i, g in enumerate(glyphs)]
    base.update(state="READY_FOR_REVIEW", transcription_id=transcription.get("id"),
                glyph_count=len(glyphs), accepted_token_ids=token_ids,
                next="a reviewer may draft phrase candidates, each linked to accepted glyphs")
    return base


def _unpromoted_reason(obj) -> str | None:
    """Why `obj` may not be consumed, when it names a sealed review task that has not been promoted."""
    from argus.core import sealed_review_queue as SQ
    try:
        SQ.assert_promoted(obj)
    except SQ.UnreviewedTaskRefused as exc:
        return str(exc)
    return None


def _token_id(glyph, index: int) -> str:
    """Derive a stable citation id for one accepted glyph, dict-shaped or bare."""
    if isinstance(glyph, dict):
        return str(glyph.get("id") or glyph.get("cell_id") or index)
    return str(glyph)


def from_reading_board(board: dict | None) -> dict:
    """Adapt a real :func:`argus.core.reading_board.claim_transcription` result for ``build``."""
    if not isinstance(board, dict):
        return {"accepted": False}
    why = _unpromoted_reason(board)
    if why:
        return {"accepted": False, "promoted_by": None, "glyphs": [], "provenance": [], "refused": why}

    cells = board.get("cells")
    promoted_by = board.get("promoted_by")
    is_promoted_transcription = (
        board.get("class") == "TRANSCRIPTION"
        and isinstance(promoted_by, dict)
        and isinstance(cells, list)
        and len(cells) > 0
    )
    if not is_promoted_transcription:
        return {"accepted": False, "promoted_by": promoted_by,
                "glyphs": [], "provenance": []}

    glyphs = [
        {"id": cell.get("cell_id"), "extent": cell.get("extent"), "state": cell.get("state"),
         "char": (cell.get("glyph") or {}).get("char")}
        for cell in cells
    ]
    provenance = [cell.get("traces_back_to") for cell in cells]
    cell_ids = sorted(str(cell.get("cell_id")) for cell in cells)
    transcription_id = "translation-input-" + hashlib.sha256(
        ("%s|%s" % (board.get("orientation"), cell_ids)).encode()).hexdigest()[:12]

    return {"id": transcription_id, "accepted": True, "glyphs": glyphs,
            "provenance": provenance, "promoted_by": promoted_by}


def propose_candidate(plan: dict, *, source_token_ids: list, text: str,
                       alternatives: list | None = None) -> dict:
    """Record one reviewer-drafted phrase candidate against a ``READY_FOR_REVIEW`` plan."""
    why = _unpromoted_reason([plan, source_token_ids])
    if why:
        return {"schema": CANDIDATE_SCHEMA, "state": "REFUSED", "read_only": False, "why": why}
    if not isinstance(plan, dict) or plan.get("state") != "READY_FOR_REVIEW":
        return {"schema": CANDIDATE_SCHEMA, "state": "REFUSED", "read_only": False,
                "why": "a candidate may only be proposed against a plan in READY_FOR_REVIEW; "
                       "translation cannot outrun accepted transcription"}

    accepted_ids = set(plan.get("accepted_token_ids") or [])
    ids = [str(i) for i in (source_token_ids or [])]
    if not ids:
        return {"schema": CANDIDATE_SCHEMA, "state": "REFUSED", "read_only": False,
                "why": "a candidate must cite at least one accepted source token"}
    unknown = [i for i in ids if i not in accepted_ids]
    if unknown:
        return {"schema": CANDIDATE_SCHEMA, "state": "REFUSED", "read_only": False,
                "why": "citation(s) %s do not name any accepted source token" % unknown}
    if not text or not str(text).strip():
        return {"schema": CANDIDATE_SCHEMA, "state": "REFUSED", "read_only": False,
                "why": "a candidate needs phrase text"}
    if alternatives is None:
        alternatives = []
    if not isinstance(alternatives, list):
        return {"schema": CANDIDATE_SCHEMA, "state": "REFUSED", "read_only": False,
                "why": "alternatives must be a declared list, even if empty"}

    return {"schema": CANDIDATE_SCHEMA, "state": "PROPOSED", "read_only": False,
            "transcription_id": plan.get("transcription_id"),
            "text": str(text), "source_token_ids": ids, "alternatives": list(alternatives)}
