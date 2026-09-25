"""What a result IS, carried with it, so it cannot be shown as something it is not."""
from __future__ import annotations

import dataclasses

RESULT_CLASS_ID = "argus-result-class-v1"

TARGET_CLASSES = (
  "LABELLED_FRAGMENT",
  "LABELLED_SCROLL",
  "UNREAD_SCROLL",
  "SYNTHETIC",
)

EXPOSURE_BASIS = (
  "HELD_OUT_BY_FOLD",
  "TRAINED_ON",
  "UNSEEN_PROVEN",
  "EXPOSURE_UNKNOWN",
)

PRESENTATION = (
  "KNOWN_DOMAIN_HELD_OUT_CONTROL",
  "CALIBRATION_ONLY",
  "EXPLORATORY_CANDIDATE",
  "QUALIFIED_EVIDENCE",
  "DEMONSTRATION_ONLY",
)

BANNERS = {
  "KNOWN_DOMAIN_HELD_OUT_CONTROL":
      "KNOWN-DOMAIN HELD-OUT CONTROL — a labelled fragment or labelled-scroll region read out-of-sample. This proves the "
      "pipeline, not a discovery.",
  "CALIBRATION_ONLY":
      "CALIBRATION ONLY — the detector was trained on this target. The number is a check on "
      "the plumbing and nothing else.",
  "EXPLORATORY_CANDIDATE":
      "EXPLORATORY — no detector has passed cross-scroll qualification, so this is a lead to "
      "examine, not a reading.",
  "QUALIFIED_EVIDENCE":
      "QUALIFIED EVIDENCE — read by a detector that passed the cross-scroll gate.",
  "DEMONSTRATION_ONLY":
      "DEMONSTRATION ONLY — synthetic or fixture data. Nothing here is about a real scroll.",
}


class ResultClassError(ValueError):
    """Raised when a result would be presentable without saying what it is."""


@dataclasses.dataclass(frozen=True)
class ResultClass:
    """The identity of one result."""

    target: str
    target_class: str
    exposure_basis: str
    detector: str
    detector_cross_scroll_qualified: bool
    acquisition: str
    metric: str | None = None
    score: float | None = None

    def __post_init__(self):
        if self.target_class not in TARGET_CLASSES:
            raise ResultClassError("unknown target class %r" % (self.target_class,))
        if self.exposure_basis not in EXPOSURE_BASIS:
            raise ResultClassError("unknown exposure basis %r" % (self.exposure_basis,))
        if not self.target or not self.detector:
            raise ResultClassError("a result must name its target and its detector")


    @property
    def presentation(self) -> str:
        """Derived, never set."""
        if self.target_class == "SYNTHETIC":
            return "DEMONSTRATION_ONLY"
        if self.exposure_basis == "TRAINED_ON":
            return "CALIBRATION_ONLY"
        if self.target_class in ("LABELLED_FRAGMENT", "LABELLED_SCROLL") \
                and self.exposure_basis == "HELD_OUT_BY_FOLD":
            return "KNOWN_DOMAIN_HELD_OUT_CONTROL"
        if (self.target_class == "UNREAD_SCROLL"
                and self.exposure_basis == "UNSEEN_PROVEN"
                and self.detector_cross_scroll_qualified):
            return "QUALIFIED_EVIDENCE"
        return "EXPLORATORY_CANDIDATE"

    @property
    def may_claim_discovery(self) -> bool:
        """Three conditions, all required."""
        return (self.target_class == "UNREAD_SCROLL"
                and self.exposure_basis == "UNSEEN_PROVEN"
                and self.detector_cross_scroll_qualified)

    @property
    def may_claim_ink_found(self) -> bool:
        """'Ink found' is a discovery claim."""
        return self.may_claim_discovery

    def display_banner(self) -> str:
        b = BANNERS[self.presentation]
        if not b:
            raise ResultClassError("every presentation must have a banner")
        return b

    def assert_not_discovery(self, phrasing: str) -> None:
        """Raise if a caller is about to describe this result as a discovery."""
        low = phrasing.lower()
        claims = ("ink found", "found ink", "discovered", "discovery", "we read",
                  "first letters", "unread scroll", "revealed text")
        hit = [c for c in claims if c in low]
        if hit and not self.may_claim_discovery:
            raise ResultClassError(
                "%r claims %s, but this result is %s: target_class=%s, exposure=%s, "
                "detector_cross_scroll_qualified=%s"
                % (phrasing[:60], hit, self.presentation, self.target_class,
                   self.exposure_basis, self.detector_cross_scroll_qualified))

    def as_dict(self) -> dict:
        return {
          "contract": RESULT_CLASS_ID,
          "target": self.target, "target_class": self.target_class,
          "exposure_basis": self.exposure_basis,
          "detector": self.detector,
          "detector_cross_scroll_qualified": self.detector_cross_scroll_qualified,
          "acquisition": self.acquisition,
          "metric": self.metric, "score": self.score,
          "presentation": self.presentation,
          "banner": self.display_banner(),
          "may_claim_discovery": self.may_claim_discovery,
          "may_claim_ink_found": self.may_claim_ink_found,
          "not_established": self.not_established(),
        }

    def not_established(self) -> list:
        out = []
        if self.target_class == "LABELLED_SCROLL":
            out.append("nothing about an unread scroll: this region has published labels")
        if self.target_class == "LABELLED_FRAGMENT":
            out.append("nothing about an unread scroll: this fragment has published labels")
            out.append("nothing about the 9.362um or 8.640um eligible acquisitions")
        if not self.detector_cross_scroll_qualified:
            out.append("cross-scroll generalization: this detector has not passed that gate")
        if self.exposure_basis == "HELD_OUT_BY_FOLD":
            out.append("held out of a FOLD is not held out of the DOMAIN")
        if self.exposure_basis == "EXPOSURE_UNKNOWN":
            out.append("the model's ancestry is incomplete, so exposure is unknown, not clean")
        return out



