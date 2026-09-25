"""What a forward/reverse orientation pair does and does not mean."""
from __future__ import annotations

from dataclasses import dataclass


class OrientationSemanticsViolation(RuntimeError):
    """Raised when a caller asks for a physical claim the measurement cannot support."""


SANCTIONED_NAMES = (
  "depth_order_sensitivity",
  "orientation_disagreement",
  "either_direction_union",
)

PHYSICAL_CLAIM_NAMES = ("recto", "verso", "recto_verso", "front_back", "opposite_face")


def refuse_physical_face_claim(name: str, *, physical_proof: str | None = None) -> str:
    """Gate on naming."""
    low = name.lower()
    if any(tok in low for tok in PHYSICAL_CLAIM_NAMES):
        if not physical_proof:
            raise OrientationSemanticsViolation(
                "%r asserts a physical face. A forward/reverse pair measures DEPTH-ORDER "
                "SENSITIVITY; the global normal sign is arbitrary per component "
                "(see argus.core.normal_orientation), so reversal is not automatically the "
                "opposite physical face. Use one of %s, or supply physical_proof describing "
                "what independently established the face." % (name, list(SANCTIONED_NAMES)))
    return name


@dataclass(frozen=True)
class OrientationPair:
    """Forward and reverse results held apart, with the union kept as its own quantity."""

    forward: dict
    reverse: dict
    union: dict | None = None
    components_per_cm2: dict | None = None
    registered_mapping: str | None = None

    def as_record(self) -> dict:
        return {
          "measures": "depth_order_sensitivity",
          "NOT": "recto/verso. Reversal re-indexes the sampled stack; it does not establish a "
                 "physical face. The global normal sign is arbitrary per component.",
          "forward": self.forward,
          "reverse": self.reverse,
          "either_direction_union": self.union,
          "components_per_cm2": self.components_per_cm2,
          "kept_separate_because": "the union answers a different question from either single "
                                   "orientation and from their agreement, and a per-cm^2 "
                                   "density does not survive pooling with counts taken over a "
                                   "different area.",
          "registered_mapping": self.registered_mapping,
          "per_pixel_comparison_permitted": bool(self.registered_mapping),
        }

    def per_pixel_difference(self, fn):
        """Apply `fn(forward, reverse)` ONLY if a registered mapping was declared."""
        if not self.registered_mapping:
            raise OrientationSemanticsViolation(
                "per-pixel comparison across orientations requires a registered mapping. "
                "Without one, pixel (i, j) in the forward map and pixel (i, j) in the reverse "
                "map are not known to refer to the same physical material -- reversal, "
                "resampling, differing valid masks and per-orientation normalisation each "
                "break that correspondence, and a difference taken across a broken "
                "correspondence is a clean-looking map of nothing.")
        return fn(self.forward, self.reverse)


REQUIRED_DEPTH_ORIENTATIONS = ("forward", "reversed")

_ORIENTATION_ALIASES = {"forward": "forward", "reversed": "reversed", "reverse": "reversed",
                        "z-reversed": "reversed"}


def require_both_orientations(orientations) -> tuple:
    """BOTH DEPTH ORIENTATIONS ARE MANDATORY."""
    if isinstance(orientations, (str, bytes)) or orientations is None:
        raise OrientationSemanticsViolation(
          "orientations must be a collection naming BOTH depth orders %s; got %r. A "
          "single-orientation read is refused."
          % (REQUIRED_DEPTH_ORIENTATIONS, orientations))
    given = list(orientations)
    norm = []
    for o in given:
        n = _ORIENTATION_ALIASES.get(str(o).strip().lower())
        if n is None:
            raise OrientationSemanticsViolation(
              "unknown orientation %r; the depth orders are %s. A physical-face name is not an "
              "orientation." % (o, REQUIRED_DEPTH_ORIENTATIONS))
        norm.append(n)
    if len(set(norm)) != len(norm):
        raise OrientationSemanticsViolation("orientation named twice: %r" % given)
    missing = [o for o in REQUIRED_DEPTH_ORIENTATIONS if o not in norm]
    if missing:
        raise OrientationSemanticsViolation(
          "single-orientation read refused: %s missing from %r. Both depth orders are mandatory, "
          "kept separate and never merged." % (missing, given))
    return REQUIRED_DEPTH_ORIENTATIONS


def asymmetry_verdict(forward_stat: float, reverse_stat: float, *,
                      null_band: float) -> dict:
    """Describe a forward/reverse gap without naming a face."""
    gap = float(forward_stat) - float(reverse_stat)
    return {
      "measures": "depth_order_sensitivity",
      "forward": float(forward_stat),
      "reverse": float(reverse_stat),
      "gap": round(gap, 6),
      "null_band": float(null_band),
      "exceeds_null": bool(abs(gap) > float(null_band)),
      "interpretation": (
        "the detector's response depends on depth ORDER beyond what the null produces"
        if abs(gap) > float(null_band) else
        "no depth-order sensitivity beyond the null band"),
      "forbidden_interpretation": "that the higher-scoring orientation is the recto face. "
                                  "Nothing in this comparison establishes a physical face.",
    }
