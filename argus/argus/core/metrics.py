"""The canonical ARGUS metric."""
from __future__ import annotations

import numpy as np

RULE_ID = "argus-metric-v1"

RULE = {
    "rule_id": RULE_ID,
    "auc": "Mann-Whitney U with AVERAGED ranks for ties, computed in float64",
    "ap": "average precision as the step-wise sum over recall increments, ties grouped",
    "labels": "coerced to bool ONLY from {0,1} or an existing bool array; anything else is "
              "refused rather than guessed",
    "single_class": "REFUSED -- AUC is undefined with no positives or no negatives, and "
                    "returning 0.5 there would be an invented answer",
    "nan": "REFUSED in scores and in labels",
}


class MetricRefusal(ValueError):
    """Raised instead of returning a number that would be meaningless."""


def _labels_to_bool(y) -> np.ndarray:
    """Boolean labels, or a refusal."""
    a = np.asarray(y)
    if a.dtype == bool:
        return a
    if a.dtype.kind == "f":
        if not np.isfinite(a).all():
            raise MetricRefusal("labels contain NaN or inf")
        uniq = np.unique(a)
        if not np.isin(uniq, (0.0, 1.0)).all():
            raise MetricRefusal(
                "float labels are not all 0.0/1.0: %s" % uniq[:6])
        return a.astype(bool)
    if a.dtype.kind in "iu":
        uniq = np.unique(a)
        if not np.isin(uniq, (0, 1)).all():
            raise MetricRefusal(
                "integer labels are not all 0/1: %s. Refusing to guess a threshold."
                % uniq[:6])
        return a.astype(bool)
    raise MetricRefusal("unsupported label dtype %r" % a.dtype)


def _scores(s) -> np.ndarray:
    a = np.asarray(s, dtype=np.float64)
    if not np.isfinite(a).all():
        raise MetricRefusal("scores contain NaN or inf")
    return a


def _check_pair(s: np.ndarray, y: np.ndarray):
    if s.shape != y.shape:
        raise MetricRefusal("scores %s and labels %s have different shapes"
                            % (s.shape, y.shape))
    if s.size == 0:
        raise MetricRefusal("empty input")
    n1 = int(y.sum())
    n0 = int(y.size - n1)
    if n1 == 0 or n0 == 0:
        raise MetricRefusal(
            "single-class input (%d positive, %d negative): AUC is undefined. Returning 0.5 "
            "here would invent an answer." % (n1, n0))
    return n1, n0


def auc(scores, labels) -> float:
    """Mann-Whitney U AUC with averaged ranks."""
    s = _scores(scores).ravel()
    y = _labels_to_bool(labels).ravel()
    n1, n0 = _check_pair(s, y)
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(s.size, dtype=np.float64)
    ranks[order] = np.arange(1, s.size + 1, dtype=np.float64)
    ss = s[order]
    i = 0
    while i < ss.size:
        j = i + 1
        while j < ss.size and ss[j] == ss[i]:
            j += 1
        if j - i > 1:
            ranks[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def average_precision(scores, labels) -> float:
    """Average precision, ties grouped so equal scores cannot be ordered by luck."""
    s = _scores(scores).ravel()
    y = _labels_to_bool(labels).ravel()
    n1, _n0 = _check_pair(s, y)
    order = np.argsort(-s, kind="mergesort")
    ys = y[order]
    ss = s[order]
    tp = 0
    ap = 0.0
    i = 0
    while i < ss.size:
        j = i + 1
        while j < ss.size and ss[j] == ss[i]:
            j += 1
        group_pos = int(ys[i:j].sum())
        if group_pos:
            tp += group_pos
            ap += (tp / float(j)) * (group_pos / float(n1))
        i = j
    return float(ap)


def score(scores, labels) -> dict:
    """Both metrics plus the rule id, so a receipt can never record a value without its rule."""
    return {"rule_id": RULE_ID,
            "auc": auc(scores, labels),
            "ap": average_precision(scores, labels),
            "n": int(np.asarray(scores).size),
            "n_positive": int(_labels_to_bool(labels).sum())}
