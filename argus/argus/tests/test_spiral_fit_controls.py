from __future__ import annotations

import numpy as np
import pytest

from argus.core import spiral_fit_controls as SFC


STEP = 20.0


def _flat_sheet_mesh(cx, cy, z0, n=12, step=STEP, noise=0.0, seed=0):
    """A locally flat, well-formed rectangular sheet at a fixed z, at exactly `step` pitch -- the shape an EXPECTED_STEP=20 contract describes for a well-behaved fitted mesh."""
    rng = np.random.default_rng(seed)
    gu, gv = np.meshgrid(np.arange(n, dtype=np.float64), np.arange(n, dtype=np.float64),
                         indexing="ij")
    x = cx + gu * step + (rng.normal(size=gu.shape) * noise if noise else 0.0)
    y = cy + gv * step + (rng.normal(size=gu.shape) * noise if noise else 0.0)
    z = np.full((n, n), float(z0))
    valid = np.ones((n, n), dtype=bool)
    return {"x": x, "y": y, "z": z, "valid": valid}


def _centre_at_origin(z):
    return np.zeros_like(z), np.zeros_like(z)




def test_mesh_summary_reads_exact_grid_pitch():
    m = _flat_sheet_mesh(cx=1000.0, cy=1000.0, z0=100.0)
    s = SFC.mesh_summary(m["x"], m["y"], m["z"], m["valid"], expected_step=STEP)
    assert abs(s["edge_p50"] - STEP) < 1e-9
    assert s["valid_quad_fraction"] == 1.0
    assert s["edge_over_3x_expected"] == 0


def test_mesh_summary_refuses_zero_valid_vertices():
    m = _flat_sheet_mesh(cx=0.0, cy=0.0, z0=0.0)
    m["valid"][:] = False
    with pytest.raises(SFC.SpiralControlRefusal, match="zero valid"):
        SFC.mesh_summary(m["x"], m["y"], m["z"], m["valid"], expected_step=STEP)


def test_mesh_summary_refuses_shape_mismatch():
    m = _flat_sheet_mesh(cx=0.0, cy=0.0, z0=0.0)
    with pytest.raises(SFC.SpiralControlRefusal, match="shapes disagree"):
        SFC.mesh_summary(m["x"][:-1], m["y"], m["z"], m["valid"], expected_step=STEP)


def test_mesh_summary_radius_uses_centre_at():
    m = _flat_sheet_mesh(cx=0.0, cy=0.0, z0=0.0, n=3, step=1.0)
    s = SFC.mesh_summary(m["x"], m["y"], m["z"], m["valid"], expected_step=1.0,
                          centre_at=_centre_at_origin)
    expected_radius_p50 = float(np.percentile(np.hypot(m["y"], m["x"]), 50))
    assert abs(s["radius_p50"] - expected_radius_p50) < 1e-9




def test_geometric_control_passes_a_clean_flat_grid_at_declared_pitch():
    meshes = {"mesh-A": _flat_sheet_mesh(1000.0, 1000.0, 100.0),
              "mesh-B": _flat_sheet_mesh(1100.0, 1100.0, 100.0)}
    result = SFC.geometric_control(meshes, expected_step=STEP)
    assert result["pass"] is True
    assert result["n_fail"] == 0
    for v in result["per_mesh"].values():
        assert v["pass"] is True
        assert v["distortion"]["status"] == "MEASURED"


def test_geometric_control_fails_a_sheared_torn_mesh():
    """A mesh with a huge single-cell jump must fail on edge length even though the rest of the grid is clean."""
    m = _flat_sheet_mesh(1000.0, 1000.0, 100.0)
    m["x"][5, 5] += 500.0
    result = SFC.geometric_control({"torn": m}, expected_step=STEP)
    assert result["pass"] is False
    assert result["per_mesh"]["torn"]["local_geometry_pass"] is False


def test_geometric_control_isometry_is_self_referential_not_penalised_by_uniform_rescale():
    """A mesh whose grid step is uniformly 4x `expected_step` in x is still internally isometric -- every cell agrees with the mesh's OWN bulk step."""
    m = _flat_sheet_mesh(1000.0, 1000.0, 100.0, step=4.0 * STEP)
    result = SFC.geometric_control({"stretched": m}, expected_step=STEP)
    assert result["per_mesh"]["stretched"]["isometry_pass"] is True


