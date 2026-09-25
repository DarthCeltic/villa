"""Exact coordinate-aware resampling between physical pitches."""
from __future__ import annotations

import numpy as np


def axis_weights(n_src: int, src_pitch: float, dst_pitch: float,
                 n_dst: int | None = None) -> tuple[np.ndarray, int]:
    """Row-stochastic (n_dst, n_src) weights mapping one axis by physical overlap."""
    total = n_src * src_pitch
    if n_dst is None:
        n_dst = int(np.floor(total / dst_pitch))
    W = np.zeros((n_dst, n_src), dtype=np.float64)
    for j in range(n_dst):
        lo, hi = j * dst_pitch, (j + 1) * dst_pitch
        i0 = int(np.floor(lo / src_pitch))
        i1 = min(int(np.ceil(hi / src_pitch)), n_src)
        for i in range(max(i0, 0), i1):
            a, b = i * src_pitch, (i + 1) * src_pitch
            overlap = min(hi, b) - max(lo, a)
            if overlap > 0:
                W[j, i] = overlap / dst_pitch
    return W, n_dst


def resample_3d(vol: np.ndarray, src_pitch_zyx, dst_pitch_zyx, n_dst_zyx=None) -> np.ndarray:
    """Integrate a (Z, Y, X) array onto a new physical grid, one axis at a time."""
    out = np.asarray(vol, dtype=np.float64)
    n_dst_zyx = n_dst_zyx or (None, None, None)
    for ax in (0, 1, 2):
        W, _ = axis_weights(out.shape[ax], src_pitch_zyx[ax], dst_pitch_zyx[ax], n_dst_zyx[ax])
        out = np.moveaxis(np.tensordot(W, np.moveaxis(out, ax, 0), axes=([1], [0])), 0, ax)
    return out


def resample_2d(a: np.ndarray, src_pitch: float, dst_pitch: float,
                n_dst=None) -> np.ndarray:
    out = np.asarray(a, dtype=np.float64)
    n_dst = n_dst or (None, None)
    for ax in (0, 1):
        W, _ = axis_weights(out.shape[ax], src_pitch, dst_pitch, n_dst[ax])
        out = np.moveaxis(np.tensordot(W, np.moveaxis(out, ax, 0), axes=([1], [0])), 0, ax)
    return out


def resample_occupancy(binary: np.ndarray, src_pitch: float, dst_pitch: float,
                       n_dst=None) -> np.ndarray:
    """Continuous occupancy in [0,1]."""
    return np.clip(resample_2d(np.asarray(binary, dtype=np.float64), src_pitch, dst_pitch,
                               n_dst), 0.0, 1.0)


def resample_support(mask: np.ndarray, src_pitch: float, dst_pitch: float,
                     n_dst=None, full_coverage: float = 0.99) -> np.ndarray:
    """Support, thresholded CONSERVATIVELY so it shrinks at a boundary rather than growing."""
    frac = resample_2d(np.asarray(mask, dtype=np.float64), src_pitch, dst_pitch, n_dst)
    return frac >= full_coverage


def grid_report(n_src: int, src_pitch: float, dst_pitch: float) -> dict:
    """Extents and residual pitch error, so the transform states its own fidelity."""
    W, n_dst = axis_weights(n_src, src_pitch, dst_pitch)
    src_extent = n_src * src_pitch
    dst_extent = n_dst * dst_pitch
    return {
        "n_src": n_src, "src_pitch_um": src_pitch, "src_extent_um": round(src_extent, 4),
        "n_dst": n_dst, "dst_pitch_um": dst_pitch, "dst_extent_um": round(dst_extent, 4),
        "extent_loss_um": round(src_extent - dst_extent, 4),
        "extent_loss_percent": round(100.0 * (src_extent - dst_extent) / src_extent, 4),
        "achieved_pitch_um": dst_pitch,
        "residual_pitch_error_um": 0.0,
        "residual_pitch_error_percent": 0.0,
        "why_residual_is_zero": "the output grid is DEFINED at dst_pitch and the source is "
                                "integrated onto it, so the achieved pitch is exact by "
                                "construction. The rounded-block path could not say this: it "
                                "chose an integer block and inherited whatever pitch that "
                                "implied, which need not be the requested one.",
        "row_sums_min": float(W.sum(axis=1).min()),
        "row_sums_max": float(W.sum(axis=1).max()),
    }
