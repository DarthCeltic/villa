"""Depth compositing along a sheet normal, at fixed PHYSICAL offsets, with identical-coordinate overlays."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, NamedTuple, Sequence

import numpy as np
from scipy import ndimage

from argus.core.signed_normal import ONE_SIDED_MAX_DEPTH_LAYERS, interior_only_allowed

EVIDENCE_LABEL = "PUBLISHED_3D_PREDICTION_LOCALIZED_LIKELY_INK"

COMPOSITE_MODES = ("mean", "max", "min")
UNSIGNED_LABEL = "UNSIGNED_BOTH_ORIENTATIONS"


class OffsetRefused(ValueError):
    pass


class InteriorRefused(RuntimeError):
    pass


class CoordinateMismatch(ValueError):
    pass


class SampledStack(NamedTuple):
    stack: np.ndarray
    coords: np.ndarray
    layer_indices: tuple[int, ...]


@dataclass(frozen=True)
class UnsignedOrientations:
    """Both one-sided composites; which one is interior is NOT known here."""

    positive_side: np.ndarray
    negative_side: np.ndarray
    label: str = UNSIGNED_LABEL
    sign_trusted: bool = False

    def __iter__(self):
        yield self.positive_side
        yield self.negative_side


def physical_offsets_to_layers(
    offsets_um: Sequence[float],
    pitch_um: float,
    *,
    tol_fraction: float = 0.25,
) -> list[tuple[float, int, float]]:
    """Map physical offsets to integer layers; refuse offsets that fall between layers."""
    pitch = float(pitch_um)
    if not math.isfinite(pitch) or pitch <= 0:
        raise OffsetRefused(f"pitch must be a positive finite number of micrometres, got {pitch_um!r}")
    if not 0.0 <= tol_fraction < 0.5:
        raise OffsetRefused("tol_fraction must be in [0, 0.5)")
    out: list[tuple[float, int, float]] = []
    for off in offsets_um:
        off = float(off)
        if not math.isfinite(off):
            raise OffsetRefused(f"offset {off!r} is not finite")
        layers = off / pitch
        k = int(round(layers))
        if abs(layers - k) > tol_fraction:
            raise OffsetRefused(
                f"offset {off} um is {layers:.3f} layers at pitch {pitch} um; "
                f"more than {tol_fraction} from a whole layer")
        out.append((off, k, off - k * pitch))
    return out


def sample_along_normal(
    volume: Any,
    points: Any,
    normals: Any,
    layer_indices: Sequence[int],
    *,
    order: int = 1,
) -> SampledStack:
    """Sample `volume` at points + k * unit_normal for each integer layer k (voxel units)."""
    vol = np.asarray(volume)
    if vol.ndim != 3:
        raise ValueError("volume must be 3-D [z, y, x]")
    pts = np.asarray(points, dtype=np.float64)
    nrm = np.asarray(normals, dtype=np.float64)
    if pts.shape != nrm.shape or pts.shape[-1] != 3:
        raise ValueError("points and normals must share shape (..., 3)")
    layers = tuple(int(k) for k in layer_indices)
    if not layers:
        raise ValueError("layer_indices is empty")

    norm = np.linalg.norm(nrm, axis=-1, keepdims=True)
    unit = np.divide(nrm, norm, out=np.full_like(nrm, np.nan), where=norm > 1e-12)
    coords = np.stack([pts + k * unit for k in layers], axis=0).astype(np.float32)

    flat = coords.reshape(-1, 3)
    upper = np.asarray(vol.shape, dtype=np.float64) - 1.0
    ok = np.isfinite(flat).all(axis=1) & (flat >= 0).all(axis=1) & (flat <= upper).all(axis=1)
    sampled = np.full(flat.shape[0], np.nan, dtype=np.float32)
    if ok.any():
        sampled[ok] = ndimage.map_coordinates(
            vol, flat[ok].T.astype(np.float64), order=order, mode="nearest", output=np.float32)
    stack = sampled.reshape(coords.shape[:-1])
    return SampledStack(stack=stack, coords=coords, layer_indices=layers)


def coordinates_hash(coords: Any) -> str:
    arr = np.ascontiguousarray(np.asarray(coords, dtype="<f4"))
    h = hashlib.sha256()
    h.update(repr(tuple(arr.shape)).encode("ascii"))
    h.update(arr.tobytes())
    return h.hexdigest()


def identical_coordinates(a: Any, b: Any) -> bool:
    return coordinates_hash(a) == coordinates_hash(b)


def _reduce(sub: np.ndarray, mode: str) -> np.ndarray:
    if mode not in COMPOSITE_MODES:
        raise ValueError(f"mode must be one of {COMPOSITE_MODES}, got {mode!r}")
    finite = np.isfinite(sub)
    count = finite.sum(axis=0)
    if mode == "mean":
        total = np.where(finite, sub, 0.0).sum(axis=0, dtype=np.float64)
        out = np.divide(total, count, out=np.full(total.shape, np.nan), where=count > 0)
    elif mode == "max":
        out = np.where(finite, sub, -np.inf).max(axis=0).astype(np.float64)
    else:
        out = np.where(finite, sub, np.inf).min(axis=0).astype(np.float64)
    out[count == 0] = np.nan
    return out.astype(np.float32)


def _window(stack: np.ndarray, lo: int, hi: int, mode: str) -> np.ndarray:
    if lo < 0 or hi >= stack.shape[0] or lo > hi:
        raise ValueError(f"layer window [{lo}, {hi}] outside stack of {stack.shape[0]} layers")
    return _reduce(stack[lo:hi + 1], mode)


def symmetric_composite(stack: Any, center_index: int, half_width: int, *, mode: str = "mean") -> np.ndarray:
    arr = np.asarray(stack, dtype=np.float64)
    if half_width < 0:
        raise ValueError("half_width must be >= 0")
    return _window(arr, center_index - half_width, center_index + half_width, mode)


def interior_composite(
    stack: Any,
    center_index: int,
    decision: Any,
    *,
    depth_layers: int,
    mode: str = "mean",
) -> np.ndarray:
    """Composite only the interior side, and only if `interior_only_allowed` says so."""
    allowed, reason = interior_only_allowed(decision)
    if not allowed:
        raise InteriorRefused(reason)
    if depth_layers < 0:
        raise ValueError("depth_layers must be >= 0")
    if depth_layers > ONE_SIDED_MAX_DEPTH_LAYERS:
        raise OffsetRefused(
            f"a one-sided depth of {depth_layers} layers exceeds {ONE_SIDED_MAX_DEPTH_LAYERS}; deeper offsets "
            f"pull in ink from the neighbouring sheet")
    arr = np.asarray(stack, dtype=np.float64)
    if decision.sign == 1:
        return _window(arr, center_index, center_index + depth_layers, mode)
    return _window(arr, center_index - depth_layers, center_index, mode)


def default_composite(
    stack: Any,
    center_index: int,
    decision: Any,
    *,
    half_width: int,
    mode: str = "mean",
) -> tuple[np.ndarray, dict[str, Any]]:
    """The composite to show by default: symmetric, unless orientation is CONFIRMED (then one-sided)."""
    allowed, reason = interior_only_allowed(decision)
    status = getattr(decision, "orientation_status", "ORIENTATION_UNCONFIRMED")
    if allowed:
        depth = min(int(half_width), ONE_SIDED_MAX_DEPTH_LAYERS)
        img = interior_composite(stack, center_index, decision, depth_layers=depth, mode=mode)
        return img, {"composite": "ONE_SIDED_INTERIOR", "depth_layers": depth, "orientation_status": status,
                     "reason": reason}
    img = symmetric_composite(stack, center_index, half_width, mode=mode)
    return img, {"composite": "SYMMETRIC", "half_width": int(half_width), "orientation_status": status,
                 "reason": reason}


def both_orientations(stack: Any, center_index: int, depth_layers: int, mode: str = "mean") -> UnsignedOrientations:
    """One-sided composites for BOTH sides; use when the sign is not trusted."""
    arr = np.asarray(stack, dtype=np.float64)
    if depth_layers < 0:
        raise ValueError("depth_layers must be >= 0")
    return UnsignedOrientations(
        positive_side=_window(arr, center_index, center_index + depth_layers, mode),
        negative_side=_window(arr, center_index - depth_layers, center_index, mode),
    )


def overlay_sample(
    prediction_volume: Any,
    points: Any,
    normals: Any,
    layer_indices: Sequence[int],
    *,
    order: int = 1,
) -> SampledStack:
    """Sample a prediction volume at coordinates identical to the CT sampling for the same inputs."""
    return sample_along_normal(prediction_volume, points, normals, layer_indices, order=order)


def _layer_stats(a: np.ndarray) -> tuple[float | None, float | None, float]:
    finite = np.isfinite(a)
    frac = float(finite.mean()) if a.size else 0.0
    if not finite.any():
        return None, None, frac
    v = a[finite].astype(np.float64)
    return float(v.mean()), float(v.std()), frac


def _pearson(a: np.ndarray, b: np.ndarray) -> tuple[float | None, int]:
    both = np.isfinite(a) & np.isfinite(b)
    n = int(both.sum())
    if n < 2:
        return None, n
    x = a[both].astype(np.float64)
    y = b[both].astype(np.float64)
    x -= x.mean()
    y -= y.mean()
    denom = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    if denom == 0.0:
        return None, n
    return float(np.clip(float((x * y).sum()) / denom, -1.0, 1.0)), n


def compare_identical_coordinates(
    ct_stack: Any,
    ct_coords: Any,
    pred_stack: Any,
    pred_coords: Any,
    *,
    layer_indices: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Descriptive per-layer statistics for CT vs prediction at byte-identical coordinates."""
    if not identical_coordinates(ct_coords, pred_coords):
        raise CoordinateMismatch("CT and prediction were not sampled at identical coordinates")
    ct = np.asarray(ct_stack, dtype=np.float64)
    pred = np.asarray(pred_stack, dtype=np.float64)
    if ct.shape != pred.shape:
        raise CoordinateMismatch(f"stack shapes differ: {ct.shape} vs {pred.shape}")
    if layer_indices is not None and len(layer_indices) != ct.shape[0]:
        raise ValueError("layer_indices length must equal the number of layers")

    per_layer: list[dict[str, Any]] = []
    for i in range(ct.shape[0]):
        c_mean, c_std, c_frac = _layer_stats(ct[i])
        p_mean, p_std, p_frac = _layer_stats(pred[i])
        r, n_pairs = _pearson(ct[i], pred[i])
        per_layer.append({
            "layer_position": i,
            "layer_index": None if layer_indices is None else int(layer_indices[i]),
            "ct_mean": c_mean, "ct_std": c_std, "ct_finite_fraction": c_frac,
            "pred_mean": p_mean, "pred_std": p_std, "pred_finite_fraction": p_frac,
            "pearson_r": r, "n_paired": n_pairs,
        })
    return {
        "coordinates_sha256": coordinates_hash(ct_coords),
        "n_layers": int(ct.shape[0]),
        "per_layer": per_layer,
        "descriptive_only": True,
        "prediction_is_ground_truth": False,
    }
