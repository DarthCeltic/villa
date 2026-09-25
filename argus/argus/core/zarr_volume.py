"""Real raw-CT access through an actual OME-Zarr multiresolution pyramid."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from argus.core import pitch as PITCH
from argus.core.contracts import Refusal
from argus.core.safe_names import is_safe_level


class ZarrVolumeRefusal(Refusal):
    def __init__(self, reason: str, evidence: dict | None = None):
        super().__init__("DATA_UNAVAILABLE", reason, evidence)


AXES = ("z", "y", "x")
PLANES = {
  "xy": ("z", "y", "x"),
  "xz": ("y", "z", "x"),
  "yz": ("x", "z", "y"),
}


def _zattrs(store_path: Path) -> dict:
    p = Path(store_path) / ".zattrs"
    if not p.is_file():
        raise ZarrVolumeRefusal("no .zattrs at %s -- not an OME-Zarr store" % store_path)
    raw = p.read_text(encoding="utf-8")
    if not raw.strip():
        raise ZarrVolumeRefusal(
            "%s is header-only -- .zattrs exists but has no metadata body" % p)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ZarrVolumeRefusal("%s is malformed JSON, not a usable OME-Zarr store: %s" % (p, exc))


def _identity_level0_pitch(store_path: Path):
    """Level-0 pitch (um) from the store's own STORE_IDENTITY `asserted.pitch_um` (only ever a PROVEN value), when that identity names level 0."""
    try:
        body = json.loads((Path(store_path) / "STORE_IDENTITY.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if str(body.get("array_path") or "0") != "0":
        return None
    try:
        v = float((body.get("asserted") or {}).get("pitch_um"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def probe_store(store_path: Path, level0_pitch_um=None) -> dict:
    """Every level the store DECLARES, whether each is actually OPENABLE, and its real shape/chunks/dtype/pitch when it is."""
    store_path = Path(store_path)
    zattrs = _zattrs(store_path)
    ms = zattrs.get("multiscales")
    if not ms:
        raise ZarrVolumeRefusal("%s declares no multiscales -- not a pyramid" % store_path)
    if level0_pitch_um is None:
        level0_pitch_um = _identity_level0_pitch(store_path)
    axes = [a.get("name") for a in ms[0].get("axes", [])] or None
    levels = []
    for ds in ms[0].get("datasets", []):
        level = str(ds["path"])
        zarray_path = store_path / level / ".zarray"
        entry: dict = {"level": level, "declared": True, "openable": False}
        if not zarray_path.is_file():
            entry["why_not_openable"] = ("chunk data exists on disk but no .zarray sidecar "
                                         "does, so no array can be constructed for this level")
        else:
            try:
                raw = zarray_path.read_text(encoding="utf-8")
                if not raw.strip():
                    raise ValueError("'.zarray' sidecar is empty (header-only, no metadata body)")
                za = json.loads(raw)
                shape, chunks, dtype = za["shape"], za["chunks"], za["dtype"]
                if not shape or not chunks:
                    raise ValueError("'.zarray' declares an empty shape or chunks field")
                _validate_extent(shape, chunks, dtype)
            except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
                entry["why_not_openable"] = (
                    "'.zarray' sidecar exists but is malformed and cannot be trusted: %s" % exc)
            else:
                entry.update(openable=True, shape=shape, chunks=chunks, dtype=dtype,
                             compressor=za.get("compressor"))
        ev = PITCH.from_ome(zattrs, level=level, level0_pitch_um=level0_pitch_um)
        entry["pitch_um_yx"] = list(ev.pitch_um_yx) if ev.pitch_um_yx else None
        entry["pitch_status"] = ev.status
        levels.append(entry)
    return {
      "schema": "argus-zarr-volume-v1", "store": str(store_path), "axes": axes,
      "canvas_size": zattrs.get("canvas_size"), "num_slices": zattrs.get("num_slices"),
      "levels": levels,
      "note": ("'declared' means the store's own multiscales metadata names this level; "
              "'openable' means a real array can be constructed for it right now. A level "
              "can be declared and not openable -- that is reported, never silently skipped "
              "or silently treated as available."),
    }


def _level_dir(store_path: Path, level: str) -> Path:
    if not is_safe_level(str(level)):
        raise ZarrVolumeRefusal("level %r is not a pyramid level key" % (level,))
    return Path(store_path) / str(level)


def _chunk_key(store_path: Path, level: str, chunk_idx: tuple, dimension_separator: str) -> Path:
    parts = [str(i) for i in chunk_idx]
    if dimension_separator == "/":
        return _level_dir(store_path, level).joinpath(*parts)
    return _level_dir(store_path, level) / dimension_separator.join(parts)


MAX_CHUNK_PROBES = 100_000
MAX_CHUNK_BYTES = 64 << 20


def _dtype_bytes(dtype) -> int:
    try:
        import numpy as np
        size = int(np.dtype(dtype).itemsize)
        if size >= 1:
            return size
    except Exception:
        pass
    digits = "".join(ch for ch in str(dtype or "") if ch.isdigit())
    return max(1, int(digits)) if digits else 8


def _validate_extent(shape, chunks, dtype=None) -> None:
    """A store's own declared shape and chunking are input: positive plain integers, one chunk length per axis, and a decoded chunk of bounded size."""
    for name, seq in (("shape", shape), ("chunks", chunks)):
        if not isinstance(seq, list) or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in seq):
            raise ValueError("'.zarray' %s must be a list of positive integers" % name)
    if len(shape) != len(chunks):
        raise ValueError("'.zarray' declares %d axes of shape but %d of chunks" % (len(shape), len(chunks)))
    chunk_bytes = 1
    for c in chunks:
        chunk_bytes *= c
    chunk_bytes *= _dtype_bytes(dtype)
    if chunk_bytes > MAX_CHUNK_BYTES:
        raise ValueError("'.zarray' declares a decoded chunk of %d bytes; ARGUS reads chunks of at most %d" % (chunk_bytes, MAX_CHUNK_BYTES))


def missing_chunks_in_region(store_path: Path, level: str, z0: int, z1: int, y0: int, y1: int,
                             x0: int, x1: int) -> list:
    """Chunk indices (z_idx, y_idx, x_idx) that the region [z0:z1, y0:y1, x0:x1] touches and that have no file on disk -- checked directly, never inferred from the array read (which would return..."""
    store_path = Path(store_path)
    zarray_path = _level_dir(store_path, level) / ".zarray"
    if not zarray_path.is_file():
        raise ZarrVolumeRefusal("level %r is not openable at %s" % (level, store_path))
    za = json.loads(zarray_path.read_text(encoding="utf-8"))
    try:
        _validate_extent(za["shape"], za["chunks"], za.get("dtype"))
        cz, cy, cx = za["chunks"]
    except (KeyError, ValueError, TypeError) as exc:
        raise ZarrVolumeRefusal("level %r has an unusable .zarray: %s" % (level, exc)) from None
    sep = za.get("dimension_separator", ".")
    probes = ((z1 - 1) // cz - z0 // cz + 1) * ((y1 - 1) // cy - y0 // cy + 1) * ((x1 - 1) // cx - x0 // cx + 1)
    if probes > MAX_CHUNK_PROBES:
        raise ZarrVolumeRefusal("this region touches %d chunks of %s; over the limit of %d" % (probes, (cz, cy, cx), MAX_CHUNK_PROBES))
    missing = []
    for zi in range(z0 // cz, (z1 - 1) // cz + 1):
        for yi in range(y0 // cy, (y1 - 1) // cy + 1):
            for xi in range(x0 // cx, (x1 - 1) // cx + 1):
                if not _chunk_key(store_path, level, (zi, yi, xi), sep).is_file():
                    missing.append([zi, yi, xi])
    return missing


_PERMANENT_OSERRORS = (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError)
_TRANSIENT_ERRORS = (TimeoutError, ConnectionError, OSError)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _PERMANENT_OSERRORS):
        return False
    return isinstance(exc, _TRANSIENT_ERRORS)


def _retry_transient(fn, *, attempts: int = 3, base_delay: float = 0.05):
    """Call `fn()`, retrying up to `attempts` times on a transient error with a short linear backoff, and re-raising immediately (no retry, no delay) on anything permanent -- a `ZarrVolumeRefusal` (this..."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ZarrVolumeRefusal:
            raise
        except Exception as exc:
            if not _is_transient(exc) or attempt == attempts:
                raise
            time.sleep(base_delay * attempt)
    raise AssertionError("unreachable -- the loop above always returns or raises")


def read_plane(store_path: Path, level: str, plane: str, index: int, u0: int, v0: int,
               h: int, w: int, *, allow_partial: bool = False, retries: int = 3) -> dict:
    """One 2D plane from a real pyramid level, in the SAME (plane, fixed-axis-index, 2D-crop-origin, height, width) shape `argus.core.planes.crop` already uses for the staged TIFF planes -- so a caller..."""
    if plane not in PLANES:
        raise ZarrVolumeRefusal("unknown plane %r; must be one of %s" % (plane, ", ".join(PLANES)))
    if not is_safe_level(str(level)):
        raise ZarrVolumeRefusal("level %r is not a pyramid level key" % (level,))
    from argus_vesuvius.volume import VolumeDependencyUnavailable, open_pyramid_level

    try:
        arr, scale = _retry_transient(
            lambda: open_pyramid_level(str(store_path), level), attempts=retries)
    except VolumeDependencyUnavailable as exc:
        raise ZarrVolumeRefusal(str(exc))
    except FileNotFoundError:
        raise ZarrVolumeRefusal("level %r is not openable at %s" % (level, store_path))

    fixed_axis, axis_u, axis_v = PLANES[plane]
    fixed_i, u_i, v_i = AXES.index(fixed_axis), AXES.index(axis_u), AXES.index(axis_v)
    shape = arr.shape
    if not (0 <= index < shape[fixed_i]):
        raise ZarrVolumeRefusal("index %d is outside axis %s (0..%d)"
                                % (index, fixed_axis, shape[fixed_i] - 1))
    if u0 < 0 or v0 < 0 or u0 + h > shape[u_i] or v0 + w > shape[v_i]:
        raise ZarrVolumeRefusal(
          "requested window (%d..%d, %d..%d) does not fit inside this level's own %s=%d/%s=%d "
          "extent. Never silently shrunk to fit -- ask for a window the level actually has."
          % (u0, u0 + h, v0, v0 + w, axis_u, shape[u_i], axis_v, shape[v_i]))

    bounds = {fixed_axis: (index, index + 1), axis_u: (u0, u0 + h), axis_v: (v0, v0 + w)}
    z0, z1 = bounds["z"]
    y0, y1 = bounds["y"]
    x0, x1 = bounds["x"]
    missing = missing_chunks_in_region(store_path, level, z0, z1, y0, y1, x0, x1)
    if missing and not allow_partial:
        raise ZarrVolumeRefusal(
          "%d chunk(s) in this region are missing on disk, not merely zero -- refusing to "
          "return them as CT signal. Pass allow_partial to read the rest anyway."
          % len(missing), evidence={"missing_chunks": missing})

    np = __import__("numpy")
    sl = [slice(None)] * 3
    sl[fixed_i] = index
    sl[u_i] = slice(u0, u0 + h)
    sl[v_i] = slice(v0, v0 + w)
    block = np.asarray(_retry_transient(lambda: arr[tuple(sl)], attempts=retries))
    return {"data": block, "scale": scale, "missing_chunks": missing,
           "plane": plane, "index": index, "shape": list(block.shape)}
