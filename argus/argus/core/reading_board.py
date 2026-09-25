"""A spatial board of reviewed regions."""
from __future__ import annotations

import hashlib

CONTRACT_ID = "argus-reading-board-v1"

CLASSES = ("READING_BOARD", "REVIEWED_REGIONS", "SPATIAL_LAYOUT", "TRANSCRIPTION")

PLACEABLE_STATES = ("ACCEPTED", "EXPERT_VALIDATED", "TRAINING_ELIGIBLE")


class BoardRefusal(RuntimeError):
    """Raised rather than producing a board that asserts more than its evidence."""


def assemble(regions: list, *, orientation: str) -> dict:
    """Lay accepted regions out in their true spatial relationship."""
    if not regions:
        raise BoardRefusal(
            "no regions. An empty board and 'nothing is written here' are different "
            "statements, and only one of them is about the scroll.")

    bad = [r for r in regions if r.get("state") not in PLACEABLE_STATES]
    if bad:
        raise BoardRefusal(
            "%d region(s) are not in a placeable state %s. They are refused rather than "
            "skipped: dropping them silently would make the board look complete when part of "
            "its input was never accepted." % (len(bad), list(PLACEABLE_STATES)))

    from argus.core import sealed_review_queue as SQ
    try:
        SQ.assert_promoted(regions)
    except SQ.UnreviewedTaskRefused as exc:
        raise BoardRefusal(str(exc)) from exc

    cells = []
    for r in sorted(regions, key=lambda x: (x["extent"][0], x["extent"][2])):
        y0, y1, x0, x1 = r["extent"]
        cells.append({
          "cell_id": "cell-" + hashlib.sha256(
            ("%s|%s" % (orientation, r["extent"])).encode()).hexdigest()[:10],
          "extent": r["extent"],
          "row_origin_px": y0, "col_origin_px": x0,
          "height_px": y1 - y0, "width_px": x1 - x0,
          "state": r["state"],
          "review": {"answers": r.get("answers"), "reviewers": r.get("reviewer_classes"),
                     "task_id": r.get("task_id")},
          "traces_back_to": {
            "surface_extent": r["extent"],
            "orientation": orientation,
            "ink_evidence": r.get("evidence"),
            "ct": r.get("ct_reference"),
          },
        })

    return {
      "contract": CONTRACT_ID,
      "class": "READING_BOARD",
      "orientation": orientation,
      "cells": cells,
      "cell_count": len(cells),
      "this_is_not": ["a transcription", "OCR output", "readable text"],
      "why_not": "those words assert that something was read. This is a spatial layout of "
                 "regions people accepted. Whether the marks on it are letters is a judgement "
                 "made by looking, and the name of the object must not make it for them.",
      "every_cell_traces_back": "board cell -> surface extent -> CT location -> the ink "
                                "evidence and the review that accepted it. A board whose cells "
                                "cannot be traced is a picture.",
      "only_accepted_regions": "ranked candidates and high-probability tiles are NOT on this "
                               "board. A board assembled from unreviewed candidates would be "
                               "the detector's opinion arranged prettily.",
    }


def claim_transcription(board: dict, *, human_review: dict,
                        derived_from_review: bool = False) -> dict:
    """Promote a board to TRANSCRIPTION."""
    if not isinstance(human_review, dict):
        raise BoardRefusal("human_review must be a record, not a flag")
    humans = int(human_review.get("independent_human_answers") or 0)
    expert = bool(human_review.get("expert_validated"))
    agreement = float(human_review.get("agreement") or 0.0)

    if derived_from_review:
        if human_review.get("computed_from") != "merged_review_tally":
            raise BoardRefusal(
                "this claim's human_review was not computed from the merged review tally. "
                "Counts a person typed are not a tally.")
        if int(human_review.get("cells_covered") or -1) != int(board.get("cell_count") or 0):
            raise BoardRefusal(
                "human_review covers %s cell(s) but the board has %s. A claim about part of a "
                "board is not a claim about the board."
                % (human_review.get("cells_covered"), board.get("cell_count")))
        validators = human_review.get("expert_validators")
        if expert and not (isinstance(validators, list) and validators and all(
                str(v.get("reviewer_id") or "").strip() and v.get("reviewer_class") in (
                    "OPERATOR", "SPECIALIST") for v in validators)):
            raise BoardRefusal(
                "expert validation must be an attributed field: at least one named expert "
                "validator (OPERATOR or SPECIALIST) on record. An unattributed validation is a "
                "flag someone set.")

    if humans < 2:
        raise BoardRefusal(
            "a transcription needs at least two INDEPENDENT HUMAN answers; got %d. One vote is "
            "not truth, and an AI consensus is not a substitute -- a model grading its own "
            "family's output is not an independent observation." % humans)
    if not expert:
        raise BoardRefusal(
            "a transcription needs expert validation. Without it this remains a reading board, "
            "which is an honest object.")
    if agreement < 0.8:
        raise BoardRefusal(
            "reviewer agreement is %.2f. Below 0.8 the reviewers do not agree on what is "
            "written, and a transcription asserting otherwise would be averaging a "
            "disagreement." % agreement)

    return {**board, "class": "TRANSCRIPTION", "promoted_by": human_review,
            "what_this_still_is_not": "a claim about an unread scroll. Promotion says people "
                                      "agreed on what these accepted regions show; it says "
                                      "nothing about detector qualification."}
