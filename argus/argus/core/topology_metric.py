"""Topology-aware surface metrics: connected components, mergers, splits."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import ndimage

RULE_ID = "argus-topology-metric-v1"

RULE = {
    "rule_id": RULE_ID,
    "components": "scipy.ndimage.label over a boolean mask",
    "connectivity_full (default True)": "every neighbour sharing a face, edge OR corner "
                                        "(structure = np.ones((3,) * ndim)); False uses "
                                        "scipy's face-only 'cross' default",
    "merger": "a predicted component whose voxels overlap TWO OR MORE distinct true "
             "components, at or above min_overlap_voxels for each",
    "split": "a true component whose voxels overlap TWO OR MORE distinct predicted "
             "components, at or above min_overlap_voxels for each",
    "overlap": "counted only where predicted AND true are both foreground at the same voxel; "
              "min_overlap_voxels filters noise-level touches from counting as a real overlap",
    "masks": "coerced to bool ONLY from an existing bool array or an all-{0,1} integer/float "
            "array; anything else is refused rather than guessed",
}


class TopologyMetricRefusal(ValueError):
    """Raised instead of returning a topology summary computed from ambiguous input."""


def _to_bool_mask(a, name: str) -> np.ndarray:
    """Boolean mask, or a refusal."""
    arr = np.asarray(a)
    if arr.dtype == bool:
        return arr
    if arr.dtype.kind == "f":
        if not np.isfinite(arr).all():
            raise TopologyMetricRefusal("%s contains NaN or inf" % name)
        uniq = np.unique(arr)
        if not np.isin(uniq, (0.0, 1.0)).all():
            raise TopologyMetricRefusal(
                "%s is float but not all 0.0/1.0: %s" % (name, uniq[:6]))
        return arr.astype(bool)
    if arr.dtype.kind in "iu":
        uniq = np.unique(arr)
        if not np.isin(uniq, (0, 1)).all():
            raise TopologyMetricRefusal(
                "%s is integer but not all 0/1: %s. Refusing to guess a threshold."
                % (name, uniq[:6]))
        return arr.astype(bool)
    raise TopologyMetricRefusal("%s has unsupported dtype %r" % (name, arr.dtype))


def _structure(ndim: int, connectivity_full: bool) -> np.ndarray | None:
    if connectivity_full:
        return np.ones((3,) * ndim, dtype=int)
    return None


def label_components(mask, *, connectivity_full: bool = True) -> tuple[np.ndarray, int]:
    """Labeled component array and count for one boolean mask, under a DECLARED connectivity."""
    m = _to_bool_mask(mask, "mask")
    if m.size == 0:
        raise TopologyMetricRefusal("mask is empty")
    structure = _structure(m.ndim, connectivity_full)
    labeled, count = ndimage.label(m, structure=structure)
    return labeled, int(count)


def evaluate(predicted, true, *, connectivity_full: bool = True,
             min_overlap_voxels: int = 1) -> dict[str, Any]:
    """Component counts plus merger/split detection between a predicted and a true mask."""
    if min_overlap_voxels < 1:
        raise TopologyMetricRefusal("min_overlap_voxels must be >= 1")
    p = _to_bool_mask(predicted, "predicted")
    t = _to_bool_mask(true, "true")
    if p.shape != t.shape:
        raise TopologyMetricRefusal(
            "predicted %s and true %s have different shapes" % (p.shape, t.shape))
    if p.size == 0:
        raise TopologyMetricRefusal("empty input")

    structure = _structure(p.ndim, connectivity_full)
    pred_labels, n_pred = ndimage.label(p, structure=structure)
    true_labels, n_true = ndimage.label(t, structure=structure)

    overlap_mask = p & t
    pred_to_true: dict[int, dict[int, int]] = {}
    true_to_pred: dict[int, dict[int, int]] = {}
    if overlap_mask.any():
        pv = pred_labels[overlap_mask]
        tv = true_labels[overlap_mask]
        pairs, counts = np.unique(np.stack([pv, tv], axis=1), axis=0, return_counts=True)
        for (pl, tl), c in zip(pairs.tolist(), counts.tolist()):
            if pl == 0 or tl == 0:
                continue
            pred_to_true.setdefault(pl, {})[tl] = c
            true_to_pred.setdefault(tl, {})[pl] = c

    def _qualified(neighbours: dict[int, int]) -> list[int]:
        return sorted(k for k, c in neighbours.items() if c >= min_overlap_voxels)

    mergers = {pl: _qualified(tl_counts) for pl, tl_counts in pred_to_true.items()}
    mergers = {pl: tls for pl, tls in mergers.items() if len(tls) > 1}
    splits = {tl: _qualified(pl_counts) for tl, pl_counts in true_to_pred.items()}
    splits = {tl: pls for tl, pls in splits.items() if len(pls) > 1}

    matched_pred = {pl for pl, tl_counts in pred_to_true.items() if _qualified(tl_counts)}
    matched_true = {tl for tl, pl_counts in true_to_pred.items() if _qualified(pl_counts)}
    unmatched_predicted = sorted(set(range(1, n_pred + 1)) - matched_pred)
    unmatched_true = sorted(set(range(1, n_true + 1)) - matched_true)

    return {
        "schema": "argus-topology-metric-v1",
        "rule_id": RULE_ID,
        "connectivity_full": bool(connectivity_full),
        "min_overlap_voxels": int(min_overlap_voxels),
        "n_components_predicted": n_pred,
        "n_components_true": n_true,
        "component_count_delta": n_pred - n_true,
        "component_count_matches": n_pred == n_true,
        "mergers": {"count": len(mergers),
                    "predicted_components": sorted(mergers),
                    "detail": {pl: tls for pl, tls in sorted(mergers.items())}},
        "splits": {"count": len(splits),
                   "true_components": sorted(splits),
                   "detail": {tl: pls for tl, pls in sorted(splits.items())}},
        "unmatched_predicted_components": unmatched_predicted,
        "unmatched_true_components": unmatched_true,
        "topology_clean": (n_pred == n_true and not mergers and not splits
                           and not unmatched_predicted and not unmatched_true),
        "not_voxel_accuracy": "This measures topology only (component count, mergers, "
                              "splits) -- it says nothing about per-voxel Dice/IoU accuracy, "
                              "which is a separate question and must be reported alongside "
                              "this, never in place of it.",
    }
