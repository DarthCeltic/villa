"""A bounded, identity-checked brick of raw CT for interactive 3D volume rendering."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from argus.core import zarr_volume as ZV
from argus.core.planes import CLIP_MAX

SCHEMA = "argus-volume-brick-v1"
AXIS_ORDER = "zyx"
DEFAULT_EDGE = 256
HARD_MAX_BYTES = 256 * (1 << 20)
HARD_MAX_RAW_BYTES = 512 * (1 << 20)
_DISPLAY_SLAB_BYTES = 16 * (1 << 20)
DEFAULT_BUDGET_BYTES = 64 * (1 << 20)
VRAM_FACTOR = 1.5
LARGER_BRICK_EDGE = 512


class BrickRefused(ValueError):
    def __init__(self, code: str, why: str, evidence: dict | None = None):
        super().__init__(why)
        self.code, self.why, self.evidence = code, why, evidence or {}


def _zattrs(store: Path) -> dict:
    return ZV._zattrs(store)


def declared_scale_zyx(zattrs: dict, level: str) -> list[float] | None:
    ds0 = next((d for d in (zattrs.get("multiscales") or [{}])[0].get("datasets", [])
                if str(d.get("path")) == "0"), None)
    for t in (ds0 or {}).get("coordinateTransformations", []):
        s0 = t.get("scale")
        if s0 and len(s0) >= 3 and all(float(a) == 1.0 for a in s0[-3:]):
            return None
    for ds in (zattrs.get("multiscales") or [{}])[0].get("datasets", []):
        if str(ds.get("path")) != str(level):
            continue
        for t in ds.get("coordinateTransformations", []):
            s = t.get("scale")
            if s and len(s) >= 3:
                v = [float(a) for a in s[-3:]]
                if all(a == 1.0 for a in v):
                    return None
                return v
    return None


def _level_info(store: Path, level: str) -> dict:
    probe = ZV.probe_store(store)
    for lv in probe["levels"]:
        if str(lv["level"]) == str(level):
            if not lv.get("openable"):
                raise BrickRefused("LEVEL_NOT_OPENABLE", "level %s of this store is declared but cannot be opened: %s"
                                   % (level, lv.get("why_not_openable")))
            if len(lv.get("shape") or []) != 3:
                raise BrickRefused("NOT_A_3D_LEVEL", "level %s declares %d axes; a brick is cut from a three-dimensional (z, y, x) level" % (level, len(lv.get("shape") or [])))
            return lv
    raise BrickRefused("UNKNOWN_LEVEL", "this store declares no level %r" % level)


def _roi(shape_level, edge_or_shape, centre, origin) -> tuple[list[int], list[int], bool, list[int] | None]:
    if isinstance(edge_or_shape, int):
        shape = [edge_or_shape] * 3
    else:
        shape = [int(v) for v in edge_or_shape]
    if len(shape) != 3 or any(v < 1 for v in shape):
        raise BrickRefused("BAD_SHAPE", "the brick shape must be three positive integers (z, y, x)")
    if any(s > m for s, m in zip(shape, shape_level)):
        raise BrickRefused("BRICK_LARGER_THAN_LEVEL", "a brick of %s does not fit inside this level's extent %s; ask for a "
                           "brick the level actually has" % (shape, list(shape_level)))
    requested = None
    clamped = False
    if origin is not None:
        o = [int(v) for v in origin]
        if len(o) != 3 or any(a < 0 or a + s > m for a, s, m in zip(o, shape, shape_level)):
            raise BrickRefused("ROI_OUT_OF_BOUNDS", "the ROI origin %s with shape %s falls outside this level's extent %s"
                               % (o, shape, list(shape_level)))
        return o, shape, False, None
    if centre is None:
        raise BrickRefused("ROI_REQUIRED", "an explicit centre or origin is required: a brick is never chosen for you")
    requested = [int(v) for v in centre]
    if len(requested) != 3 or any(c < 0 or c >= m for c, m in zip(requested, shape_level)):
        raise BrickRefused("ROI_OUT_OF_BOUNDS", "the centre %s is outside this level's extent %s" % (requested, list(shape_level)))
    o = [c - s // 2 for c, s in zip(requested, shape)]
    fitted = [min(max(a, 0), m - s) for a, s, m in zip(o, shape, shape_level)]
    clamped = fitted != o
    return fitted, shape, clamped, requested


def plan(store: Path, level: str, *, centre=None, origin=None, edge_or_shape=DEFAULT_EDGE, budget_bytes: int = DEFAULT_BUDGET_BYTES,
         max_texture: int | None = None, vram_budget_bytes: int | None = None) -> dict:
    """Everything a caller needs to decide, and nothing read from the volume."""
    lv = _level_info(Path(store), level)
    shape_level = [int(v) for v in lv["shape"]]
    o, shape, clamped, requested = _roi(shape_level, edge_or_shape, centre, origin)
    nbytes = math.prod(int(v) for v in shape)
    try:
        itemsize = int(np.dtype(lv.get("dtype")).itemsize)
    except (TypeError, ValueError):
        raise BrickRefused("UNREADABLE_DTYPE", "this level declares a dtype ARGUS cannot size: %r" % (lv.get("dtype"),)) from None
    raw_bytes = nbytes * itemsize
    vram = int(nbytes * VRAM_FACTOR)
    limits = {"hard_max_bytes": HARD_MAX_BYTES, "budget_bytes": int(budget_bytes), "max_texture_size": max_texture,
              "vram_budget_bytes": vram_budget_bytes}
    if nbytes > HARD_MAX_BYTES:
        raise BrickRefused("BRICK_TOO_LARGE", "%d bytes is over the hard ceiling of %d; a volume is never sent whole" % (nbytes, HARD_MAX_BYTES), limits)
    if raw_bytes > HARD_MAX_RAW_BYTES:
        raise BrickRefused("BRICK_TOO_LARGE", "%d source bytes (%d voxels of %d bytes) is over the ceiling of %d; ask for a smaller brick"
                           % (raw_bytes, nbytes, itemsize, HARD_MAX_RAW_BYTES), limits)
    if nbytes > budget_bytes:
        raise BrickRefused("OVER_BYTE_BUDGET", "%d bytes is over this request's budget of %d; raise the budget explicitly or ask for a smaller brick"
                           % (nbytes, budget_bytes), limits)
    if max_texture is not None and any(v > max_texture for v in shape):
        raise BrickRefused("TEXTURE_LIMIT_EXCEEDED", "this browser's 3D texture limit is %d; a brick of %s cannot be uploaded" % (max_texture, shape), limits)
    if vram_budget_bytes is not None and vram > vram_budget_bytes:
        raise BrickRefused("OVER_VRAM_BUDGET", "the estimated GPU memory %d exceeds the budget %d for this hardware tier" % (vram, vram_budget_bytes), limits)
    za = _zattrs(Path(store))
    spacing = declared_scale_zyx(za, level)
    return {"schema": SCHEMA, "store": str(store), "level": str(level), "axis_order": AXIS_ORDER, "shape_zyx": shape,
            "origin_voxel_zyx": o, "requested_centre_voxel_zyx": requested, "origin_adjusted_to_fit": clamped,
            "level_extent_zyx": shape_level, "bytes": nbytes, "source_bytes": raw_bytes, "estimated_gpu_bytes": vram, "limits": limits,
            "spacing_um_zyx": spacing, "spacing_status": "DECLARED_BY_STORE" if spacing else "NOT_DECLARED",
            "origin_um_zyx": [a * s for a, s in zip(o, spacing)] if spacing else None,
            "dtype_source": lv.get("dtype")}


def display_bytes(raw: np.ndarray) -> np.ndarray:
    flat = np.ascontiguousarray(raw).reshape(-1)
    out = np.empty(flat.shape, dtype=np.uint8)
    step = max(1, _DISPLAY_SLAB_BYTES // 16)
    for start in range(0, flat.size, step):
        shown = np.clip(flat[start:start + step].astype(np.float32), 0, CLIP_MAX) * (255.0 / CLIP_MAX)
        out[start:start + step] = shown.astype(np.uint8)
    return out.reshape(raw.shape)


def read(store: Path, level: str, *, centre=None, origin=None, edge_or_shape=DEFAULT_EDGE, budget_bytes: int = DEFAULT_BUDGET_BYTES,
         max_texture: int | None = None, vram_budget_bytes: int | None = None, allow_partial: bool = False) -> tuple[bytes, dict]:
    p = plan(store, level, centre=centre, origin=origin, edge_or_shape=edge_or_shape, budget_bytes=budget_bytes,
             max_texture=max_texture, vram_budget_bytes=vram_budget_bytes)
    z0, y0, x0 = p["origin_voxel_zyx"]
    dz, dy, dx = p["shape_zyx"]
    missing = ZV.missing_chunks_in_region(Path(store), str(level), z0, z0 + dz, y0, y0 + dy, x0, x0 + dx)
    if missing and not allow_partial:
        raise BrickRefused("MISSING_CHUNKS", "%d chunk(s) under this brick are absent on disk, not merely zero; they are never "
                           "returned as CT signal" % len(missing), {"missing_chunks": missing[:64], "missing_count": len(missing)})
    from argus_vesuvius.volume import VolumeDependencyUnavailable, open_pyramid_level
    try:
        arr, _scale = ZV._retry_transient(lambda: open_pyramid_level(str(store), str(level)))
    except VolumeDependencyUnavailable as exc:
        raise BrickRefused("DATA_UNAVAILABLE", str(exc))
    raw = np.asarray(ZV._retry_transient(lambda: arr[z0:z0 + dz, y0:y0 + dy, x0:x0 + dx]))
    if missing:
        cz, cy, cx = _chunks(Path(store), str(level))
        for zi, yi, xi in missing:
            raw[max(zi * cz - z0, 0):max((zi + 1) * cz - z0, 0), max(yi * cy - y0, 0):max((yi + 1) * cy - y0, 0),
                max(xi * cx - x0, 0):max((xi + 1) * cx - x0, 0)] = 0
    data = display_bytes(raw)
    body = data.tobytes(order="C")
    meta = dict(p)
    meta.update({
        "byte_order": "C (z slowest, x fastest)", "dtype": "uint8",
        "display_mapping": {"mode": "fixed", "clip_max": CLIP_MAX, "formula": "clip(v, 0, %d) * 255 / %d" % (CLIP_MAX, CLIP_MAX),
                            "comparable_with": "/api/volume_plane"},
        "missing_chunk_count": len(missing), "missing_chunks": missing[:256], "missing_voxels_are_zero_and_declared_missing": bool(missing),
        "raw_min": int(raw.min()), "raw_max": int(raw.max()), "sha256": hashlib.sha256(body).hexdigest(),
        "note": "raw CT visualization; a display setting never changes these bytes and this brick is not a detector output"})
    return body, meta


def _chunks(store: Path, level: str) -> tuple[int, int, int]:
    za = json.loads((store / str(level) / ".zarray").read_text(encoding="utf-8"))
    return tuple(za["chunks"])
