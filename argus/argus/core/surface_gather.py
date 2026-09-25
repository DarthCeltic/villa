"""Mesh-normal-aware 3D -> 2D gather: sample a volume along the sheet's own normal."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class GatherResult:
    stack: np.ndarray
    coverage: np.ndarray
    normals: np.ndarray
    offsets_um: np.ndarray
    n_unmapped: int


def _central_diff(a: np.ndarray) -> tuple:
    """d/drow and d/dcol with edge-replicated boundaries."""
    dr = np.zeros_like(a)
    dc = np.zeros_like(a)
    dr[1:-1] = (a[2:] - a[:-2]) * 0.5
    dr[0] = a[1] - a[0]
    dr[-1] = a[-1] - a[-2]
    dc[:, 1:-1] = (a[:, 2:] - a[:, :-2]) * 0.5
    dc[:, 0] = a[:, 1] - a[:, 0]
    dc[:, -1] = a[:, -1] - a[:, -2]
    return dr, dc


def surface_normals(zmap: np.ndarray, ymap: np.ndarray, xmap: np.ndarray) -> np.ndarray:
    """Unit normals in (z, y, x) from the cross product of the two surface tangents."""
    tz_r, tz_c = _central_diff(zmap.astype(np.float64))
    ty_r, ty_c = _central_diff(ymap.astype(np.float64))
    tx_r, tx_c = _central_diff(xmap.astype(np.float64))
    tr = np.stack([tz_r, ty_r, tx_r], axis=-1)
    tc = np.stack([tz_c, ty_c, tx_c], axis=-1)
    n = np.cross(tr, tc)
    mag = np.linalg.norm(n, axis=-1, keepdims=True)
    out = np.zeros_like(n)
    good = mag[..., 0] > 1e-9
    out[good] = n[good] / mag[good]
    return out


def _trilinear(vol: np.ndarray, z: np.ndarray, y: np.ndarray, x: np.ndarray) -> tuple:
    """Sample `vol` at fractional (z, y, x)."""
    D, H, W = vol.shape
    z0 = np.floor(z).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    x0 = np.floor(x).astype(np.int64)
    inside = ((z0 >= 0) & (z0 + 1 < D) & (y0 >= 0) & (y0 + 1 < H)
              & (x0 >= 0) & (x0 + 1 < W))
    zc = np.clip(z0, 0, D - 2)
    yc = np.clip(y0, 0, H - 2)
    xc = np.clip(x0, 0, W - 2)
    dz = (z - zc)[..., None, None, None] if False else (z - zc)
    dy = y - yc
    dx = x - xc
    v = np.zeros(z.shape, dtype=np.float64)
    for wz, iz in ((1.0 - dz, 0), (dz, 1)):
        for wy, iy in ((1.0 - dy, 0), (dy, 1)):
            for wx, ix in ((1.0 - dx, 0), (dx, 1)):
                v += wz * wy * wx * vol[zc + iz, yc + iy, xc + ix].astype(np.float64)
    return v, inside


def gather_along_normals(volume: np.ndarray, zmap, ymap, xmap, *,
                         pitch_um: float, half_thickness_um: float, n_planes: int,
                         nearest: bool = False, validity=None,
                         tifxyz_rule: bool = False) -> GatherResult:
    """Sample `volume` at n_planes physical offsets along each point's surface normal."""
    if n_planes < 1:
        raise ValueError("n_planes must be positive")
    if pitch_um <= 0:
        raise ValueError("pitch_um must be positive; depth in microns needs a real pitch")
    zm = np.asarray(zmap, dtype=np.float64)
    ym = np.asarray(ymap, dtype=np.float64)
    xm = np.asarray(xmap, dtype=np.float64)
    if not (zm.shape == ym.shape == xm.shape):
        raise ValueError("the three TIFXYZ components must have the same shape")
    if volume.ndim != 3:
        raise ValueError("volume must be 3D (z, y, x)")

    if validity is not None:
        mapped = np.asarray(validity).astype(bool)
        if mapped.shape != zm.shape:
            raise ValueError("validity mask shape does not match the TIFXYZ components")
    elif tifxyz_rule:
        mapped = np.isfinite(zm) & np.isfinite(ym) & np.isfinite(xm) & (zm > 0)
    else:
        raise ValueError(
            "no validity given: pass `validity=` (an explicit mask, e.g. from mask.tif or "
            "argus.core.normal_orientation.validity_from_tifxyz) or `tifxyz_rule=True` to "
            "apply the official Z>0 rule. Refusing to infer mapped-ness from non-zero "
            "coordinates -- that convention is not the format's and silently drops a "
            "legitimate (0,0,0) vertex while accepting the (-1,-1,-1) sentinel as valid.")
    normals = surface_normals(zm, ym, xm)
    has_normal = np.linalg.norm(normals, axis=-1) > 0

    offsets_um = (np.zeros(1) if n_planes == 1
                  else np.linspace(-half_thickness_um, half_thickness_um, n_planes))
    steps = offsets_um / pitch_um

    H, W = zm.shape
    stack = np.zeros((n_planes, H, W), dtype=np.float32)
    inside_all = np.ones((H, W), dtype=bool)
    for i, s in enumerate(steps):
        pz = zm + normals[..., 0] * s
        py = ym + normals[..., 1] * s
        px = xm + normals[..., 2] * s
        if nearest:
            iz = np.rint(pz).astype(np.int64)
            iy = np.rint(py).astype(np.int64)
            ix = np.rint(px).astype(np.int64)
            D, HH, WW = volume.shape
            ins = ((iz >= 0) & (iz < D) & (iy >= 0) & (iy < HH) & (ix >= 0) & (ix < WW))
            vals = np.zeros((H, W), dtype=np.float64)
            vals[ins] = volume[iz[ins], iy[ins], ix[ins]]
        else:
            vals, ins = _trilinear(volume, pz, py, px)
        stack[i] = vals.astype(np.float32)
        inside_all &= ins

    coverage = mapped & has_normal & inside_all
    stack[:, ~coverage] = 0.0
    return GatherResult(stack=stack, coverage=coverage, normals=normals,
                        offsets_um=offsets_um, n_unmapped=int((~coverage).sum()))
