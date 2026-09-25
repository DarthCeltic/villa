"""Genuine propagation-error detection for classical VC3D Copy Out/In."""
from __future__ import annotations

import numpy as np

CONTRACT = "argus-copy-out-in-defect-control-v1"


def tifxyz_validity_mask(x, y, z) -> np.ndarray:
    """A grid cell is valid if it is finite and not VC3D's -1/-1/-1 \"unmapped\" sentinel triple."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    sentinel = (x == -1.0) & (y == -1.0) & (z == -1.0)
    return finite & ~sentinel


def local_defect_score(x, y, z, *, mask=None) -> np.ndarray:
    """Discrete-Laplacian magnitude of the position field at each grid cell."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    if x.shape != y.shape or x.shape != z.shape:
        raise ValueError("x, y, z must share one shape")
    if x.ndim != 2 or min(x.shape) < 3:
        raise ValueError("a defect score needs a 2-D grid at least 3 cells wide in each axis")
    if mask is None:
        mask = tifxyz_validity_mask(x, y, z)
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != x.shape:
        raise ValueError("mask must share x/y/z's shape")

    P = np.stack([x, y, z], axis=-1)
    up = np.zeros_like(P)
    down = np.zeros_like(P)
    left = np.zeros_like(P)
    right = np.zeros_like(P)
    up[1:, :, :] = P[:-1, :, :]
    down[:-1, :, :] = P[1:, :, :]
    left[:, 1:, :] = P[:, :-1, :]
    right[:, :-1, :] = P[:, 1:, :]
    lap = P - 0.25 * (up + down + left + right)
    magnitude = np.linalg.norm(lap, axis=-1)

    neighbours_valid = np.zeros(x.shape, dtype=bool)
    neighbours_valid[1:-1, 1:-1] = (
        mask[1:-1, 1:-1] & mask[:-2, 1:-1] & mask[2:, 1:-1]
        & mask[1:-1, :-2] & mask[1:-1, 2:]
    )
    score = np.full(x.shape, np.nan, dtype=np.float64)
    score[neighbours_valid] = magnitude[neighbours_valid]
    return score


def defect_mask_from_score(score, *, robust_z_threshold: float = 6.0,
                            min_absolute: float | None = None) -> np.ndarray:
    """Robust outlier flag over a defect score: ``score > median + robust_z_threshold * MAD_sigma``."""
    score = np.asarray(score, dtype=np.float64)
    finite = np.isfinite(score)
    flags = np.zeros(score.shape, dtype=bool)
    if not finite.any():
        return flags
    values = score[finite]
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    sigma = mad * 1.4826 if mad > 0 else (float(np.std(values)) or 1e-9)
    threshold = median + float(robust_z_threshold) * sigma
    if min_absolute is not None:
        threshold = max(threshold, float(min_absolute))
    flags[finite] = score[finite] > threshold
    return flags


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grow a boolean mask by `radius` cells (4-connected), with no external dependency."""
    if radius <= 0:
        return mask.copy()
    out = mask.copy()
    for _ in range(int(radius)):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def detect_propagated_defects(source_xyz, candidate_xyz, *, robust_z_threshold: float = 6.0,
                               correspondence_radius: int = 1) -> dict:
    """Compare independently-scored defect maps for a source wrap and its Copy Out/In candidate."""
    sx, sy, sz = (np.asarray(a, dtype=np.float64) for a in source_xyz)
    cx, cy, cz = (np.asarray(a, dtype=np.float64) for a in candidate_xyz)
    if sx.shape != sy.shape or sx.shape != sz.shape:
        raise ValueError("source x, y, z must share one shape")
    if cx.shape != cy.shape or cx.shape != cz.shape:
        raise ValueError("candidate x, y, z must share one shape")

    s_score = local_defect_score(sx, sy, sz)
    c_score = local_defect_score(cx, cy, cz)
    s_mask = defect_mask_from_score(s_score, robust_z_threshold=robust_z_threshold)
    c_mask = defect_mask_from_score(c_score, robust_z_threshold=robust_z_threshold)

    same_shape = sx.shape == cx.shape
    h = min(sx.shape[0], cx.shape[0])
    w = min(sx.shape[1], cx.shape[1])
    s_region = s_mask[:h, :w]
    c_region = c_mask[:h, :w]
    s_dilated = _dilate(s_region, correspondence_radius)

    propagated = c_region & s_dilated
    candidate_only = c_region & ~s_dilated

    def _coords(mask_arr, limit=200):
        rows, cols = np.nonzero(mask_arr)
        return [[int(r), int(c)] for r, c in zip(rows[:limit], cols[:limit])]

    n_candidate = int(c_region.sum())
    n_propagated = int(propagated.sum())
    if n_candidate == 0:
        verdict = "NO_CANDIDATE_DEFECTS"
    elif n_propagated > 0:
        verdict = "PROPAGATION_DETECTED"
    else:
        verdict = "CANDIDATE_DEFECTS_NOT_SOURCE_LOCAL"

    return {
        "schema": "argus-copy-out-in-propagation-report-v1",
        "contract": CONTRACT,
        "correspondence_method": "grid_index" if same_shape else "grid_index_overlap_region",
        "correspondence_radius_cells": int(correspondence_radius),
        "same_shape": bool(same_shape),
        "compared_region_shape": [int(h), int(w)],
        "source_shape": [int(sx.shape[0]), int(sx.shape[1])],
        "candidate_shape": [int(cx.shape[0]), int(cx.shape[1])],
        "robust_z_threshold": float(robust_z_threshold),
        "source_defect_count": int(s_region.sum()),
        "candidate_defect_count": n_candidate,
        "propagated_defect_count": n_propagated,
        "candidate_only_defect_count": int(candidate_only.sum()),
        "propagated_fraction_of_candidate_defects": (
            (n_propagated / n_candidate) if n_candidate else 0.0
        ),
        "propagated_sample_grid_coords": _coords(propagated),
        "candidate_only_sample_grid_coords": _coords(candidate_only),
        "verdict": verdict,
        "scientific_boundary": (
            "a local-roughness co-location signal, not a scientific defect classifier; a clean "
            "report does not certify the candidate defect-free, and a flagged report does not by "
            "itself disqualify it -- both require downstream human or scientific-gate review"
        ),
    }
