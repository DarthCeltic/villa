"""Persisted per-pixel 2D<->3D coordinate map for a flattened/rendered surface."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from pathlib import Path

import numpy as np

CONTRACT = "argus-coordinate-map-v1"

INVALID_SENTINEL = -1.0


class CoordinateMapRefusal(Exception):
    """Raised for a request this module will not silently misanswer: a point outside the array, a point the map itself marked invalid, a reload whose bytes no longer match their own recorded hash."""


def _sha(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


@dataclasses.dataclass(frozen=True)
class CoordinateMap:
    Xd: np.ndarray
    Yd: np.ndarray
    Zd: np.ndarray
    okd: np.ndarray
    meta: dict

    @property
    def shape(self) -> tuple[int, int]:
        return self.Xd.shape


def save_coordinate_map(
    out_dir: Path, *, Xd: np.ndarray, Yd: np.ndarray, Zd: np.ndarray, okd: np.ndarray,
    source_volume_id: str, source_array_path: str, mesh_identity: str, voxel_um: float,
    row_axis: str = "mesh parametrization row (v)",
    col_axis: str = "mesh parametrization column (u)",
    units: str = "volume voxels (multiply by voxel_um for physical units)",
    extra: dict | None = None,
) -> Path:
    """Write the coordinate map beside a render."""
    if Xd.shape != Yd.shape or Xd.shape != Zd.shape or Xd.shape != okd.shape:
        raise CoordinateMapRefusal(
            "Xd/Yd/Zd/okd must share one shape; got %r/%r/%r/%r"
            % (Xd.shape, Yd.shape, Zd.shape, okd.shape))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    Xd = np.asarray(Xd, dtype=np.float64)
    Yd = np.asarray(Yd, dtype=np.float64)
    Zd = np.asarray(Zd, dtype=np.float64)
    okd = np.asarray(okd, dtype=bool)
    npz_path = out_dir / "coordinate_map.npz"
    np.savez(npz_path, Xd=Xd, Yd=Yd, Zd=Zd, okd=okd)
    meta = {
        "contract": CONTRACT,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_volume_id": source_volume_id,
        "source_array_path": source_array_path,
        "mesh_identity": mesh_identity,
        "voxel_um": float(voxel_um),
        "units": units,
        "shape": list(Xd.shape),
        "orientation": {"row_axis": row_axis, "col_axis": col_axis},
        "invalid_sentinel": INVALID_SENTINEL,
        "valid_pixel_count": int(okd.sum()),
        "hashes": {"Xd": _sha(Xd), "Yd": _sha(Yd), "Zd": _sha(Zd), "okd": _sha(okd)},
        "npz_file": npz_path.name,
    }
    if extra:
        meta["extra"] = extra
    json_path = out_dir / "coordinate_map.json"
    json_path.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return json_path


def load_coordinate_map(json_path: Path) -> CoordinateMap:
    """Read a coordinate map back and verify it against its own recorded hashes."""
    json_path = Path(json_path)
    if not json_path.is_file():
        raise CoordinateMapRefusal("no coordinate map at %s" % json_path)
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    if meta.get("contract") != CONTRACT:
        raise CoordinateMapRefusal(
            "unrecognised contract %r at %s" % (meta.get("contract"), json_path))
    npz_path = json_path.parent / meta["npz_file"]
    if not npz_path.is_file():
        raise CoordinateMapRefusal("sidecar names %s but it is missing" % npz_path)
    with np.load(npz_path) as z:
        Xd, Yd, Zd, okd = z["Xd"], z["Yd"], z["Zd"], z["okd"].astype(bool)
    got = {"Xd": _sha(Xd), "Yd": _sha(Yd), "Zd": _sha(Zd), "okd": _sha(okd)}
    want = meta.get("hashes", {})
    mismatched = [k for k in want if want.get(k) != got.get(k)]
    if mismatched:
        raise CoordinateMapRefusal(
            "reload does not match its own recorded hash for %s -- the map on disk has "
            "changed since it was saved, or is not the file it claims to be" % mismatched)
    return CoordinateMap(Xd=Xd, Yd=Yd, Zd=Zd, okd=okd, meta=meta)


def lookup_2d_to_3d(cmap: CoordinateMap, row: int, col: int) -> tuple[float, float, float]:
    """The pixel a person marked -> the 3D point it shows."""
    h, w = cmap.shape
    if not (0 <= row < h and 0 <= col < w):
        raise CoordinateMapRefusal(
            "(row=%r, col=%r) is outside this map's %dx%d extent" % (row, col, h, w))
    if not bool(cmap.okd[row, col]):
        raise CoordinateMapRefusal(
            "(row=%r, col=%r) is a pixel this map marked invalid -- no 3D point was ever "
            "sampled there" % (row, col))
    return (float(cmap.Xd[row, col]), float(cmap.Yd[row, col]), float(cmap.Zd[row, col]))


def lookup_3d_to_nearest_2d(cmap: CoordinateMap, x: float, y: float, z: float) -> dict:
    """The 3D point a correction or a detector hit names -> the nearest pixel that shows it."""
    valid = cmap.okd
    if not valid.any():
        raise CoordinateMapRefusal("this map has no valid pixels at all")
    dx = cmap.Xd - x
    dy = cmap.Yd - y
    dz = cmap.Zd - z
    d2 = dx * dx + dy * dy + dz * dz
    d2 = np.where(valid, d2, np.inf)
    flat = int(np.argmin(d2))
    row, col = divmod(flat, cmap.shape[1])
    return {"row": row, "col": col, "distance_voxels": float(np.sqrt(d2[row, col]))}


def round_trip_error_voxels(cmap: CoordinateMap, row: int, col: int) -> float:
    """2D -> 3D -> nearest 2D, back to pixel-distance from where it started."""
    x, y, z = lookup_2d_to_3d(cmap, row, col)
    back = lookup_3d_to_nearest_2d(cmap, x, y, z)
    return float(np.hypot(back["row"] - row, back["col"] - col))
