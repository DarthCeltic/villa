"""Labelled-segment inventory: the fields that decide whether a labelled segment may be used.

Public build. The operator's curated segment inventory, its qualification status and its
detector-exposure notes are not part of the public release. The contract, the segment type
and every function other modules call are kept, so callers keep working; the inventory is
empty and every count is zero. The public label sources themselves are listed in
argus.core.label_sources.
"""
from __future__ import annotations

import dataclasses

INVENTORY_ID = "argus-label-inventory-v1"

ALIGNMENT = (
  "DIRECT_NATIVE",
  "TRANSFERRED_POOLED",
)

PROVENANCE = ("HUMAN_ANNOTATION", "MODEL_PREDICTION")

AUTHORITY_IS_OWNED_BY = "argus.core.corpus_manifest.SCROLL_LABEL_INVENTORY"

NOT_PUBLIC = "the curated label inventory is not part of the public release"

_COMPATIBLE = {
  "HUMAN_ANNOTATION": {"UNKNOWN", "DERIVED_MIXED", "MACHINE_GATED", "HUMAN_ONLY"},
  "MODEL_PREDICTION": {"UNKNOWN", "DERIVED_MIXED", "MACHINE_GATED"},
}

ADMISSION_RULE = ("a candidate scroll is NOT admitted by scroll identity alone. Its exact CT "
                  "volume and the detector's model-exposure lineage must both be proven "
                  "before it counts.")


class LabelAuthorityDisagreement(RuntimeError):
    """Raised when this inventory and the authority it defers to cannot both be true."""


class InventoryError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class LabelledSegment:
    """One labelled segment."""

    key: str
    public_segment_id: str
    scroll: str
    bucket_uri: str
    provenance: str
    alignment: str
    source_volume: str
    source_pitch_um: float
    source_energy_kev: int
    label_pitch_um: float
    slices: int
    annotated_slice: int
    has_validation_mask: bool
    licence: str
    attribution: str

    def __post_init__(self):
        if self.provenance not in PROVENANCE:
            raise InventoryError("unknown provenance %r" % (self.provenance,))
        if self.alignment not in ALIGNMENT:
            raise InventoryError("unknown alignment %r" % (self.alignment,))


SEGMENTS: tuple = ()


def authority_for(scroll: str) -> dict:
    """The PROVEN authority class for a scroll, from the module that owns that question."""
    try:
        from argus.core import corpus_manifest as _cm
        rec = _cm.SCROLL_LABEL_INVENTORY.get(scroll)
        if rec:
            return {"authority": rec.get("authority", "UNKNOWN"),
                    "byte_separable_human_subset": rec.get("byte_separable_human_subset"),
                    "source": AUTHORITY_IS_OWNED_BY}
        return {"authority": "UNKNOWN", "source": AUTHORITY_IS_OWNED_BY,
                "why": "this scroll is not in the label-authority inventory at all"}
    except Exception as exc:
        return {"authority": "UNKNOWN", "source": None,
                "why": "the authority module could not be consulted (%s)" % type(exc).__name__}


def assert_authority_agrees() -> dict:
    """HARD REFUSAL on an unreconcilable disagreement, and a report when they reconcile."""
    rows, bad = [], []
    for s in SEGMENTS:
        auth = authority_for(s.scroll)
        cls = auth["authority"]
        ok = cls in _COMPATIBLE.get(s.provenance, set())
        rows.append({"key": s.key, "scroll": s.scroll, "recorded_provenance": s.provenance,
                     "proven_authority": cls, "reconciled": ok})
        if not ok:
            bad.append("%s (%s) recorded %s, proven authority %s"
                       % (s.key, s.scroll, s.provenance, cls))
    if bad:
        raise LabelAuthorityDisagreement(
          "%d segment(s) cannot be reconciled with %s. The authority module governs and this "
          "inventory may not override it. Disagreements: %s"
          % (len(bad), AUTHORITY_IS_OWNED_BY, "; ".join(bad)))
    proven_human = sorted({r["scroll"] for r in rows if r["proven_authority"] == "HUMAN_ONLY"})
    return {
      "reconciled": True, "segments": len(rows), "rows": rows,
      "authority_owned_by": AUTHORITY_IS_OWNED_BY,
      "scrolls_proven_human_only": proven_human,
      "proven_human_only_count": len(proven_human),
      "what_this_does_not_say": (
        "that any labels were not drawn by people. Proven authority is read from provenance "
        "and stored bytes, which is a weaker and different claim."),
      "public_build": NOT_PUBLIC,
    }


def overlapping_public_segments() -> dict:
    from collections import defaultdict
    by_pub = defaultdict(list)
    for s in SEGMENTS:
        by_pub[s.public_segment_id].append(s.key)
    return {p: ks for p, ks in by_pub.items() if len(ks) > 1}


def at_eligible_acquisition() -> list:
    return []


def independent_scrolls_for(detector: str) -> dict:
    """Which scrolls could give this detector genuine cross-scroll evidence."""
    return {
      "detector": detector,
      "candidate_independent_scrolls": [],
      "excluded_scrolls": {},
      "admission_rule": ADMISSION_RULE,
      "public_build": NOT_PUBLIC,
    }


def cross_scroll_readiness(detector: str, min_scrolls: int = 3) -> dict:
    return {
      "contract": INVENTORY_ID,
      "detector": detector,
      "min_scrolls_required": min_scrolls,
      "scrolls": [], "count": 0,
      "excluded": {},
      "overlapping_material": overlapping_public_segments(),
      "admission_rule": ADMISSION_RULE,
      "public_build": NOT_PUBLIC,
    }


def as_record() -> dict:
    return {
      "contract": INVENTORY_ID,
      "segments": [],
      "label_authority_reconciliation": assert_authority_agrees(),
      "counts": {"total": 0, "direct_native": 0, "transferred_pooled": 0,
                 "with_validation_mask": 0},
      "public_build": NOT_PUBLIC,
    }
