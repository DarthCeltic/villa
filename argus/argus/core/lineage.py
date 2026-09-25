"""Purpose-aware lineage."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

EDGES = {
    "DERIVED_FROM":      {"carries_evidence": True,  "carries_training": False},
    "RENDERED_FROM":     {"carries_evidence": True,  "carries_training": False},
    "GEOMETRY_FROM":     {"carries_evidence": True,  "carries_training": False},
    "READ_AT_INFERENCE": {"carries_evidence": True,  "carries_training": False},
    "TRAINED_FROM":      {"carries_evidence": False, "carries_training": True},
    "SUPERVISED_BY":     {"carries_evidence": False, "carries_training": True},
    "MODEL_USED":        {"carries_evidence": True,  "carries_training": False,
                          "stops_evidence_at_node": True},
}

POLICIES = {
    "first_letters_ruling": {
        "eligible_volumes": {("PHercExample", "20000101000001")},
        "training_may_use": {("PHercExample", "20000101000002")},
        "source": "public organizer ruling, ScrollPrize/villa issue 1739",
        "requires_disjointness_proof": True,
    },
    "grand_prize": {
        "eligible_volumes": {("PHercExample", "20000101000001")},
        "training_may_use": set(),
        "source": "no ruling was given for the Grand Prize; the First Letters answer is not "
                  "generalised to it",
        "requires_disjointness_proof": True,
    },
}


class LineageRefusal(RuntimeError):
    """Raised when lineage cannot be evaluated, as distinct from evaluating to NOT eligible."""


@dataclass
class Node:
    """A node IS its artifact."""
    kind: str
    ident: str
    edges: list = field(default_factory=list)
    volume: tuple | None = None
    region: tuple | None = None


def _walk_evidence(root: Node):
    """Volumes in the SUBMITTED closure."""
    seen, vols, stopped = set(), set(), []
    stack = [(root, False)]
    while stack:
        node, blocked = stack.pop()
        key = (id(node), blocked)
        if key in seen:
            continue
        seen.add(key)
        if node.volume and not blocked:
            vols.add(node.volume)
        for etype, parent in node.edges:
            if etype not in EDGES:
                raise LineageRefusal(
                    "unknown edge type %r between %s/%s and %s/%s. A lineage model that "
                    "ignores an edge it cannot parse approves whatever it failed to read."
                    % (etype, node.kind, node.ident, parent.kind, parent.ident))
            spec = EDGES[etype]
            if not spec.get("carries_evidence"):
                continue
            if spec.get("stops_evidence_at_node"):
                stopped.append("%s/%s" % (parent.kind, parent.ident))
                stack.append((parent, True))
            else:
                stack.append((parent, blocked))
    return vols, stopped


def _walk_training(root: Node):
    """Volumes that TAUGHT the model."""
    seen, vols = set(), set()
    stack = [(root, False)]
    while stack:
        node, in_training = stack.pop()
        key = (id(node), in_training)
        if key in seen:
            continue
        seen.add(key)
        if node.volume and in_training:
            vols.add(node.volume)
        for etype, parent in node.edges:
            if etype not in EDGES:
                raise LineageRefusal(
                    "unknown edge type %r between %s/%s and %s/%s"
                    % (etype, node.kind, node.ident, parent.kind, parent.ident))
            stack.append((parent, in_training or EDGES[etype].get("carries_training", False)))
    return vols


def evidence_volumes(root: Node):
    return _walk_evidence(root)


def training_volumes(root: Node):
    return _walk_training(root)


def regions_disjoint(train_regions, submit_regions, buffer_px: float) -> dict:
    """Physical disjointness with the buffer applied."""
    if train_regions is None or submit_regions is None:
        return {"proven": False, "why": "no region geometry supplied; disjointness is UNPROVEN "
                                        "and an unproven separation is not a separation"}
    if buffer_px is None:
        return {"proven": False, "why": "no exclusion buffer declared"}
    overlaps = []
    for t in train_regions:
        for s in submit_regions:
            ty0, ty1, tx0, tx1 = t
            sy0, sy1, sx0, sx1 = s
            if not (ty1 + buffer_px <= sy0 or sy1 + buffer_px <= ty0
                    or tx1 + buffer_px <= sx0 or sx1 + buffer_px <= tx0):
                overlaps.append({"train": list(t), "submit": list(s)})
    return {"proven": not overlaps, "overlaps": overlaps, "buffer_px": buffer_px,
            "why": ("no train region comes within the buffer of any submitted region"
                    if not overlaps else
                    "%d train/submit pair(s) are within the exclusion buffer" % len(overlaps))}


def exclusion_buffer(*, receptive_field_radius_px: float, resampling_support_px: float,
                     max_augmentation_displacement_px: float,
                     registration_uncertainty_px: float) -> dict:
    """Every term declared."""
    terms = {"receptive_field_radius_px": receptive_field_radius_px,
             "resampling_support_px": resampling_support_px,
             "max_augmentation_displacement_px": max_augmentation_displacement_px,
             "registration_uncertainty_px": registration_uncertainty_px}
    missing = [k for k, v in terms.items() if v is None]
    if missing:
        raise LineageRefusal("exclusion buffer is missing declared terms: %s" % missing)
    return {"terms": terms, "total_px": float(sum(terms.values())),
            "why_each": {
                "receptive_field_radius_px": "the model sees this far beyond the pixel it predicts",
                "resampling_support_px": "interpolation pulls values from beyond the sample point",
                "max_augmentation_displacement_px": "augmentation can move a training patch this far",
                "registration_uncertainty_px": "the cross-resolution transform is not exact, and "
                                               "the bound is predeclared rather than measured "
                                               "after the fact"}}


def evaluate(submission: Node, *, policy: str, train_regions=None, submit_regions=None,
             buffer_px: float | None = None) -> dict:
    """Is this submission permitted?"""
    if policy not in POLICIES:
        raise LineageRefusal("unknown policy %r" % policy)
    pol = POLICIES[policy]

    ev, stopped = evidence_volumes(submission)
    tr = training_volumes(submission)

    ev_bad = sorted(v for v in ev if v not in pol["eligible_volumes"])
    tr_bad = sorted(v for v in tr
                    if v not in pol["eligible_volumes"] and v not in pol["training_may_use"])

    dis = regions_disjoint(train_regions, submit_regions, buffer_px)
    needs_proof = bool(pol["requires_disjointness_proof"] and tr)

    reasons = []
    if ev_bad:
        reasons.append("submitted evidence derives from ineligible volume(s): %s" % ev_bad)
    if tr_bad:
        reasons.append("training used volume(s) this policy does not permit: %s" % tr_bad)
    if needs_proof and not dis["proven"]:
        reasons.append("disjointness unproven: %s" % dis["why"])

    verdict = ("ALLOW_FIRST_LETTERS" if policy == "first_letters_ruling" and not reasons
               else "ALLOW" if not reasons
               else "REFUSE" if (needs_proof and not dis["proven"] and not ev_bad and not tr_bad)
               else "REJECT")

    return {
        "schema": "argus-lineage-verdict-v1",
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": policy, "policy_source": pol["source"],
        "verdict": verdict, "allowed": verdict.startswith("ALLOW"),
        "evidence_volumes": sorted(ev), "training_volumes": sorted(tr),
        "evidence_traversal_stopped_at": stopped,
        "ineligible_in_evidence": ev_bad, "impermissible_in_training": tr_bad,
        "disjointness": dis,
        "reasons": reasons or ["evidence derives only from eligible volumes; training inputs are "
                               "permitted by this policy; regions are provably disjoint"],
        "model_used_semantics": ("MODEL_USED reaches the checkpoint, because the checkpoint is "
                                 "part of the submission, and stops there: what TAUGHT the "
                                 "checkpoint is audited separately and is not evidence."),
        "display_banner": (None if verdict.startswith("ALLOW") else "NOT SUBMISSION ELIGIBLE"),
    }
