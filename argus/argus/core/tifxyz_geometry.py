"""Tifxyz geometry interpreted the same way as upstream Villa."""
from __future__ import annotations

import math

import numpy as np

MISSING = -1.0
BBOX_TOLERANCE = 1e-3


def _axis(stored, scale) -> int:
    n = int(stored)
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        s32 = float(np.float32(scale))
    if not math.isfinite(s32) or s32 <= 0.0:
        return max(1, n)
    return max(1, int(math.floor(float(n) * (1.0 / s32) + 0.5)))


def full_resolution_shape(stored_shape, scale) -> tuple[int, int]:
    """Return ``(rows, columns)`` using Villa's float32 scale and lround semantics."""
    rows, cols = int(stored_shape[0]), int(stored_shape[1])
    if isinstance(scale, (list, tuple, np.ndarray)):
        if len(scale) != 2:
            raise ValueError("scale must be one number or a (row_scale, col_scale) pair")
        row_scale, col_scale = scale
    else:
        row_scale = col_scale = scale
    return _axis(rows, row_scale), _axis(cols, col_scale)


def valid_mask(x, y, z) -> np.ndarray:
    arrays = tuple(np.asarray(a, dtype=np.float64) for a in (x, y, z))
    if not (arrays[0].shape == arrays[1].shape == arrays[2].shape):
        raise ValueError("x, y and z must have the same shape, got %s %s %s"
                         % tuple(a.shape for a in arrays))
    bad = np.zeros(arrays[0].shape, dtype=bool)
    for array in arrays:
        bad |= (array == MISSING) | ~np.isfinite(array)
    return ~bad


def valid_bbox(x, y, z):
    """Bounds over valid points, or ``None`` when the surface has no valid point."""
    mask = valid_mask(x, y, z)
    if not mask.any():
        return None
    arrays = tuple(np.asarray(a, dtype=np.float64)[mask] for a in (x, y, z))
    return (tuple(float(a.min()) for a in arrays), tuple(float(a.max()) for a in arrays))


def _stored_pair(stored):
    try:
        low, high = stored
        low, high = [float(v) for v in low], [float(v) for v in high]
    except (TypeError, ValueError):
        return None
    return (low, high) if len(low) == 3 and len(high) == 3 else None


def bbox_report(stored, x, y, z) -> dict:
    """Compare ``meta.json`` bounds with valid-point bounds without changing the source."""
    valid = valid_bbox(x, y, z)
    recomputed = None if valid is None else [list(valid[0]), list(valid[1])]
    pair = _stored_pair(stored) if stored is not None else None
    if stored is None:
        return {"state": "NO_STORED_BBOX", "stored": None, "recomputed": recomputed,
                "reasons": ["meta.json carries no bbox; the valid-point box is reported"]}
    if pair is None:
        return {"state": "BBOX_UNTRUSTED", "stored": stored, "recomputed": recomputed,
                "reasons": ["the stored bbox is not [[x,y,z],[x,y,z]]"]}
    reasons = []
    if any(v == MISSING for v in pair[0]):
        reasons.append("the stored bbox minimum carries the -1 missing-point marker")
    if recomputed is None:
        reasons.append("the surface has no valid point, so no bbox can be recomputed")
    elif any(abs(a - b) > BBOX_TOLERANCE
             for a, b in zip(pair[0] + pair[1], recomputed[0] + recomputed[1])):
        reasons.append("the stored bbox does not equal the valid-point bbox")
    return {"state": "BBOX_UNTRUSTED" if reasons else "TRUSTED",
            "stored": [pair[0], pair[1]], "recomputed": recomputed, "reasons": reasons}
