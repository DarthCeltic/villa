"""The Winding Inspector's data: proposed windings from an EXISTING trace, never a new trace."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.adapters.published_mesh import MAX_STEP_RATIO
from argus.core.contracts import Refusal
from argus.core.coordinate_map import INVALID_SENTINEL
from argus.core.normal_orientation import orient_normals, validity_from_tifxyz

MAX_POINTS_PER_WINDING = 900
MAX_WINDINGS_DEFAULT = 12


def _read_tifxyz(d: Path, coord_scale: float):
    import tifffile
    for f in ("x.tif", "y.tif", "z.tif"):
        if not (d / f).is_file():
            raise Refusal("GEOMETRY", "no tifxyz mesh at %s" % d)
    X = tifffile.imread(d / "x.tif").astype(np.float64) * coord_scale
    Y = tifffile.imread(d / "y.tif").astype(np.float64) * coord_scale
    Z = tifffile.imread(d / "z.tif").astype(np.float64) * coord_scale
    return X, Y, Z


def grid_jumps(valid, Z, Y, X):
    """Per-edge jump masks and the median step they are measured against."""
    h, w = valid.shape
    P = np.stack([Z, Y, X], axis=-1)
    dr = np.linalg.norm(P[:, 1:] - P[:, :-1], axis=-1)
    dd = np.linalg.norm(P[1:, :] - P[:-1, :], axis=-1)
    pair_r = valid[:, 1:] & valid[:, :-1]
    pair_d = valid[1:, :] & valid[:-1, :]
    steps = np.concatenate([dr[pair_r].ravel(), dd[pair_d].ravel()])
    if steps.size == 0:
        return None
    median_step = float(np.median(steps))
    limit = median_step * MAX_STEP_RATIO
    jump_r = np.zeros((h, w - 1), bool)
    jump_d = np.zeros((h - 1, w), bool)
    jump_r[pair_r] = dr[pair_r] > limit
    jump_d[pair_d] = dd[pair_d] > limit
    return jump_r, jump_d, median_step


def component_stable_ids(comp, n_comp: int, w: int) -> dict:
    """{component label -> stable id}, the SAME formula `inspect()` uses per winding (lowest grid index)."""
    ids = {}
    for cid in range(n_comp):
        ys, xs = np.nonzero(comp == cid)
        if ys.size == 0:
            continue
        ids[cid] = int(ys.min()) * w + int(xs.min())
    return ids


def _components(valid, jump_r, jump_d):
    """Connected components of the grid graph with jumping edges deleted."""
    h, w = valid.shape
    comp = np.full((h, w), -1, dtype=np.int64)
    cid = 0
    for sy in range(h):
        for sx in range(w):
            if not valid[sy, sx] or comp[sy, sx] != -1:
                continue
            stack = [(sy, sx)]
            comp[sy, sx] = cid
            while stack:
                y, x = stack.pop()
                if x + 1 < w and valid[y, x + 1] and comp[y, x + 1] == -1 and not jump_r[y, x]:
                    comp[y, x + 1] = cid
                    stack.append((y, x + 1))
                if x - 1 >= 0 and valid[y, x - 1] and comp[y, x - 1] == -1 and not jump_r[y, x - 1]:
                    comp[y, x - 1] = cid
                    stack.append((y, x - 1))
                if y + 1 < h and valid[y + 1, x] and comp[y + 1, x] == -1 and not jump_d[y, x]:
                    comp[y + 1, x] = cid
                    stack.append((y + 1, x))
                if y - 1 >= 0 and valid[y - 1, x] and comp[y - 1, x] == -1 and not jump_d[y - 1, x]:
                    comp[y - 1, x] = cid
                    stack.append((y - 1, x))
            cid += 1
    return comp, cid


def inspect(mesh_dir, *, coord_scale: float = 1.0, sentinel=INVALID_SENTINEL,
            max_windings: int = MAX_WINDINGS_DEFAULT, stride: int = 1) -> dict:
    """Proposed windings, their geometry metrics, and every reason to disbelieve them."""
    d = Path(mesh_dir)
    X, Y, Z = _read_tifxyz(d, coord_scale)
    if stride > 1:
        X, Y, Z = X[::stride, ::stride], Y[::stride, ::stride], Z[::stride, ::stride]
    if X.shape != Y.shape or X.shape != Z.shape:
        raise Refusal("GEOMETRY", "tifxyz components differ in shape")
    h, w = X.shape

    valid = validity_from_tifxyz(Z, Y, X, sentinel=sentinel, require_finite=True)
    if not valid.any():
        raise Refusal("GEOMETRY", "the mesh has no valid cells")

    P = np.stack([Z, Y, X], axis=-1)
    gj = grid_jumps(valid, Z, Y, X)
    if gj is None:
        raise Refusal("GEOMETRY", "no adjacent valid cells to measure")
    jump_r, jump_d, median_step = gj

    comp, n_comp = _components(valid, jump_r, jump_d)

    from argus.core.surface_gather import surface_normals
    normals = surface_normals(Z, Y, X)
    orient = orient_normals(normals, valid)

    out = []
    for cid in range(n_comp):
        m = comp == cid
        n_px = int(m.sum())
        if n_px < 4:
            continue
        ys, xs = np.nonzero(m)
        pts = P[m]
        span = pts.max(axis=0) - pts.min(axis=0)
        touch = 0
        if w > 1:
            touch += int((jump_r & (comp[:, :-1] == cid)).sum())
            touch += int((jump_r & (comp[:, 1:] == cid)).sum())
        if h > 1:
            touch += int((jump_d & (comp[:-1, :] == cid)).sum())
            touch += int((jump_d & (comp[1:, :] == cid)).sum())
        contradictory = any(r["component"] for r in orient.rejected_components
                            if np.any(m & (orient.component == r["component"])))
        support = float(valid[m].mean()) if n_px else 0.0
        flags = []
        if touch:
            flags.append("JUMPS")
        if contradictory:
            flags.append("CONTRADICTORY_ORIENTATION")
        if support < 1.0:
            flags.append("MISSING_SUPPORT")

        step = max(1, int(np.ceil(n_px / MAX_POINTS_PER_WINDING)))
        out.append({
            "id": int(ys.min()) * w + int(xs.min()),
            "component": cid,
            "n_points": n_px,
            "grid_bbox": {"y0": int(ys.min()), "y1": int(ys.max()) + 1,
                          "x0": int(xs.min()), "x1": int(xs.max()) + 1},
            "extent_vox": {"z": float(span[0]), "y": float(span[1]), "x": float(span[2])},
            "centroid_vox": {"z": float(pts[:, 0].mean()), "y": float(pts[:, 1].mean()),
                             "x": float(pts[:, 2].mean())},
            "median_step_vox": median_step,
            "jump_edges_touching": touch,
            "support_fraction": support,
            "flags": flags,
            "points_zyx": [[round(float(a), 2), round(float(b), 2), round(float(c), 2)]
                           for a, b, c in pts[::step]],
        })

    out.sort(key=lambda r: -r["n_points"])
    shown = out[:max_windings]

    hsh = hashlib.sha256()
    for r in out:
        hsh.update(("%d:%d:%d" % (r["id"], r["component"], r["n_points"])).encode())

    return {
        "schema": "argus-windings-v1",
        "mesh_dir": str(d),
        "grid_shape": [h, w],
        "stride": stride,
        "stride_note": ("the grid was subsampled by this factor before analysis. Counts and "
                        "edge totals are for the subsampled grid, not the full mesh."
                        if stride > 1 else "full resolution"),
        "median_step_vox": median_step,
        "jump_ratio_limit": MAX_STEP_RATIO,
        "jump_rule_source": "argus.adapters.published_mesh.MAX_STEP_RATIO -- imported, not restated",
        "n_windings_total": len(out),
        "n_windings_returned": len(shown),
        "windings": shown,
        "orientation": {
            "n_components": orient.n_components,
            "rejected_components": orient.rejected_components,
            "discontinuous_edges": orient.discontinuous_edges,
            "conflict_edges": orient.conflict_edges,
            "field_sha256": orient.field_sha256,
        },
        "windings_sha256": hsh.hexdigest(),
        "correspondence": "UNVALIDATED_CORRESPONDENCE",
        "correspondence_note": ("an identity and a colour are STABLE, not physical. Nothing here "
                                "establishes that a winding in this view is the same papyrus as "
                                "a winding in any other view, and matching colours prove "
                                "nothing until the geometry gate passes."),
        "is_not_image_evidence": True,
        "note": ("this is the mesh's own coordinate geometry grouped by the existing jump rule. "
                 "It carries no intensity from the volume and is not a picture of papyrus."),
        "does_not_trace": ("no tracing is performed here. The trace is upstream's tifxyz mesh "
                           "and this only reads and groups it."),
    }
