"""A decimated mesh lattice for the UI, with the jumping edges marked."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.adapters.published_mesh import MAX_STEP_RATIO
from argus.core.contracts import Refusal
from argus.core.coordinate_map import INVALID_SENTINEL

MAX_POINTS = 12000


def lattice(mesh_dir: Path, *, max_points: int = MAX_POINTS,
            coord_scale: float = 1.0, stride: int | None = None,
            with_windings: bool = False) -> dict:
    """Read a tifxyz mesh and return points, jumping edges, and the projection used."""
    import tifffile
    d = Path(mesh_dir)
    for f in ("x.tif", "y.tif", "z.tif"):
        if not (d / f).is_file():
            raise Refusal("GEOMETRY", "no tifxyz mesh at %s" % d)
    X = tifffile.imread(d / "x.tif").astype(np.float64) * coord_scale
    Y = tifffile.imread(d / "y.tif").astype(np.float64) * coord_scale
    Z = tifffile.imread(d / "z.tif").astype(np.float64) * coord_scale
    ok = np.isfinite(X) & np.isfinite(Y) & np.isfinite(Z) & (X > 0)
    if not ok.any():
        raise Refusal("GEOMETRY", "the mesh has no valid cells")

    h, w = X.shape
    step = stride if stride else max(1, int(np.ceil(np.sqrt(h * w / max(max_points, 1)))))
    sl = (slice(None, None, step), slice(None, None, step))
    Xs, Ys, Zs, oks = X[sl], Y[sl], Z[sl], ok[sl]

    winding_id_by_ij = None
    if with_windings:
        from argus.core.windings import (
            _components as _wcomponents,
            component_stable_ids,
            grid_jumps,
        )
        from argus.core.normal_orientation import validity_from_tifxyz

        wvalid = validity_from_tifxyz(Zs, Ys, Xs, sentinel=INVALID_SENTINEL, require_finite=True)
        gj = grid_jumps(wvalid, Zs, Ys, Xs)
        if gj is not None:
            wjump_r, wjump_d, _ = gj
            wcomp, wn_comp = _wcomponents(wvalid, wjump_r, wjump_d)
            wids = component_stable_ids(wcomp, wn_comp, Xs.shape[1])
            winding_id_by_ij = np.where(
                wvalid, np.vectorize(lambda c: wids.get(int(c), -1))(wcomp), -1
            )

    P = np.stack([Xs, Ys, Zs], -1)
    spread = np.array([np.ptp(a[oks]) if oks.any() else 0.0 for a in (Xs, Ys, Zs)])
    order = list(np.argsort(spread)[::-1][:2])
    names = ["x", "y", "z"]

    pts, pts3, jumps = [], [], []
    winding_ids = [] if with_windings else None
    hh, ww = Xs.shape
    med = _median_step(P, oks)
    idx = np.full((hh, ww), -1, dtype=np.int64)
    for i in range(hh):
        for j in range(ww):
            if oks[i, j]:
                idx[i, j] = len(pts3)
                pts.append([round(float(P[i, j, order[0]]), 2),
                            round(float(P[i, j, order[1]]), 2)])
                pts3.append([round(float(P[i, j, 0]), 2),
                             round(float(P[i, j, 1]), 2),
                             round(float(P[i, j, 2]), 2)])
                if winding_ids is not None:
                    winding_ids.append(int(winding_id_by_ij[i, j]) if winding_id_by_ij is not None else -1)
    faces = []
    for i in range(hh - 1):
        for j in range(ww - 1):
            a, b, c, d = idx[i, j], idx[i, j + 1], idx[i + 1, j], idx[i + 1, j + 1]
            if a < 0 or b < 0 or c < 0 or d < 0:
                continue
            quad_pts = (P[i, j], P[i, j + 1], P[i + 1, j], P[i + 1, j + 1])
            if med > 0 and _max_pair_ratio(quad_pts, med) > MAX_STEP_RATIO:
                continue
            faces.append([int(a), int(b), int(c)])
            faces.append([int(b), int(d), int(c)])
    for i in range(hh):
        for j in range(ww):
            if not oks[i, j]:
                continue
            for di, dj in ((0, 1), (1, 0)):
                a, b = i + di, j + dj
                if a >= hh or b >= ww or not oks[a, b]:
                    continue
                dist = float(np.linalg.norm(P[a, b] - P[i, j]))
                if med > 0 and dist / med > MAX_STEP_RATIO:
                    jumps.append({"from": [round(float(P[i, j, order[0]]), 2),
                                           round(float(P[i, j, order[1]]), 2)],
                                  "to": [round(float(P[a, b, order[0]]), 2),
                                         round(float(P[a, b, order[1]]), 2)],
                                  "from3": [round(float(P[i, j, 0]), 2),
                                            round(float(P[i, j, 1]), 2),
                                            round(float(P[i, j, 2]), 2)],
                                  "to3": [round(float(P[a, b, 0]), 2),
                                          round(float(P[a, b, 1]), 2),
                                          round(float(P[a, b, 2]), 2)],
                                  "ratio": round(dist / med, 1)})
    return {"schema": "argus-mesh-lattice-v1",
            "mesh_dir": str(d), "grid_shape": [int(h), int(w)],
            "decimation_step": step, "points": pts, "points3": pts3,
            "jump_edges": jumps,
            "faces": faces,
            "winding_ids": winding_ids,
            "projection": {"axes": [names[order[0]], names[order[1]]],
                           "chosen_by": "largest coordinate spread, not by axis name",
                           "default_camera_axes": [int(order[0]), int(order[1])],
                           "note": ("points[] is this 2D projection and is unchanged. "
                                    "points3[] carries the full coordinate so a viewer can "
                                    "turn the sheet instead of accepting one angle.")},
            "median_step_vox": round(med, 3),
            "jump_ratio_limit": MAX_STEP_RATIO,
            "is_not_image_evidence": True,
            "note": ("the mesh's own coordinate lattice, decimated by a stride so seams "
                     "survive; it carries no intensity from the volume and is not a render")}


def _max_pair_ratio(quad_pts, med: float) -> float:
    """The worst edge-length-to-median ratio among a quad's two diagonals -- the same jump test `jumps` applies to grid-adjacent cells, applied here so a triangle is never drawn across a discontinuity..."""
    a, b, c, d = quad_pts
    diag1 = float(np.linalg.norm(d - a))
    diag2 = float(np.linalg.norm(c - b))
    return max(diag1, diag2) / med


def _median_step(P, ok) -> float:
    hh, ww = ok.shape
    ds = []
    for di, dj in ((0, 1), (1, 0)):
        if hh - di < 1 or ww - dj < 1:
            continue
        a = P[di:, dj:]
        b = P[:hh - di if di else hh, :ww - dj if dj else ww]
        m = ok[di:, dj:] & ok[:hh - di if di else hh, :ww - dj if dj else ww]
        if m.any():
            ds.append(np.linalg.norm(a - b, axis=-1)[m])
    return float(np.median(np.concatenate(ds))) if ds else 0.0