def test_geometric_control_isometry_fails_a_locally_torn_mesh():
    """A mesh whose BULK is a clean, regular grid but a sub-block has collapsed to near-identical points (a tearing failure mode) must fail on degenerate cell fraction, even though..."""
    m = _flat_sheet_mesh(1000.0, 1000.0, 100.0, step=STEP, n=16)
    m["x"][6:12, 6:12] = m["x"][6, 6]
    m["y"][6:12, 6:12] = m["y"][6, 6]
    result = SFC.geometric_control({"torn": m}, expected_step=STEP)
    assert result["per_mesh"]["torn"]["isometry_pass"] is False
    assert result["per_mesh"]["torn"]["degenerate_fraction"] > 0.05
    assert result["pass"] is False


def test_geometric_control_refuses_empty_input():
    with pytest.raises(SFC.SpiralControlRefusal, match="no meshes"):
        SFC.geometric_control({}, expected_step=STEP)




def _nested_windings(radii_by_index, n=20, z0=100.0, theta0=0.3, theta_span=0.6):
    """Synthetic nested sheets, one per winding index, in POLAR form: every sheet shares the exact same (z, theta) grid (each sheet spans the full z-window)..."""
    if any(r <= 0 for r in radii_by_index.values()):
        raise ValueError("test fixture requires r > 0 (atan2 is degenerate at the origin)")
    zline = np.linspace(z0, z0 + 100.0, n)
    theta_line = np.linspace(theta0, theta0 + theta_span, n)
    gz, gt = np.meshgrid(zline, theta_line, indexing="ij")
    out = {}
    for idx, r in radii_by_index.items():
        x = r * np.cos(gt)
        y = r * np.sin(gt)
        z = gz.copy()
        valid = np.ones((n, n), dtype=bool)
        out[idx] = {"x": x, "y": y, "z": z, "valid": valid}
    return out


_TEST_BINNING = dict(n_z_bins=3, n_theta_bins=3, min_points_per_bin=3, min_shared_bins=2)


def test_topological_control_passes_clean_nested_non_crossing_windings():
    windings = _nested_windings({6: 50.0, 7: 100.0, 8: 200.0, 9: 300.0})
    result = SFC.topological_control(windings, centre_at=_centre_at_origin, **_TEST_BINNING)
    assert result["topology_clean"] is True
    assert result["connected_pass"] is True
    assert result["winding_order_pass"] is True
    assert result["non_crossing_pass"] is True
    assert result["crossings"] == []


def test_topological_control_detects_a_disconnected_winding_sheet():
    windings = _nested_windings({6: 50.0, 7: 100.0})
    v = windings[6]["valid"]
    v[:] = False
    v[0:3, 0:3] = True
    v[17:20, 17:20] = True
    result = SFC.topological_control(windings, centre_at=_centre_at_origin, **_TEST_BINNING)
    assert result["connectedness"][6]["n_components"] == 2
    assert result["connectedness"][6]["pass"] is False
    assert result["connected_pass"] is False
    assert result["topology_clean"] is False


def test_topological_control_detects_self_intersecting_overlapping_sheets():
    """Sheet 7 is declared to be further out than sheet 6 but is actually built at a SMALLER radius -- so in every shared (z, theta) bin the 'outer' sheet's measured radius is actually below the 'inner'..."""
    windings = _nested_windings({6: 50.0, 7: 49.0})
    result = SFC.topological_control(windings, centre_at=_centre_at_origin, **_TEST_BINNING)
    assert result["non_crossing_pass"] is False
    assert len(result["crossings"]) == 1
    assert result["crossings"][0]["inner"] == 6
    assert result["crossings"][0]["outer"] == 7
    assert result["crossings"][0]["violation_fraction"] == 1.0
    assert result["topology_clean"] is False


def test_topological_control_detects_swapped_winding_order():
    """Index 7 claims to be further out than index 8 but is actually the inner sheet -- a real winding-count/order defect, checked (and here demonstrated) independently of the crossing detector:..."""
    windings = _nested_windings({7: 500.0, 8: 10.0})
    result = SFC.topological_control(windings, centre_at=_centre_at_origin, **_TEST_BINNING)
    assert result["winding_order_pass"] is False
    assert result["topology_clean"] is False


def test_topological_control_reports_insufficient_overlap_rather_than_a_verdict():
    """Two sheets whose (z, theta) footprints never co-occur cannot be judged for crossing at all -- that must be reported explicitly, not silently scored as passing or failing."""
    a = _nested_windings({6: 50.0}, z0=100.0)
    b = _nested_windings({7: 150.0}, z0=100000.0)
    windings = {**a, **b}
    result = SFC.topological_control(windings, centre_at=_centre_at_origin, **_TEST_BINNING)
    assert result["crossings"] == []
    assert len(result["insufficient_overlap_pairs"]) == 1
    assert result["insufficient_overlap_pairs"][0] == {"inner": 6, "outer": 7, "n_shared_bins": 0}
    assert result["non_crossing_pass"] is True


