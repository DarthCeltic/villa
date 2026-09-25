"""Small auditable corrections to a surface proposal, mapped to explicit 3D constraints."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time

CONTRACT = "argus-surface-corrections-v1"

ACTIONS = (
  "CLICK_CORRECT_SHEET",
  "MOVE_ALONG_NORMAL",
  "DRAW_CENTERLINE",
  "MARK_SHEET_SWITCH",
  "SPLIT_COMPONENT",
  "MERGE_COMPONENTS",
  "MARK_NEITHER_USABLE",
)

CONSTRAINT_KIND = {
  "CLICK_CORRECT_SHEET": "SURFACE_PASSES_THROUGH",
  "MOVE_ALONG_NORMAL":   "SURFACE_OFFSET_ALONG_NORMAL",
  "DRAW_CENTERLINE":     "SURFACE_FOLLOWS_POLYLINE",
  "MARK_SHEET_SWITCH":   "SURFACE_DISCONTINUOUS_AT",
  "SPLIT_COMPONENT":     "COMPONENT_IS_NOT_ONE_SHEET",
  "MERGE_COMPONENTS":    "COMPONENTS_ARE_ONE_SHEET",
  "MARK_NEITHER_USABLE": "NO_USABLE_PROPOSAL_HERE",
}

PROMOTION_STATES = (
  "PROPOSED",
  "PAIRWISE_PREFERRED",
  "HUMAN_CORRECTED",
  "INDEPENDENTLY_VALIDATED",
  "FROZEN_SUPERVISION",
)
PROMOTION_INDEX = {s: i for i, s in enumerate(PROMOTION_STATES)}
TRAINABLE_STATE = "FROZEN_SUPERVISION"

CLICK_TOLERANCE_VOXELS = 2.0


REVIEW_LAB_CORRESPONDENCE = {
  "PROPOSED": "PROPOSED",
  "PAIRWISE_PREFERRED": "no review_lab equivalent -- a preference is not a review task outcome",
  "HUMAN_CORRECTED": "CONSENSUS_REACHED is the NEAREST, and it is not the same: consensus means "
                     "reviewers agreed about a task, correction means somebody changed a "
                     "surface.",
  "INDEPENDENTLY_VALIDATED": "EXPERT_OR_CONTROL_VALIDATED",
  "FROZEN_SUPERVISION": "FROZEN_IN_DATASET_VERSION",
}

BOTH_CHAINS_AGREE_ON = (
  "only the LAST state of either chain may enter a training contract. "
  "review_lab.TRAINING_ELIGIBLE is NOT that state -- it means a task's answers are eligible to "
  "be frozen, not that they have been."
)


class CorrectionRefusal(ValueError):
    """Raised when a correction would become something it has not earned."""


def _sha(obj) -> str:
    return hashlib.sha256(
      json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclasses.dataclass(frozen=True)
class CoordinateTransform:
    """How a 2D view coordinate becomes a voxel in the immutable volume."""

    volume_id: str
    array_path: str
    crop_row0: int
    crop_col0: int
    depth_offset: int
    depth_planes: int
    voxel_um: float

    def to_voxel(self, view_row: float, view_col: float, plane: int) -> tuple:
        if not 0 <= plane < self.depth_planes:
            raise CorrectionRefusal(
              "plane %d is outside the %d-plane window this view was drawn on. A correction "
              "placed outside its own window is not clipped into range -- that would move it "
              "somewhere the reviewer never looked." % (plane, self.depth_planes))
        return (self.depth_offset + int(plane),
                self.crop_row0 + float(view_row),
                self.crop_col0 + float(view_col))

    @property
    def transform_sha256(self) -> str:
        return _sha(dataclasses.asdict(self))


@dataclasses.dataclass(frozen=True)
class Correction:
    """One reviewer action, preserved whole."""

    correction_id: str
    proposal_id: str
    proposal_sha256: str
    action: str
    payload: dict
    reviewer_id: str
    reviewer_class: str
    transform: CoordinateTransform
    utc: str
    view_hash: str = ""

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise CorrectionRefusal(
              "unknown action %r. The set is closed (%s) so every correction maps to a known "
              "constraint kind rather than to free-form intent."
              % (self.action, ", ".join(ACTIONS)))
        if len(self.proposal_sha256) != 64:
            raise CorrectionRefusal(
              "a correction must pin the EXACT proposal it corrects by full hash. Without that "
              "it cannot be replayed, and a correction nobody can replay is one nobody can "
              "check.")

    @property
    def correction_sha256(self) -> str:
        d = dataclasses.asdict(self)
        d["transform"] = self.transform.transform_sha256
        return _sha(d)


def _need(payload: dict, *keys):
    missing = [k for k in keys if k not in payload]
    if missing:
        raise CorrectionRefusal("action payload is missing %s" % missing)


def to_constraint(c: Correction) -> dict:
    """Turn one action into an explicit 3D constraint through the recorded transform."""
    kind = CONSTRAINT_KIND[c.action]
    base = {
      "contract": CONTRACT,
      "constraint_kind": kind,
      "correction_id": c.correction_id,
      "correction_sha256": c.correction_sha256,
      "proposal_id": c.proposal_id,
      "proposal_sha256": c.proposal_sha256,
      "reviewer_id": c.reviewer_id, "reviewer_class": c.reviewer_class,
      "utc": c.utc,
      "volume_id": c.transform.volume_id,
      "array_path": c.transform.array_path,
      "transform_sha256": c.transform.transform_sha256,
      "view_hash": c.view_hash,
      "is_a_label": False,
      "what_it_asserts": None,
    }
    p = c.payload

    if c.action == "CLICK_CORRECT_SHEET":
        _need(p, "view_row", "view_col", "plane")
        base["voxel"] = c.transform.to_voxel(p["view_row"], p["view_col"], p["plane"])
        base["tolerance_voxels"] = float(p.get("tolerance_voxels", CLICK_TOLERANCE_VOXELS))
        base["what_it_asserts"] = (
          "the sheet surface passes within %.1f voxels of this point. It does NOT assert that "
          "this voxel is ink, nor that neighbouring voxels are or are not on the sheet."
          % base["tolerance_voxels"])

    elif c.action == "MOVE_ALONG_NORMAL":
        _need(p, "view_row", "view_col", "plane", "offset_voxels")
        base["voxel"] = c.transform.to_voxel(p["view_row"], p["view_col"], p["plane"])
        base["offset_voxels"] = float(p["offset_voxels"])
        base["offset_um"] = float(p["offset_voxels"]) * c.transform.voxel_um
        base["axis"] = "LOCAL_SURFACE_NORMAL"
        base["what_it_asserts"] = (
          "at this point the proposal tracks the right sheet but sits %.2f voxels (%.2f um) "
          "off along the LOCAL NORMAL. The sign is relative to the proposal's own outward "
          "normal, which is a property of the proposal -- it says nothing about the physical "
          "recto/verso sense of the papyrus, which remains UNKNOWN."
          % (base["offset_voxels"], base["offset_um"]))

    elif c.action == "DRAW_CENTERLINE":
        _need(p, "points", "plane")
        pts = list(p["points"])
        if len(pts) < 2:
            raise CorrectionRefusal("a centerline needs at least two points")
        base["polyline"] = [c.transform.to_voxel(r, col, p["plane"]) for r, col in pts]
        base["tolerance_voxels"] = float(p.get("tolerance_voxels", CLICK_TOLERANCE_VOXELS))
        base["what_it_asserts"] = (
          "the sheet runs along this short path within tolerance. It constrains WHERE the sheet "
          "goes, not how thick it is and not where it ends.")

    elif c.action == "MARK_SHEET_SWITCH":
        _need(p, "view_row", "view_col", "plane")
        base["voxel"] = c.transform.to_voxel(p["view_row"], p["view_col"], p["plane"])
        base["what_it_asserts"] = (
          "the proposal changes wraps at this point, so the surface is discontinuous here even "
          "though the proposal is continuous. It does not say which side is correct.")

    elif c.action == "SPLIT_COMPONENT":
        _need(p, "component_id")
        base["component_id"] = p["component_id"]
        base["at_voxel"] = (c.transform.to_voxel(p["view_row"], p["view_col"], p["plane"])
                            if {"view_row", "view_col", "plane"} <= set(p) else None)
        base["what_it_asserts"] = (
          "this proposed component spans more than one sheet. It does not say how many, nor "
          "where every boundary lies -- only that one is present.")

    elif c.action == "MERGE_COMPONENTS":
        _need(p, "component_ids")
        ids = list(p["component_ids"])
        if len(ids) < 2:
            raise CorrectionRefusal("a merge needs at least two components")
        base["component_ids"] = ids
        base["what_it_asserts"] = (
          "these proposed components are parts of ONE sheet. It does not assert that the sheet "
          "is complete, or that nothing else belongs to it.")

    else:
        base["what_it_asserts"] = (
          "no proposal shown here is worth correcting. This is a real and useful answer: it "
          "routes the ROI to re-segmentation instead of spending reviewer time polishing "
          "something that cannot be salvaged.")
        base["routes_to"] = "RE_SEGMENTATION"

    base["promotion_state"] = "HUMAN_CORRECTED" if c.reviewer_class != "AI_AGENT" else "PROPOSED"
    base["may_train"] = False
    base["why_not_trainable"] = (
      "a correction is a constraint, not supervision. It reaches HUMAN_CORRECTED and stops "
      "there; the distance to FROZEN_SUPERVISION is independent validation.")
    return base


def assert_promotable(from_state: str, to_state: str, *, human_validated: bool,
                      independent_validators: int) -> str:
    """One step at a time, and never into training without independent human validation."""
    for s in (from_state, to_state):
        if s not in PROMOTION_INDEX:
            raise CorrectionRefusal("unknown promotion state %r" % s)
    i, j = PROMOTION_INDEX[from_state], PROMOTION_INDEX[to_state]
    if j != i + 1:
        raise CorrectionRefusal(
          "%s -> %s skips %d state(s). Each state exists because something has to happen "
          "between it and the next, and skipping is how a preference becomes a label."
          % (from_state, to_state, j - i - 1) if j > i else
          "%s -> %s is not a promotion" % (from_state, to_state))
    if to_state == "INDEPENDENTLY_VALIDATED" and independent_validators < 2:
        raise CorrectionRefusal(
          "independent validation needs at least two independent validators; %d supplied. One "
          "validator agreeing with one corrector is two opinions, not corroboration."
          % independent_validators)
    if to_state == TRAINABLE_STATE and not human_validated:
        raise CorrectionRefusal(
          "only human-validated material may be frozen as supervision. An AI consensus, however "
          "large, is agreement among models and not evidence about papyrus.")
    return to_state


def may_enter_training(state: str) -> bool:
    """The single question a training contract asks of any review-derived material."""
    return state == TRAINABLE_STATE


def record(corrections) -> dict:
    """The whole correction set, in the only shape it may be handed on in."""
    cs = list(corrections)
    return {
      "contract": CONTRACT,
      "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
      "count": len(cs),
      "constraints": [to_constraint(c) for c in cs],
      "promotion_states": list(PROMOTION_STATES),
      "only_this_state_may_train": TRAINABLE_STATE,
      "preference_votes_are_never_labels": (
        "a ranking fitted from preferences may reach PAIRWISE_PREFERRED and no further. It "
        "prioritises which candidates and which uncertain cases get attention; it does not "
        "manufacture truth."),
      "what_was_never_asked_for": (
        "a full 128^3 segmentation. That request yields either nothing or a confident dense "
        "annotation whose author was guessing in most of its voxels, indistinguishable "
        "afterwards from one they were sure about."),
    }
