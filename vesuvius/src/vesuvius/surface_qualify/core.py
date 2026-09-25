"""Safe qualification and conservative clean-chart extraction for a tifxyz grid.

This module complements the report-only census in ScrollPrize/villa PR #1884
(``vc_tifxyz_topology``: islands / holes / tears / folds).  It does NOT
re-implement those classes.  It adds only:

* a per-vertex angle-defect map,
* connected crease-network extraction (|defect| >= 5 deg, or a triangle edge
  outside [0.6, 1.6] x the nominal grid spacing, then dilated by 2 triangle hops),
* a hole rule that also tests the bridge between a hole and the mesh exterior,
* conservative clean-chart extraction with an explicit REFUSAL when unsafe.

It never repairs anything and never modifies its input: the coordinate array
is wrapped in a read-only view before any computation.

The mask rule, the exterior-bridge hole rule (kappa = 3.0) and the chart rule
are adapted from the ARGUS experiments (cleanchart11.py, gate7.py, cleancore.py);
their parameters were fixed before this package existed and are not tuned here.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

import numpy as np
from scipy import ndimage as ndi
from scipy import sparse
from scipy.sparse.csgraph import connected_components

SCHEMA = "vesuvius.surface_qualify/1"

DEFAULTS: dict[str, Any] = {
    "defect_deg": 5.0,  # |angle defect| at an interior vertex
    "edge_lo": 0.6,  # triangle edge / nominal spacing, lower bound
    "edge_hi": 1.6,  # admits the 1.41 x diagonal of a uniform grid
    "dilate_hops": 2,  # triangle hops added around every flagged triangle
    "jump_factor": 4.0,  # a cell with an edge > jump_factor x nominal is a torn cell, never triangulated
    "kappa": 3.0,  # hole width / hole separation / exterior bridge, in grid cells
    "min_chart_area_vox2": 250000.0,  # smaller clean components are fragments, never extracted
    "piece_min_area_fraction": 0.25,  # reporting rule only
    "piece_min_largest_vox2": 1.0e6,  # reporting rule only
    "nominal_tolerance": (0.8, 1.25),  # median grid step / nominal outside this: the edge rule does not apply, REFUSE
    "diagonal": "shorter",  # "shorter" (ARGUS) or "villa" (fixed p01-p10, as vc_tifxyz2obj / vc_flatten)
    "max_sites": 50,
}

# classes of the cell image (higher = more prominent when an overlay is downsampled)
CLASS_NAMES = {
    0: "no_quad",
    1: "island_not_largest_component",
    2: "clean_fragment_below_min_area",
    3: "clean_chart_refused",
    4: "clean_chart_extractable",
    5: "crease_excluded",
    6: "torn_cell",
    7: "hole_not_gated",
    8: "hole_gated",
}


# --------------------------------------------------------------------------- grid -> mesh
def valid_mask(P: np.ndarray) -> np.ndarray:
    """Villa loader rule: finite and z > 0 (z <= 0 is the invalid marker, which also covers -1,-1,-1)."""
    return np.isfinite(P).all(axis=-1) & (P[..., 2] > 0)


def build_mesh(P: np.ndarray, ok: np.ndarray, L0: float, jump_factor: float, diagonal: str) -> dict[str, Any]:
    """Triangulate every valid quad (two triangles).  Quads with an edge > jump_factor x L0 are torn cells and are not triangulated."""
    H, W = ok.shape
    idx = np.arange(H * W, dtype=np.int64).reshape(H, W)
    Pf = P.reshape(-1, 3)
    cok = ok[:-1, :-1] & ok[1:, :-1] & ok[:-1, 1:] & ok[1:, 1:]
    ci, cj = np.nonzero(cok)  # row-major: deterministic
    a, b, c, d = idx[ci, cj], idx[ci + 1, cj], idx[ci, cj + 1], idx[ci + 1, cj + 1]

    def dist(p, q):
        return np.linalg.norm(Pf[p] - Pf[q], axis=1)

    eab, eac, ebd, ecd, ead, ebc = dist(a, b), dist(a, c), dist(b, d), dist(c, d), dist(a, d), dist(b, c)
    use_ad = (ead <= ebc) if diagonal == "shorter" else np.zeros(len(a), bool)
    if len(a):
        emax = np.max([eab, eac, ebd, ecd, np.where(use_ad, ead, ebc)], axis=0)
    else:
        emax = np.zeros(0)
    torn = emax > jump_factor * L0
    keep = ~torn
    t1 = np.where(use_ad[:, None], np.stack([a, d, b], 1), np.stack([a, c, b], 1))
    t2 = np.where(use_ad[:, None], np.stack([a, c, d], 1), np.stack([b, c, d], 1))
    cell = ci * (W - 1) + cj
    tris = np.concatenate([t1[keep], t2[keep]])
    tri_cell = np.concatenate([cell[keep], cell[keep]])
    step = np.concatenate([eab, eac])
    return dict(H=H, W=W, Pf=Pf, tris=tris.reshape(-1, 3), tri_cell=tri_cell, torn_cells=cell[torn], n_valid_quads=int(cok.sum()), grid_steps=step)


def topology(tris: np.ndarray, nv: int) -> tuple[sparse.csr_matrix, np.ndarray]:
    """Triangle edge-adjacency (CSR) and the boolean per-vertex 'on a boundary edge' flag."""
    M = len(tris)
    bnd = np.zeros(nv, bool)
    if M == 0:
        return sparse.csr_matrix((0, 0)), bnd
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e = np.sort(e, axis=1)
    tid = np.tile(np.arange(M, dtype=np.int64), 3)
    key = e[:, 0].astype(np.int64) * nv + e[:, 1]
    order = np.argsort(key, kind="stable")
    ks, ts, es = key[order], tid[order], e[order]
    starts = np.r_[0, np.nonzero(ks[1:] != ks[:-1])[0] + 1]
    counts = np.diff(np.r_[starts, len(ks)])
    for ev in es[starts[counts != 2]]:  # boundary (1) or non-manifold (>2) edge
        bnd[ev] = True
    two = starts[counts == 2]
    ra, rb = ts[two], ts[two + 1]
    Adj = sparse.coo_matrix((np.ones(2 * len(ra)), (np.r_[ra, rb], np.r_[rb, ra])), shape=(M, M)).tocsr()
    return Adj, bnd


def angle_defects_deg(Pf: np.ndarray, tris: np.ndarray, nv: int) -> np.ndarray:
    """2*pi minus the sum of incident triangle angles, per vertex, degrees; NaN where no triangle touches.  Meaningful at interior vertices only."""
    s = np.zeros(nv)
    for k in range(3):
        a, b, c = tris[:, k], tris[:, (k + 1) % 3], tris[:, (k + 2) % 3]
        u, v = Pf[b] - Pf[a], Pf[c] - Pf[a]
        den = np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1), 1e-300)
        th = np.arccos(np.clip((u * v).sum(1) / den, -1.0, 1.0))
        s += np.bincount(a, weights=th, minlength=nv)
    d = np.degrees(2 * np.pi - s)
    used = np.zeros(nv, bool)
    used[tris.ravel()] = True
    d[~used] = np.nan
    return d


def tri_areas(Pf: np.ndarray, tris: np.ndarray) -> np.ndarray:
    a, b, c = Pf[tris[:, 0]], Pf[tris[:, 1]], Pf[tris[:, 2]]
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def _components(Adj: sparse.csr_matrix, sel: np.ndarray, area: np.ndarray) -> list[np.ndarray]:
    """Connected components of the selected triangles, ordered by (-area, smallest triangle id)."""
    idx = np.nonzero(sel)[0]
    if not len(idx):
        return []
    n, lab = connected_components(Adj[idx][:, idx], directed=False)
    order = np.argsort(lab, kind="stable")
    bounds = np.searchsorted(lab[order], np.arange(n + 1))
    comps = [idx[order[bounds[k] : bounds[k + 1]]] for k in range(n)]
    comps.sort(key=lambda t: (-float(area[t].sum()), int(t[0])))
    return comps


def _sha_ints(a: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(a, np.int64).tobytes()).hexdigest()


def _bbox(cells: np.ndarray, W: int) -> list[int]:
    ci, cj = cells // (W - 1), cells % (W - 1)
    return [int(ci.min()), int(cj.min()), int(ci.max()) + 1, int(cj.max()) + 1]  # (row0, col0, row1, col1) in vertex indices


# --------------------------------------------------------------------------- crease mask
def crease_mask(Pf, tris, Adj, bnd, L0, p) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """bad = touches an interior vertex with |defect| >= defect_deg, or has an edge outside [lo, hi] x L0; then dilated by dilate_hops triangle hops.

    Returns (bad, core, defect_deg_per_vertex, counts)."""
    nv = len(Pf)
    dfc = angle_defects_deg(Pf, tris, nv)
    e = np.stack([np.linalg.norm(Pf[tris[:, 1]] - Pf[tris[:, 0]], axis=1), np.linalg.norm(Pf[tris[:, 2]] - Pf[tris[:, 1]], axis=1), np.linalg.norm(Pf[tris[:, 0]] - Pf[tris[:, 2]], axis=1)], 1) / L0
    hot = (~bnd) & (np.abs(np.nan_to_num(dfc)) >= p["defect_deg"])
    r_def = hot[tris].any(1)
    r_edge = (e.min(1) < p["edge_lo"]) | (e.max(1) > p["edge_hi"])
    core = r_def | r_edge
    b = core.astype(float)
    for _ in range(int(p["dilate_hops"])):
        b = ((Adj @ b + b) > 0).astype(float)
    bad = b > 0
    return bad, core, dfc, dict(defect_triangles=int(r_def.sum()), edge_triangles=int(r_edge.sum()), before_dilation=int(core.sum()), after_dilation=int(bad.sum()))


# --------------------------------------------------------------------------- hole rule with exterior bridge
def hole_census(ok: np.ndarray, kappa: float, max_sites: int) -> tuple[dict, np.ndarray, np.ndarray]:
    """Enclosed runs of invalid vertices (8-connected), each measured for (1) width, (2) separation from every other hole and (3) bridge to the mesh exterior,
    all in grid cells.  A hole is GATED (must be preserved one-to-one by any re-export) only if all three are >= kappa; the rest are reported, never gated.

    The exterior is the invalid region connected to a one-cell invalid ring around the grid, so a sheet that runs to the grid edge is bounded too.
    Returns (summary, hole_label_grid (H,W) int32 [0 = not a hole], gated_grid (H,W) bool)."""
    H, W = ok.shape
    pad = np.pad(~ok, 1, constant_values=True)
    lab, _n = ndi.label(pad, structure=np.ones((3, 3), int))
    ring = int(lab[0, 0])
    ext = lab == ring
    ids = [int(k) for k in np.unique(lab) if k > 0 and k != ring]
    dte = ndi.distance_transform_edt(~ext)
    objs = ndi.find_objects(lab)
    R = int(np.ceil(kappa)) + 3
    recs = []
    for k in ids:
        sl0 = objs[k - 1]
        sl = tuple(slice(max(0, s.start - R), s.stop + R) for s in sl0)
        sub = lab[sl]
        own = sub == k
        width = float(2 * ndi.distance_transform_edt(np.pad(lab[sl0] == k, 1)).max())
        gap_ext = float(dte[sl][own].min())
        oth = (sub > 0) & (sub != k) & (sub != ring)
        gap_hole = float(ndi.distance_transform_edt(~own)[oth].min()) if oth.any() else None  # None: no other hole within the window
        elig = width >= kappa and (gap_hole is None or gap_hole >= kappa) and gap_ext >= kappa
        recs.append(dict(id=k, cells=int((lab[sl0] == k).sum()), width_cells=round(width, 4), gap_hole_cells=None if gap_hole is None else round(gap_hole, 4), gap_ext_cells=round(gap_ext, 4), gated=bool(elig),
                         grid_bbox=[int(sl0[0].start - 1), int(sl0[1].start - 1), int(sl0[0].stop - 1), int(sl0[1].stop - 1)]))
    recs.sort(key=lambda r: (-r["cells"], r["grid_bbox"][0], r["grid_bbox"][1]))
    grid = np.zeros((H, W), np.int32)
    gated = np.zeros((H, W), bool)
    inner = lab[1:-1, 1:-1]
    for i, r in enumerate(recs):
        m = inner == r["id"]
        grid[m] = i + 1
        gated |= m & r["gated"]
        r["id"] = i + 1
    summary = dict(count=len(recs), cells=int(sum(r["cells"] for r in recs)), gated=int(sum(r["gated"] for r in recs)), not_gated=int(sum(not r["gated"] for r in recs)), kappa_cells=kappa,
                   rule="gated iff width, hole-to-hole separation and hole-to-exterior bridge are each >= kappa grid cells; the rest are reported, never gated", sites=recs[:max_sites], sites_truncated=len(recs) > max_sites)
    return summary, grid, gated


# --------------------------------------------------------------------------- the analysis
def analyze(P: np.ndarray, *, nominal: float | None = None, params: Mapping[str, Any] | None = None, census: Mapping[str, Any] | None = None, name: str = "surface") -> tuple[dict, dict]:
    """Qualify one grid.  P: (H, W, 3) coordinates, invalid where z <= 0 or non-finite.  nominal: nominal grid spacing in voxels (1 / meta.json scale);
    if None the median grid step is used and the edge rule is therefore self-referential (reported).  census: the #1884 entry for this surface, if any.

    Returns (report, arrays).  The input is never written to (it is wrapped in a read-only view)."""
    p = dict(DEFAULTS)
    p.update(params or {})
    Pv = np.asarray(P, dtype=np.float64).view()
    Pv.flags.writeable = False
    if Pv.ndim != 3 or Pv.shape[2] != 3 or min(Pv.shape[:2]) < 2:
        raise ValueError("P must have shape (H, W, 3) with H, W >= 2")
    H, W = Pv.shape[:2]
    ok = valid_mask(Pv)
    h = hashlib.sha256()
    h.update(np.asarray(Pv.shape, np.int64).tobytes())
    h.update(np.ascontiguousarray(np.where(ok[..., None], Pv, 0.0)).tobytes())
    h.update(np.packbits(ok).tobytes())
    rep: dict[str, Any] = dict(
        schema=SCHEMA, tool="vesuvius.surface_qualify", source_surface_modified=False,
        claim="safe qualification and extraction of clean regions; NOT automatic topology repair and NOT a topology census (see ScrollPrize/villa PR #1884 for the census)",
        surface=dict(name=name, grid_rows=H, grid_cols=W, valid_vertices=int(ok.sum()), input_array_sha256=h.hexdigest(), validity_rule="finite and z > 0"),
        parameters={k: (list(v) if isinstance(v, tuple) else v) for k, v in p.items()},
    )
    arrays: dict[str, np.ndarray] = {}
    refusals: list[str] = []

    # -- hole rule (needs only the validity grid)
    hsum, hgrid, hgated = hole_census(ok, float(p["kappa"]), int(p["max_sites"]))
    rep["holes"] = dict(hsum, label="calibrated exterior-bridge hole rule (kappa from ARGUS EXP7; grid-native adaptation, PENDING validation on real data)")
    ctx = (hgrid, hgated, H, W)

    # -- nominal spacing
    m0 = build_mesh(Pv, ok, 1.0, 1e300, p["diagonal"])  # untorn mesh, only to measure the grid step
    med = float(np.median(m0["grid_steps"])) if len(m0["grid_steps"]) else None
    L0, src = (float(nominal), "argument (meta.json scale)") if nominal else (med, "median grid step (self-referential)")
    rep["surface"]["valid_quads"] = m0["n_valid_quads"]
    if census is not None:
        rep["census_1884"] = _census_block(census, m0["n_valid_quads"], hsum["count"])
    if not m0["n_valid_quads"] or not L0:
        refusals.append("no_valid_quads")
        return _finish(rep, arrays, refusals, ctx)
    ratio = med / L0
    lo, hi = p["nominal_tolerance"]
    rep["nominal"] = dict(spacing_vox=round(L0, 6), source=src, median_grid_step_over_nominal=round(ratio, 6), tolerance=[lo, hi])
    if not (lo <= ratio <= hi):
        refusals.append("nominal_spacing_mismatch")  # the edge rule assumes a near-uniform grid; it would mislabel a legitimately non-uniform mesh
        return _finish(rep, arrays, refusals, ctx)

    # -- mesh and internal component selection (defines 'the surface'; it is not the #1884 island census)
    mesh = build_mesh(Pv, ok, L0, float(p["jump_factor"]), p["diagonal"])
    tris0, cell0 = mesh["tris"], mesh["tri_cell"]
    nv = H * W
    Adj0, _ = topology(tris0, nv)
    area0 = tri_areas(mesh["Pf"], tris0)
    comps0 = _components(Adj0, np.ones(len(tris0), bool), area0)
    if not comps0:
        refusals.append("no_triangles_after_torn_cell_removal")
        return _finish(rep, arrays, refusals, ctx)
    big = comps0[0]
    rep["internal_component_selection"] = dict(
        note="computed internally only to define the surface (largest edge-connected triangle component, as vc_flatten does); islands, tears and folds are censused by PR #1884, not here",
        components=len(comps0), kept_triangles=int(len(big)), dropped_triangles_not_largest=int(len(tris0) - len(big)), torn_cells_dropped=int(len(mesh["torn_cells"])))
    sel = np.zeros(len(tris0), bool)
    sel[big] = True
    tris, tri_cell, area = tris0[sel], cell0[sel], area0[sel]
    Adj, bnd = topology(tris, nv)
    Pf = mesh["Pf"]
    total = float(area.sum())

    # -- angle defect and crease mask
    bad, core, dfc, cnt = crease_mask(Pf, tris, Adj, bnd, L0, p)
    interior = (~bnd) & np.isfinite(dfc)
    d = np.abs(dfc[interior])
    rep["angle_defect"] = dict(
        interior_vertices=int(interior.sum()), abs_ge_thr=int((d >= p["defect_deg"]).sum()), share_abs_ge_thr=round(float((d >= p["defect_deg"]).mean()), 6) if d.size else None,
        p50_abs_deg=round(float(np.percentile(d, 50)), 6) if d.size else None, p99_abs_deg=round(float(np.percentile(d, 99)), 6) if d.size else None, max_abs_deg=round(float(d.max()), 6) if d.size else None,
        map="per-vertex float32 grid; written by --save-arrays")
    arrays["angle_defect_deg"] = np.where(interior, dfc, np.nan).reshape(H, W).astype(np.float32)

    nets = _components(Adj, bad, area)
    rep["crease"] = dict(
        rule="triangle touches an interior vertex with |defect| >= defect_deg, or has an edge outside [edge_lo, edge_hi] x nominal; dilated by dilate_hops triangle hops; a network is a connected set of such triangles",
        counts=cnt, excluded_area_fraction=round(float(area[bad].sum() / total), 6), networks=len(nets),
        network_sites=[dict(id=i + 1, triangles=int(len(t)), area_vox2=round(float(area[t].sum()), 3), area_fraction=round(float(area[t].sum() / total), 6), grid_bbox=_bbox(tri_cell[t], W), core_triangles=int(core[t].sum())) for i, t in enumerate(nets[: p["max_sites"]])],
        networks_truncated=len(nets) > p["max_sites"], excluded_cells_sha256=_sha_ints(np.unique(tri_cell[bad])))

    # -- clean components -> charts
    comps = _components(Adj, ~bad, area)
    lab = -np.ones(len(tris), np.int64)
    charts: list[dict[str, Any]] = []
    frag_area, frag_n = 0.0, 0
    for t in comps:
        a = float(area[t].sum())
        if a < p["min_chart_area_vox2"]:
            frag_area += a
            frag_n += 1
            continue
        lab[t] = len(charts)
        cells = np.unique(tri_cell[t])
        charts.append(dict(triangles=int(len(t)), area_vox2=round(a, 3), area_fraction=round(a / total, 6), grid_bbox=_bbox(cells, W), cells_sha256=_sha_ints(cells), reasons=[], _t=t))
    und = undeclared_boundary_pairs(Adj, bad, lab)
    dil = ndi.maximum_filter(hgrid, size=3)
    rank, extractable_area, largest = 0, 0.0, 0.0
    cclass = np.zeros((H - 1) * (W - 1), np.uint8)
    cclass[mesh["torn_cells"]] = 6
    cclass[cell0] = 1
    cclass[tri_cell[~bad]] = 2
    cclass[tri_cell[bad]] = 5
    cell_lab = np.zeros((H - 1) * (W - 1), np.uint16)
    for k, c in enumerate(charts):
        t = c.pop("_t")
        c["undeclared_boundary_edges"] = int(und.get(k, 0))
        if c["undeclared_boundary_edges"]:
            c["reasons"].append("undeclared_boundary")
        hl = np.unique(dil.ravel()[np.unique(tris[t])])
        hl = hl[hl > 0]
        c["holes_adjacent"] = int(len(hl))
        c["gated_holes_adjacent"] = int(sum(bool(hgated[hgrid == x].any()) for x in hl))
        c["status"] = "EXTRACTABLE" if not c["reasons"] else "REFUSED"
        both = np.nonzero(np.bincount(tri_cell[t], minlength=(H - 1) * (W - 1)) == 2)[0]  # a half-clean cell is never labelled
        cclass[both] = 4 if c["status"] == "EXTRACTABLE" else 3
        if c["status"] == "EXTRACTABLE":
            rank += 1
            c["chart_label"] = rank
            cell_lab[both] = min(rank, 65535)
            extractable_area += c["area_vox2"]
            largest = max(largest, c["area_vox2"])
    cclass = cclass.reshape(H - 1, W - 1)
    hv = (hgrid > 0)
    ring = hv[:-1, :-1] | hv[1:, :-1] | hv[:-1, 1:] | hv[1:, 1:]
    gring = hgated[:-1, :-1] | hgated[1:, :-1] | hgated[:-1, 1:] | hgated[1:, 1:]
    cclass[ring & (cclass == 0)] = 7
    cclass[gring & (cclass == 7)] = 8
    rep["extraction"] = dict(
        policy="a clean component >= min_chart_area_vox2 is a chart; a chart is EXTRACTABLE only if every chart-boundary edge is a mesh boundary or borders an excluded triangle; nothing is repaired, filled or flattened here; excluded regions are NOT covered by any chart",
        total_area_vox2=round(total, 3), charts=charts, chart_count=len(charts), extractable_charts=rank, fragments=frag_n, fragment_area_fraction=round(frag_area / total, 6),
        extractable_area_fraction=round(extractable_area / total, 6), largest_extractable_area_vox2=round(largest, 3),
        piece_rule_pass=bool(extractable_area / total >= p["piece_min_area_fraction"] and largest >= p["piece_min_largest_vox2"]),
        piece_rule="reporting only: extractable area >= piece_min_area_fraction of the surface AND largest extractable chart >= piece_min_largest_vox2",
        flattening="not performed here; chart distortion (SLIM isometry / area error / flips) is measured by the benchmark harness, not asserted by this report")
    if rank == 0:
        refusals.append("no_extractable_chart")
    arrays["cell_class"] = cclass
    arrays["chart_labels"] = cell_lab.reshape(H - 1, W - 1)
    return _finish(rep, arrays, refusals, ctx)


def undeclared_boundary_pairs(Adj: sparse.csr_matrix, bad: np.ndarray, lab: np.ndarray) -> dict[int, int]:
    """Per chart, count adjacent triangle pairs whose neighbour is CLEAN but in a different (or no) chart: a boundary no exclusion declares.  Must be zero."""
    coo = Adj.tocoo()
    r, c = coo.row, coo.col
    m = (lab[r] >= 0) & (lab[r] != lab[c]) & (~bad[c])
    out: dict[int, int] = {}
    for k in lab[r][m]:
        out[int(k)] = out.get(int(k), 0) + 1
    return out


def _census_block(census: Mapping[str, Any], my_valid_quads: int, my_holes: int) -> dict[str, Any]:
    """Consume the #1884 report entry for this surface (field names verified against the PR) and cross-check the two numbers both tools compute under the same validity rule."""

    def g(*keys):
        d: Any = census
        for k in keys:
            if not isinstance(d, Mapping) or k not in d:
                return None
            d = d[k]
        return d

    blk = dict(source="vc_tifxyz_topology (PR #1884) JSON entry, consumed not recomputed", valid_quads=g("valid_quads"), isolated_vertices=g("isolated_vertices"), island_components=g("islands", "components"), island_quads=g("islands", "island_quads"),
               hole_count=g("holes", "count"), tear_edges=g("tears", "edges"), fold_quads=g("folds", "quads"))
    blk["cross_check"] = dict(valid_quads_agree=(None if blk["valid_quads"] is None else int(blk["valid_quads"]) == my_valid_quads), hole_count_agree=(None if blk["hole_count"] is None else int(blk["hole_count"]) == my_holes),
                              note="informational; a mismatch is reported, never silently reconciled")
    return blk


def _finish(rep, arrays, refusals, ctx):
    hgrid, hgated, H, W = ctx
    if "extraction" not in rep:
        rep["extraction"] = dict(charts=[], chart_count=0, extractable_charts=0, extractable_area_fraction=0.0, piece_rule_pass=False)
    rep["status"] = "REFUSED" if refusals else "EXTRACTABLE"
    rep["refusal_reasons"] = sorted(set(refusals))
    if "cell_class" not in arrays:
        hv = hgrid > 0
        ring = hv[:-1, :-1] | hv[1:, :-1] | hv[:-1, 1:] | hv[1:, 1:]
        cc = np.zeros((H - 1, W - 1), np.uint8)
        cc[ring] = 7
        cc[ring & (hgated[:-1, :-1] | hgated[1:, :-1] | hgated[:-1, 1:] | hgated[1:, 1:])] = 8
        arrays["cell_class"] = cc
        arrays["chart_labels"] = np.zeros((H - 1, W - 1), np.uint16)
        arrays.setdefault("angle_defect_deg", np.full((H, W), np.nan, np.float32))
    return rep, arrays