def test_topological_control_refuses_fewer_than_two_windings():
    windings = _nested_windings({6: 50.0}, n=5)
    with pytest.raises(SFC.SpiralControlRefusal, match="at least 2"):
        SFC.topological_control(windings, centre_at=_centre_at_origin)


def test_topological_control_refuses_an_empty_winding():
    windings = _nested_windings({6: 50.0, 7: 100.0}, n=5)
    windings[6]["valid"][:] = False
    with pytest.raises(SFC.SpiralControlRefusal, match="zero valid vertices"):
        SFC.topological_control(windings, centre_at=_centre_at_origin)




def _law_radius(k, a=3.0, b=50.0):
    """A perfectly linear radius(winding) law: radius = a*k + b."""
    return a * k + b


def test_holdout_control_passes_when_the_held_out_region_follows_the_same_law():
    radius_by_winding = {k: _law_radius(k) for k in list(range(6, 10)) + list(range(10, 64))
                         + list(range(64, 130))}
    result = SFC.holdout_control(radius_by_winding, calibration_range=(10, 63), degree=1)
    assert result["pass"] is True
    assert result["median_rel_error"] < 1e-6
    assert set(result["train_windings"]) == set(range(10, 64))
    assert set(result["holdout_windings"]) == set(range(6, 10)) | set(range(64, 130))


def test_holdout_control_fails_when_the_held_out_region_breaks_the_law():
    """The held-out (extrapolation) region follows a DIFFERENT law than the calibration region -- exactly the scenario a real fit could produce (a law that only holds where it was calibrated) and which..."""
    radius_by_winding = {k: _law_radius(k) for k in range(10, 64)}
    radius_by_winding.update({k: _law_radius(k, a=30.0) for k in range(64, 130)})
    result = SFC.holdout_control(radius_by_winding, calibration_range=(10, 63), degree=1)
    assert result["pass"] is False
    assert result["median_rel_error"] > result["median_rel_error_tol"]


def test_holdout_control_refuses_when_calibration_range_is_underpopulated():
    radius_by_winding = {10: 100.0, 11: 103.0, 64: 300.0}
    with pytest.raises(SFC.SpiralControlRefusal, match="calibration range"):
        SFC.holdout_control(radius_by_winding, calibration_range=(10, 63), degree=1)


def test_holdout_control_refuses_when_nothing_is_held_out():
    radius_by_winding = {k: _law_radius(k) for k in range(10, 64)}
    with pytest.raises(SFC.SpiralControlRefusal, match="nothing to hold out"):
        SFC.holdout_control(radius_by_winding, calibration_range=(10, 63), degree=1)




def test_evaluate_spiral_fit_rolls_up_all_three_and_carries_known_blockers():
    geometric = SFC.geometric_control(
        {"mesh-A": _flat_sheet_mesh(1000.0, 1000.0, 100.0)}, expected_step=STEP)
    topological = SFC.topological_control(
        _nested_windings({6: 50.0, 7: 100.0}), centre_at=_centre_at_origin, **_TEST_BINNING)
    holdout = SFC.holdout_control(
        {k: _law_radius(k) for k in list(range(6, 10)) + list(range(10, 64)) + list(range(64, 130))},
        calibration_range=(10, 63), degree=1)
    result = SFC.evaluate_spiral_fit(geometric=geometric, topological=topological,
                                     holdout=holdout,
                                     known_blockers={"any": True, "reason": "fixture blocker"})
    assert result["all_three_controls_pass"] is True
    assert result["ready_for_qualified_fit"] is False
    assert result["known_blockers"]["reason"] == "fixture blocker"


def test_evaluate_spiral_fit_ready_only_when_no_blockers_and_all_controls_pass():
    geometric = SFC.geometric_control(
        {"mesh-A": _flat_sheet_mesh(1000.0, 1000.0, 100.0)}, expected_step=STEP)
    topological = SFC.topological_control(
        _nested_windings({6: 50.0, 7: 100.0}), centre_at=_centre_at_origin, **_TEST_BINNING)
    holdout = SFC.holdout_control(
        {k: _law_radius(k) for k in list(range(6, 10)) + list(range(10, 64)) + list(range(64, 130))},
        calibration_range=(10, 63), degree=1)
    result = SFC.evaluate_spiral_fit(geometric=geometric, topological=topological, holdout=holdout)
    assert result["ready_for_qualified_fit"] is True
