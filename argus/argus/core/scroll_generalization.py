"""LEAVE_ONE_SCROLL_OUT_WEIGHT_GATE: do these WEIGHTS read a scroll they never saw?"""
from __future__ import annotations

import dataclasses
import re
from typing import Sequence

import numpy as np

from argus.core.adaptation import QUALIFY_AUC, QUALIFY_CI_LOWER
from argus.core.metrics import RULE_ID, MetricRefusal, auc

BOOTSTRAP_SEED = 20260909
BOOTSTRAP_N = 2000

MIN_HELDOUT_SCROLLS = 3


class GateRefusal(ValueError):
    """Raised when there is no legitimate verdict to give."""


SCROLL_ID = re.compile(r"^PHerc(?:Paris\d{1,2}|\d{3,4}[A-Z]?\d?)$")

_WINDOW_MARKERS = ("_w", "-w", "_seg", "-seg", "_win", "-win", "_patch", "_fold", "_crop")


def canonical_scroll(name: str) -> str:
    """Return the scroll id, or refuse."""
    if not isinstance(name, str) or not name.strip():
        raise GateRefusal("scroll id must be a non-empty string")
    n = name.strip()
    low = n.lower()
    for m in _WINDOW_MARKERS:
        if m in low:
            raise GateRefusal(
                "%r names a window or segment, not a scroll. Windows of one scroll share its "
                "texture, acquisition and artefacts; counting them as independent holdouts "
                "measures memorisation rather than transfer." % n)
    if not SCROLL_ID.match(n):
        raise GateRefusal(
            "%r is not a canonical scroll id (expected e.g. PHerc0841, PHerc0009B, "
            "PHerc0814, PHercParis4). An unrecognised id cannot be shown to be a distinct "
            "scroll, so it is refused rather than counted." % n)
    return n



@dataclasses.dataclass(frozen=True)
class WeightIdentity:
    """What the weights are, and -- load-bearing -- what they were trained on."""
    weight_id: str
    sha256: str
    trained_on_scrolls: tuple

    def __post_init__(self):
        if not self.weight_id or not self.sha256:
            raise GateRefusal("weights must carry an id and a sha256; an unidentified "
                              "checkpoint cannot be gated or cited")
        if not isinstance(self.trained_on_scrolls, tuple):
            raise GateRefusal("trained_on_scrolls must be a tuple; it is a claim about "
                              "exposure and must not be mutable after declaration")
        for _s in self.trained_on_scrolls:
            canonical_scroll(_s)
        if len(self.trained_on_scrolls) == 0:
            raise GateRefusal(
                "trained_on_scrolls is empty. An undeclared training manifest is not the "
                "same as a clean one. Declare the scrolls or the weights cannot be gated.")


@dataclasses.dataclass(frozen=True)
class HeldOutEvaluation:
    """One held-out scroll: the weights, the scroll, and the predictions made on it."""
    scroll: str
    weights: WeightIdentity
    scores: np.ndarray
    labels: np.ndarray

    def __post_init__(self):
        canonical_scroll(self.scroll)
        if self.scroll in self.weights.trained_on_scrolls:
            raise GateRefusal(
                "scroll %r appears in the training manifest of weights %r. This is not a "
                "holdout and its score is not evidence." % (self.scroll,
                                                            self.weights.weight_id))


def _bootstrap_ci(scores: np.ndarray, labels: np.ndarray) -> tuple:
    """Percentile bootstrap CI on the canonical AUC, with a frozen seed."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    y = np.asarray(labels).astype(bool)
    pos = np.flatnonzero(y)
    neg = np.flatnonzero(~y)
    if pos.size < 2 or neg.size < 2:
        raise GateRefusal("too few of one class to bootstrap a CI (%d positive, %d negative)"
                          % (pos.size, neg.size))
    vals = np.empty(BOOTSTRAP_N, dtype=np.float64)
    for i in range(BOOTSTRAP_N):
        idx = np.concatenate([rng.choice(pos, pos.size, replace=True),
                              rng.choice(neg, neg.size, replace=True)])
        vals[i] = auc(scores[idx], y[idx])
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def evaluate(evaluations: Sequence[HeldOutEvaluation], *,
             min_scrolls: int = MIN_HELDOUT_SCROLLS) -> dict:
    """Gate WEIGHTS on the worst held-out scroll."""
    if not evaluations:
        raise GateRefusal("no held-out evaluations: a gate with nothing to gate is not a pass")

    scrolls = [e.scroll for e in evaluations]
    if len(set(scrolls)) != len(scrolls):
        raise GateRefusal("a scroll appears twice: %s" % sorted(scrolls))
    if len(scrolls) < min_scrolls:
        raise GateRefusal(
            "only %d held-out scroll(s); %d are required. Transfer shown on one or two "
            "scrolls is an anecdote, not generalization." % (len(scrolls), min_scrolls))

    per_scroll = {}
    for e in evaluations:
        try:
            a = auc(e.scores, e.labels)
            lo, hi = _bootstrap_ci(np.asarray(e.scores, dtype=np.float64), e.labels)
        except MetricRefusal as exc:
            raise GateRefusal("scroll %s cannot be scored: %s" % (e.scroll, exc)) from exc
        per_scroll[e.scroll] = {
            "auc": a, "ci95": (lo, hi),
            "n": int(np.asarray(e.scores).size),
            "n_positive": int(np.asarray(e.labels).astype(bool).sum()),
            "weight_id": e.weights.weight_id,
            "weight_sha256": e.weights.sha256,
            "trained_on": list(e.weights.trained_on_scrolls),
            "meets_bar": bool(a >= QUALIFY_AUC and lo >= QUALIFY_CI_LOWER)}

    worst_name = min(per_scroll, key=lambda s: per_scroll[s]["auc"])
    worst = per_scroll[worst_name]
    qualified = all(v["meets_bar"] for v in per_scroll.values())
    failing = sorted(s for s, v in per_scroll.items() if not v["meets_bar"])

    return {
        "gate": "LEAVE_ONE_SCROLL_OUT_WEIGHT_GATE",
        "metric_rule_id": RULE_ID,
        "verdict": "WEIGHTS_QUALIFIED" if qualified else "WEIGHTS_REJECTED",
        "held_out_scrolls": sorted(per_scroll),
        "n_held_out_scrolls": len(per_scroll),
        "worst_scroll": worst_name,
        "worst_auc": worst["auc"],
        "worst_ci95": worst["ci95"],
        "failing_scrolls": failing,
        "qualify_auc": QUALIFY_AUC,
        "qualify_ci_lower": QUALIFY_CI_LOWER,
        "per_scroll": per_scroll,
        "bootstrap": {"seed": BOOTSTRAP_SEED, "n": BOOTSTRAP_N,
                      "stratified": True, "interval": "percentile 2.5/97.5"},
        "basis": ("every score is from weights that never saw that scroll -- exposure is "
                  "checked at construction, before any number is read. The gate is the "
                  "WORST held-out scroll, never a mean and never the best transfer."),
        "consequence": ("WEIGHTS_REJECTED means these weights may not be used for target "
                        "inference. A high score on a scroll they trained on does not "
                        "overturn it." if not qualified else
                        "weights transferred to every held-out scroll at or above the bar"),
    }