def example_fragment_control(*, auc: float, detector_sha256: str) -> ResultClass:
    """A synthetic known-domain held-out control result, for tests and documentation."""
    return ResultClass(
      target="FragmentFixture1", target_class="LABELLED_FRAGMENT",
      exposure_basis="HELD_OUT_BY_FOLD",
      detector="example-detector %s" % detector_sha256[:12],
      detector_cross_scroll_qualified=False,
      acquisition="fixture acquisition",
      metric="AUC (argus-metric-v1)", score=auc)



FIRST_LETTERS_BRIDGE = (
  {"gate": "SAMPLER_ADMITTED",
   "what": "an admitted, qualified CT-to-surface sampler",
   "needs": "a pinned, hashed build that passes a declared control",
   "currently": "not recorded in the public release",
   "blocks": "any sampled stack from an eligible scroll"},
  {"gate": "MODEL_EXPOSURE_FROZEN",
   "what": "a frozen exposure manifest for the detector being used",
   "needs": "the detector's complete training exposure, hashed and recorded",
   "currently": "not recorded in the public release",
   "blocks": "knowing what the detector has seen"},
  {"gate": "CROSS_SCROLL_CONTROL_AT_ELIGIBLE_ACQUISITION",
   "what": "a clean cross-scroll control at the eligible acquisition class",
   "needs": "leave-one-scroll-out evaluation over independently labelled material",
   "currently": "UNMET in the public release; no control result ships with it",
   "blocks": "any claim that a detector generalizes across scrolls"},
  {"gate": "ELIGIBLE_TARGET_REGION_ADMITTED",
   "what": "admit one bounded region of one eligible target",
   "needs": "all three gates above, plus a pre-registered decision framework",
   "currently": "not reachable",
   "blocks": "First Letters"},
)


def bridge_status() -> dict:
    return {"contract": RESULT_CLASS_ID,
            "gates": list(FIRST_LETTERS_BRIDGE),
            "gates_passed": 0,
            "unread_scroll_search": "NOT OPEN -- and may not open before all four gates pass",
            "ink_found_claim": "FORBIDDEN for any PHerc output until all four gates pass"}
