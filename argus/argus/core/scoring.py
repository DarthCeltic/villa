"""The canonical scoring SERVICE."""
from __future__ import annotations

import dataclasses
from typing import Sequence

import numpy as np

from argus.core.metrics import (RULE_ID, MetricRefusal, auc as _auc,
                                average_precision as _ap)

SERVICE_ID = "argus-scoring-v1"

AGGREGATIONS = ("pixel", "tile", "segment", "scroll", "pooled", "macro")

BOOTSTRAP_SEED = 20260909
BOOTSTRAP_N = 1000


class ScoringRefusal(ValueError):
    """Raised instead of returning a number whose meaning is not established."""


@dataclasses.dataclass(frozen=True)
class Population:
    """One scorable population: scores, boolean labels, and an explicit validity mask."""
    scores: np.ndarray
    labels: np.ndarray
    valid: np.ndarray | None = None
    group: str = ""

    def resolved(self):
        s = np.asarray(self.scores)
        if s.dtype.kind not in "fiu":
            raise ScoringRefusal("scores must be numeric, got %r" % s.dtype)
        s = s.astype(np.float64, copy=False).ravel()

        y = np.asarray(self.labels)
        if y.dtype == bool:
            yb = y.ravel()
        elif y.dtype.kind in "iuf":
            u = np.unique(y[np.isfinite(y)] if y.dtype.kind == "f" else y)
            neg = [v for v in u if v not in (0, 1)]
            if neg:
                raise ScoringRefusal(
                    "labels carry values outside {0,1}: %s. A -1 sentinel or a multiclass "
                    "raster is not a binary label; pass an explicit `valid` mask instead of "
                    "letting a sentinel be read as a class." % list(neg)[:5])
            yb = y.astype(bool).ravel()
        else:
            raise ScoringRefusal("unsupported label dtype %r" % y.dtype)

        if s.shape != yb.shape:
            raise ScoringRefusal("scores %s and labels %s differ in shape"
                                 % (s.shape, yb.shape))
        n_total = s.size
        if self.valid is None:
            v = np.isfinite(s)
            implicit = int((~v).sum())
            if implicit:
                raise ScoringRefusal(
                    "%d non-finite score(s) and no explicit `valid` mask. Refusing to decide "
                    "on your behalf whether those are absent samples or a defect."
                    % implicit)
        else:
            v = np.asarray(self.valid).astype(bool).ravel()
            if v.shape != s.shape:
                raise ScoringRefusal("valid mask %s does not match scores %s"
                                     % (v.shape, s.shape))
            v = v & np.isfinite(s)
        if v.sum() == 0:
            raise ScoringRefusal("validity mask selects nothing: there is no population")
        return s[v], yb[v], n_total, int(v.sum())


def _ci(s: np.ndarray, y: np.ndarray) -> tuple:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    pos, neg = np.flatnonzero(y), np.flatnonzero(~y)
    if pos.size < 2 or neg.size < 2:
        return (float("nan"), float("nan"))
    vals = np.empty(BOOTSTRAP_N)
    for i in range(BOOTSTRAP_N):
        idx = np.concatenate([rng.choice(pos, pos.size, True),
                              rng.choice(neg, neg.size, True)])
        vals[i] = _auc(s[idx], y[idx])
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def _one(s: np.ndarray, y: np.ndarray, n_total: int, n_valid: int, *, ci: bool) -> dict:
    n1 = int(y.sum())
    n0 = int(y.size - n1)
    if n1 == 0 or n0 == 0:
        raise ScoringRefusal(
            "single-class population (%d positive, %d negative). AUC is undefined and 0.5 "
            "would be an invented answer." % (n1, n0))
    a = _auc(s, y)
    ap = _ap(s, y)
    prev = n1 / float(y.size)
    return {"auc": a, "ap": ap, "prevalence": prev,
            "lift": (ap / prev) if prev > 0 else float("nan"),
            "n": int(y.size), "n_positive": n1, "n_negative": n0,
            "n_total_before_mask": int(n_total),
            "coverage": float(n_valid) / float(n_total) if n_total else 0.0,
            "ci95": list(_ci(s, y)) if ci else None}


def score(populations: Sequence[Population], *, aggregation: str,
          ci: bool = True) -> dict:
    """Score one or more populations under an EXPLICIT aggregation."""
    if aggregation not in AGGREGATIONS:
        raise ScoringRefusal(
            "aggregation must be one of %s. It has no default: a score whose aggregation is "
            "unstated cannot be compared with another score." % (AGGREGATIONS,))
    if not populations:
        raise ScoringRefusal("no populations to score")

    resolved = []
    for p in populations:
        try:
            resolved.append((p.group,) + p.resolved())
        except MetricRefusal as e:
            raise ScoringRefusal("population %r: %s" % (p.group, e)) from e

    if aggregation in ("pixel", "tile", "segment", "scroll", "pooled"):
        s = np.concatenate([r[1] for r in resolved])
        y = np.concatenate([r[2] for r in resolved])
        n_total = sum(r[3] for r in resolved)
        n_valid = sum(r[4] for r in resolved)
        body = _one(s, y, n_total, n_valid, ci=ci)
        body["groups_pooled"] = len(resolved)
    else:
        per = {}
        for g, s, y, nt, nv in resolved:
            per[g or "g%d" % len(per)] = _one(s, y, nt, nv, ci=False)
        aucs = [v["auc"] for v in per.values()]
        body = {"auc": float(np.mean(aucs)),
                "auc_min": float(np.min(aucs)), "auc_max": float(np.max(aucs)),
                "ap": float(np.mean([v["ap"] for v in per.values()])),
                "prevalence": float(np.mean([v["prevalence"] for v in per.values()])),
                "lift": float(np.mean([v["lift"] for v in per.values()])),
                "n": int(sum(v["n"] for v in per.values())),
                "n_positive": int(sum(v["n_positive"] for v in per.values())),
                "n_negative": int(sum(v["n_negative"] for v in per.values())),
                "n_total_before_mask": int(sum(v["n_total_before_mask"]
                                               for v in per.values())),
                "coverage": float(np.mean([v["coverage"] for v in per.values()])),
                "ci95": None, "groups": len(per), "per_group": per,
                "macro_note": ("an unweighted mean over groups. A large group and a tiny one "
                               "count equally, which is the point of macro and the reason it "
                               "must never be compared against a pooled number.")}

    body.update({"service_id": SERVICE_ID, "metric_rule_id": RULE_ID,
                 "aggregation": aggregation,
                 "tie_rule": "averaged ranks",
                 "nan_policy": "non-finite scores are excluded ONLY via an explicit valid "
                               "mask; otherwise they are a refusal",
                 "label_policy": "boolean, or 0/1 exactly; sentinels are refused",
                 "bootstrap": {"seed": BOOTSTRAP_SEED, "n": BOOTSTRAP_N,
                               "stratified": True} if ci else None})
    return body
