"""Durable record of the one human decision that promotes a real, assembled `argus.core.reading_board` board into a transcription claim (`reading_board.claim_transcription`'s own `human_review`..."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

FILENAME = "BOARD_PROMOTION.json"
SCHEMA = "argus-board-promotion-v1"
CLAIMS_FILENAME = "BOARD_CLAIMS.jsonl"
CLAIM_SCHEMA = "argus-board-claim-v1"


class BoardPromotionError(ValueError):
    pass


def promotion_path(target_dir) -> Path:
    return Path(target_dir) / FILENAME


def record_promotion(target_dir, *, independent_human_answers: int, expert_validated: bool,
                     agreement: float, reviewer_id: str, why: str) -> dict:
    """A real, disclosed record of a human's explicit decision to promote a board -- never a flag flipped in passing."""
    p = promotion_path(target_dir)
    if p.exists():
        raise BoardPromotionError(
            "a board promotion record already exists at %s; promotion records are "
            "append-only and are never overwritten" % p)
    record = {
        "schema": SCHEMA,
        "human_review": {"independent_human_answers": int(independent_human_answers),
                         "expert_validated": bool(expert_validated),
                         "agreement": float(agreement)},
        "reviewer_id": reviewer_id, "why": why,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record


def load_promotion(target_dir) -> dict | None:
    """The real, stored promotion record for `target_dir`, or `None` if none was ever recorded -- never fabricated, never inferred from anything else."""
    p = promotion_path(target_dir)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None



class BoardClaimError(ValueError):
    pass


def claims_path(target_dir) -> Path:
    return Path(target_dir) / CLAIMS_FILENAME


def board_sha256(board: dict) -> str:
    """Identity of the exact board (cells, their review task, state and glyph) a claim is about."""
    cells = [[c.get("cell_id"), c.get("extent"), c.get("state"),
              (c.get("review") or {}).get("task_id"), (c.get("glyph") or {}).get("char")]
             for c in board.get("cells", [])]
    return hashlib.sha256(json.dumps(
        [board.get("orientation"), cells], sort_keys=True, default=str).encode("utf-8")).hexdigest()


def load_review_claims(target_dir) -> list:
    p = claims_path(target_dir)
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
    return out


def active_claim(target_dir, current_board_sha256: str | None) -> dict:
    """{'state': NONE | ACTIVE | STALE, 'claim': record or None}."""
    claims = load_review_claims(target_dir)
    if not claims:
        return {"state": "NONE", "claim": None}
    last = claims[-1]
    if current_board_sha256 and last.get("board_sha256") == current_board_sha256:
        return {"state": "ACTIVE", "claim": last}
    return {"state": "STALE", "claim": last,
            "why": "the board changed after this claim (a region, a state or a letter is not what "
                   "was claimed). The claim is kept as history and is not a claim about this board."}


def record_review_claim(target_dir, *, board: dict, human_review: dict, claimed_by: str,
                        claimed_by_class: str, why: str, transcription: dict | None = None) -> dict:
    """Persist a transcription claim whose `human_review` was COMPUTED from the merged review tally."""
    from argus.core import reading_board as RB
    if not str(claimed_by or "").strip():
        raise BoardClaimError("a transcription claim is attributed to a named person")
    try:
        promoted = RB.claim_transcription(board, human_review=human_review,
                                          derived_from_review=True)
    except RB.BoardRefusal as exc:
        raise BoardClaimError(str(exc)) from exc
    sha = board_sha256(board)
    prior = load_review_claims(target_dir)
    if any(c.get("board_sha256") == sha for c in prior):
        raise BoardClaimError("this exact board was already claimed by %s; claims are append-only "
                              "and are not repeated" % prior[-1].get("claimed_by"))
    record = {
        "schema": CLAIM_SCHEMA, "class": promoted["class"], "board_sha256": sha,
        "board": promoted, "human_review": human_review,
        "claimed_by": str(claimed_by).strip(), "claimed_by_class": claimed_by_class,
        "evidence_role": "HUMAN_JUDGMENT", "why": why,
        "transcription": transcription,
        "not": ["a claim about an unread scroll", "detector qualification",
                "prize eligibility", "independent physical ground truth"],
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p = claims_path(target_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, (json.dumps(record, sort_keys=True, default=str) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return record
