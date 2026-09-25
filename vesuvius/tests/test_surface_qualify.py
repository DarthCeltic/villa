"""LOGIC-ONLY tests on tiny synthetic arrays.

These check that the rules do what they say (planted defects found, unsafe input refused, output deterministic, input untouched).  They are NOT
evidence that the tool is useful on scroll data; that evidence is the real-data run described in the PR.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from scipy import sparse

from vesuvius.surface_qualify import analyze, canonical_json, render_overlay, with_replay_hash
from vesuvius.surface_qualify import cli
from vesuvius.surface_qualify.core import angle_defects_deg, build_mesh, hole_census, topology, undeclared_boundary_pairs, valid_mask

L = 10.0
SMALL = dict(min_chart_area_vox2=1000.0)  # the real default (250000 vox2) is for real surfaces; these grids are 40 x 40 cells of 100 vox2


def plane(H=40, W=40, z0=100.0):
    i, j = np.mgrid[0:H, 0:W].astype(np.float64)
    return np.stack([j * L, i * L, np.full_like(i, z0)], -1)


def run(P, **kw):
    kw.setdefault("params", SMALL)
    kw.setdefault("nominal", L)
    return analyze(P, **kw)


def cell_class(arrays, i, j):
    return int(arrays["cell_class"][i, j])


# ------------------------------------------------------------------ clean sheets
def test_all_clean_sheet_is_one_extractable_chart():
    rep, arr = run(plane())
    assert rep["status"] == "EXTRACTABLE" and rep["refusal_reasons"] == []
    ex = rep["extraction"]
    assert ex["chart_count"] == 1 and ex["extractable_charts"] == 1 and ex["extractable_area_fraction"] == 1.0
    assert rep["crease"]["networks"] == 0 and rep["crease"]["excluded_area_fraction"] == 0.0
    assert rep["holes"]["count"] == 0
    assert np.nanmax(np.abs(arr["angle_defect_deg"])) < 1e-6


def test_developable_cylinder_has_zero_angle_defect():
    R = 200.0
    i, j = np.mgrid[0:30, 0:40].astype(np.float64)
    th = j * L / R
    P = np.stack([R * np.sin(th), i * L, R * np.cos(th) + 500.0], -1)
    rep, arr = run(P)
    assert np.nanmax(np.abs(arr["angle_defect_deg"])) < 0.5  # chord vs arc, interior only
    assert rep["status"] == "EXTRACTABLE" and rep["extraction"]["extractable_area_fraction"] == 1.0


# ------------------------------------------------------------------ planted crease
def _ridge_of_cones(P, row=20, cols=range(2, 38, 2), h=8.0):
    P = P.copy()
    for c in cols:
        P[row, c, 2] += h
    return P


def test_planted_crease_is_found_and_splits_the_sheet():
    P = _ridge_of_cones(plane(), cols=range(0, 40, 2))
    rep, arr = run(P)
    assert rep["angle_defect"]["abs_ge_thr"] > 0 and np.nanmax(np.abs(arr["angle_defect_deg"])) >= 5.0
    assert rep["crease"]["networks"] == 1  # the cones are joined by the 2-hop dilation into one connected network
    assert 0.1 < rep["crease"]["excluded_area_fraction"] < 0.6
    assert rep["extraction"]["extractable_charts"] == 2  # above and below the crease, each its own chart
    assert cell_class(arr, 20, 20) == 5  # the crease is excluded
    assert cell_class(arr, 2, 20) == 4 and cell_class(arr, 37, 20) == 4
    assert arr["chart_labels"][20, 20] == 0  # excluded cells are never labelled as a chart
    assert {int(x) for x in np.unique(arr["chart_labels"])} == {0, 1, 2}


def test_isolated_cones_far_apart_are_separate_networks():
    P = plane()
    P[8, 8, 2] += 8.0
    P[32, 32, 2] += 8.0
    rep, _ = run(P)
    assert rep["crease"]["networks"] == 2


def test_stretched_cell_is_flagged_by_edge_rule_not_defect():
    P = plane()
    P[20, 20, 0] += 5.0  # in-plane move: edge ratios 0.5 and 1.5, defect 0
    rep, arr = run(P)
    assert abs(arr["angle_defect_deg"][20, 20]) < 1e-6
    assert rep["crease"]["counts"]["edge_triangles"] > 0
    assert cell_class(arr, 19, 19) == 5


def test_folded_quad_is_excluded_from_every_chart():
    P = plane()
    P[20, 20, 0] += 1.5 * L  # vertex pushed across its right-hand neighbours: folded cells
    rep, arr = run(P)
    for i, j in ((19, 19), (19, 20), (20, 19), (20, 20)):
        assert cell_class(arr, i, j) in (5, 6), (i, j)
        assert arr["chart_labels"][i, j] == 0
    assert rep["extraction"]["extractable_area_fraction"] < 1.0


# ------------------------------------------------------------------ hole rule with exterior bridge
def _with_hole(P, r0, r1, c0, c1):
    P = P.copy()
    P[r0 : r1 + 1, c0 : c1 + 1] = -1.0
    return P


def test_hole_far_from_everything_is_gated():
    h = run(_with_hole(plane(), 19, 21, 19, 21))[0]["holes"]
    assert h["count"] == 1 and h["gated"] == 1 and h["sites"][0]["gated"] is True


def test_hole_near_exterior_is_reported_but_never_gated():
    rep, arr = run(_with_hole(plane(), 1, 3, 19, 21))  # one valid row between the hole and the sheet edge (bridge distance 2 < kappa 3)
    h = rep["holes"]
    assert h["count"] == 1 and h["gated"] == 0 and h["not_gated"] == 1
    assert h["sites"][0]["gap_ext_cells"] < 3.0 <= h["sites"][0]["width_cells"]
    assert rep["status"] == "EXTRACTABLE"  # not gated means reported, never a refusal
    assert (arr["cell_class"] == 7).any() and not (arr["cell_class"] == 8).any()


def test_hole_touching_the_grid_edge_is_exterior_not_a_hole():
    assert run(_with_hole(plane(), 0, 3, 10, 14))[0]["holes"]["count"] == 0


def test_two_close_holes_are_not_gated_but_far_ones_are():
    P = _with_hole(_with_hole(plane(), 18, 20, 12, 14), 18, 20, 16, 18)  # separated by 1 valid column (distance 2 < kappa 3)
    h = run(P)[0]["holes"]
    assert h["count"] == 2 and h["gated"] == 0
    P2 = _with_hole(_with_hole(plane(), 18, 20, 8, 10), 18, 20, 28, 30)
    assert run(P2)[0]["holes"]["gated"] == 2


def test_kappa_is_a_parameter_and_changes_the_verdict():
    P = _with_hole(plane(), 19, 21, 19, 21)
    assert run(P, params=dict(SMALL, kappa=3.0))[0]["holes"]["gated"] == 1
    assert run(P, params=dict(SMALL, kappa=6.0))[0]["holes"]["gated"] == 0  # width 4 < 6


# ------------------------------------------------------------------ refusal
def _dense_cones(h):
    P = plane()
    P[2:-2:2, 2:-2:2, 2] += h
    P[3:-2:2, 3:-2:2, 2] -= h
    return P


def test_everywhere_creased_surface_is_refused():
    rep, arr = run(_dense_cones(5.0))  # every interior vertex is a cone point: nothing clean is left
    assert rep["status"] == "REFUSED" and rep["refusal_reasons"] == ["no_extractable_chart"]
    assert rep["extraction"]["extractable_charts"] == 0 and not arr["chart_labels"].any()


def test_violently_non_uniform_surface_is_refused_by_the_nominal_guard():
    rep, _ = run(_dense_cones(8.0))
    assert rep["status"] == "REFUSED" and rep["refusal_reasons"] == ["nominal_spacing_mismatch"]


def test_nominal_spacing_mismatch_is_refused_before_any_mask_is_computed():
    rep, arr = analyze(plane(), nominal=100.0, params=SMALL)  # grid step 10 vs claimed 100
    assert rep["status"] == "REFUSED" and rep["refusal_reasons"] == ["nominal_spacing_mismatch"]
    assert "crease" not in rep and not arr["chart_labels"].any()


def test_component_below_min_area_is_a_fragment_and_refused():
    rep, _ = analyze(plane(), nominal=L, params=dict(min_chart_area_vox2=1e9))
    assert rep["status"] == "REFUSED" and rep["extraction"]["fragments"] == 1 and rep["extraction"]["chart_count"] == 0


def test_empty_grid_is_refused():
    rep, arr = analyze(np.full((20, 20, 3), -1.0), nominal=L)
    assert rep["status"] == "REFUSED" and rep["refusal_reasons"] == ["no_valid_quads"]
    assert arr["cell_class"].shape == (19, 19)


def test_bad_shapes_raise():
    with pytest.raises(ValueError):
        analyze(np.zeros((1, 5, 3)))


def test_undeclared_boundary_detector_catches_a_mislabelled_chart():
    # three triangles in a strip: 0 - 1 - 2, all clean; chart labels split it 0|1,2 => an undeclared boundary between tri 0 and tri 1
    Adj = sparse.csr_matrix(np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]], float))
    bad = np.zeros(3, bool)
    assert undeclared_boundary_pairs(Adj, bad, np.array([0, 1, 1])) == {0: 1, 1: 1}
    assert undeclared_boundary_pairs(Adj, bad, np.array([0, 0, 0])) == {}
    bad[1] = True  # the middle triangle is excluded: the boundary is declared
    assert undeclared_boundary_pairs(Adj, bad, np.array([0, -1, 1])) == {}


# ------------------------------------------------------------------ mesh and census plumbing
def test_tear_is_dropped_as_torn_cells_and_the_smaller_side_is_not_the_surface():
    P = plane(40, 60)
    P[:, 40:, 0] += 5 * L  # a 5-cell gap: every cell across column 39-40 is torn
    rep, arr = run(P)
    sel = rep["internal_component_selection"]
    assert sel["components"] == 2 and sel["torn_cells_dropped"] == 39 and sel["dropped_triangles_not_largest"] > 0
    assert cell_class(arr, 10, 39) == 6 and cell_class(arr, 10, 50) == 1  # torn cell; cell of the non-largest component


def test_census_1884_is_consumed_and_cross_checked_not_recomputed():
    P = _with_hole(plane(), 19, 21, 19, 21)
    ok_census = {"valid_quads": 39 * 39 - 16, "holes": {"count": 1}, "islands": {"components": 1, "island_quads": 0}, "tears": {"edges": 0}, "folds": {"quads": 0}}
    rep, _ = run(P, census=ok_census)
    cc = rep["census_1884"]["cross_check"]
    assert cc["valid_quads_agree"] is True and cc["hole_count_agree"] is True
    bad = dict(ok_census, valid_quads=1)
    rep2, _ = run(P, census=bad)
    assert rep2["census_1884"]["cross_check"]["valid_quads_agree"] is False and rep2["status"] == rep["status"]


def test_villa_diagonal_option_builds_the_fixed_pair():
    P = plane(3, 3)
    ok = valid_mask(P)
    m = build_mesh(P, ok, L, 4.0, "villa")
    assert len(m["tris"]) == 8
    assert angle_defects_deg(m["Pf"], m["tris"], 9)[4] == pytest.approx(0.0, abs=1e-9)
    Adj, bnd = topology(m["tris"], 9)
    assert not bnd[4] and bnd[0]


# ------------------------------------------------------------------ determinism and input safety
def _report_bytes(P, **kw):
    rep, arr = run(P, **kw)
    return canonical_json(with_replay_hash(rep)), arr


def test_output_is_deterministic():
    P = _with_hole(_ridge_of_cones(plane(), cols=range(0, 40, 2)), 5, 7, 5, 7)
    a, arr_a = _report_bytes(P)
    b, arr_b = _report_bytes(P.copy())
    assert a == b and json.loads(a)["replay_sha256"] == json.loads(b)["replay_sha256"]
    for k in arr_a:
        assert np.array_equal(arr_a[k], arr_b[k], equal_nan=True)
    c, _ = _report_bytes(P, params=dict(SMALL, defect_deg=6.0))
    assert json.loads(c)["replay_sha256"] != json.loads(a)["replay_sha256"]  # the hash is bound to the parameters


def test_input_array_is_never_mutated_and_read_only_input_works():
    P = _with_hole(_ridge_of_cones(plane()), 5, 7, 5, 7)
    before = hashlib.sha256(P.tobytes()).hexdigest()
    run(P)
    assert hashlib.sha256(P.tobytes()).hexdigest() == before and P.flags.writeable
    Q = P.copy()
    Q.flags.writeable = False
    rep, _ = run(Q)  # would raise if anything tried to write into it
    assert rep["source_surface_modified"] is False


def test_overlay_png_is_written_and_deterministic(tmp_path):
    rep, arr = run(_ridge_of_cones(plane(), cols=range(0, 40, 2)))
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    render_overlay(arr, a)
    render_overlay(arr, b)
    assert a.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and a.read_bytes() == b.read_bytes()


# ------------------------------------------------------------------ CLI (needs tifffile, which Villa already uses)
def _write_tifxyz(root: Path, P, scale=1.0 / L):
    tifffile = pytest.importorskip("tifffile")
    root.mkdir(parents=True)
    for k, c in enumerate("xyz"):
        tifffile.imwrite(str(root / f"{c}.tif"), P[..., k].astype(np.float32))
    (root / "meta.json").write_text(json.dumps({"scale": [scale, scale], "format": "tifxyz"}), encoding="utf-8")


def _tree_hash(root: Path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir())}


def test_cli_end_to_end_source_untouched_deterministic_and_exit_codes(tmp_path):
    src = tmp_path / "s.tifxyz"
    _write_tifxyz(src, _ridge_of_cones(plane(), cols=range(0, 40, 2)))
    before = _tree_hash(src)
    args = [str(src), "--min-chart-area-vox2", "1000", "--save-arrays"]
    assert cli.main(args + ["-o", str(tmp_path / "o1")]) == 0
    assert cli.main(args + ["-o", str(tmp_path / "o2")]) == 0
    assert _tree_hash(src) == before  # the source surface is byte-identical
    assert _tree_hash(tmp_path / "o1") == _tree_hash(tmp_path / "o2")  # report, arrays and PNG are byte-identical across runs
    rep = json.loads((tmp_path / "o1" / "report.json").read_text())
    assert rep["status"] == "EXTRACTABLE" and rep["extraction"]["extractable_charts"] == 2

    bad = tmp_path / "bad.tifxyz"
    _write_tifxyz(bad, _dense_cones(5.0))
    assert cli.main([str(bad), "--min-chart-area-vox2", "1000", "-o", str(tmp_path / "ob")]) == 3  # refusal is exit 3 and still writes the report
    assert json.loads((tmp_path / "ob" / "report.json").read_text())["status"] == "REFUSED"


def test_cli_refuses_to_write_into_the_source_and_reports_missing_files(tmp_path, capsys):
    src = tmp_path / "s.tifxyz"
    _write_tifxyz(src, plane())
    assert cli.main([str(src), "-o", str(src / "out")]) == 1
    assert not (src / "out").exists()
    assert cli.main([str(tmp_path / "nope.tifxyz"), "-o", str(tmp_path / "o")]) == 1


def test_cli_census_lookup_by_surface_name(tmp_path):
    src = tmp_path / "s.tifxyz"
    _write_tifxyz(src, plane())
    census = tmp_path / "c.json"
    census.write_text(json.dumps({"surfaces": [{"surface": "/elsewhere/s.tifxyz", "valid_quads": 39 * 39, "holes": {"count": 0}}]}))
    assert cli.main([str(src), "--min-chart-area-vox2", "1000", "--census-1884", str(census), "-o", str(tmp_path / "o")]) == 0
    assert json.loads((tmp_path / "o" / "report.json").read_text())["census_1884"]["cross_check"]["valid_quads_agree"] is True
