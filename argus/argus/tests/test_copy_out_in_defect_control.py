"""The classical Copy Out/In propagation-error control is real geometry, not a cosmetic pass/fail."""
from __future__ import annotations

import numpy as np
import pytest

from argus.core import copy_out_in_defect_control as C


def _smooth_grid(n=40, R=80.0, dz=2.0, theta_max=0.6):
    """A developable cylinder patch -- zero Gaussian curvature, genuinely smooth everywhere, matching the fixture style already used for real VC3D output checks in..."""
    theta = np.linspace(0.0, theta_max, n)
    z_ax = np.arange(n, dtype=np.float64) * dz
    th, zax = np.meshgrid(theta, z_ax)
    x = R * np.sin(th)
    y = zax
    z = R * np.cos(th) + R + 10.0
    return x, y, z


def _inject_spike(x, y, z, row, col, magnitude=500.0):
    """A real local defect: one grid cell displaced far off the smooth surface (a fold/spike), exactly the shape of geometry error Copy Out/In's own risk statement describes."""
    x = x.copy(); y = y.copy(); z = z.copy()
    z[row, col] += magnitude
    return x, y, z



def test_validity_mask_flags_nan_and_sentinel_but_not_real_geometry():
    x, y, z = _smooth_grid(n=12)
    x[3, 3] = np.nan
    x[5, 5] = y[5, 5] = z[5, 5] = -1.0
    mask = C.tifxyz_validity_mask(x, y, z)
    assert mask[3, 3] == False
    assert mask[5, 5] == False
    assert mask[0, 0] == True
    assert mask.sum() == mask.size - 2



def test_local_defect_score_is_near_zero_on_smooth_geometry():
    x, y, z = _smooth_grid(n=30)
    score = C.local_defect_score(x, y, z)
    interior = score[2:-2, 2:-2]
    assert np.all(np.isfinite(interior))
    assert np.nanmax(interior) < 5.0


def test_local_defect_score_flags_a_planted_spike():
    x, y, z = _smooth_grid(n=30)
    x2, y2, z2 = _inject_spike(x, y, z, 15, 15, magnitude=200.0)
    score = C.local_defect_score(x2, y2, z2)
    baseline = np.nanmedian(C.local_defect_score(x, y, z)[2:-2, 2:-2])
    assert score[15, 15] > baseline + 50.0


def test_local_defect_score_rejects_mismatched_shapes():
    x, y, z = _smooth_grid(n=10)
    with pytest.raises(ValueError):
        C.local_defect_score(x, y, z[:-1, :])


def test_local_defect_score_nan_for_cells_next_to_invalid_data():
    x, y, z = _smooth_grid(n=12)
    x[6, 6] = np.nan
    score = C.local_defect_score(x, y, z)
    assert np.isnan(score[6, 6])
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        assert np.isnan(score[6 + dr, 6 + dc]), "a neighbour of an invalid cell has no honest score"



def test_defect_mask_flags_rare_outliers_not_the_bulk():
    rng = np.random.default_rng(0)
    score = np.abs(rng.normal(0.0, 0.1, size=(20, 20)))
    score[10, 10] = 50.0
    mask = C.defect_mask_from_score(score, robust_z_threshold=6.0)
    assert mask[10, 10] == True
    assert mask.sum() < 5


def test_defect_mask_all_nan_returns_no_flags():
    score = np.full((5, 5), np.nan)
    mask = C.defect_mask_from_score(score)
    assert not mask.any()



def test_propagation_detected_when_candidate_repeats_the_source_defect():
    """The failure mode Villa's own audit text names: a local error in the source wrap gets carried straight through into the candidate at the same grid location."""
    sx, sy, sz = _smooth_grid(n=30)
    sx, sy, sz = _inject_spike(sx, sy, sz, 12, 12, magnitude=200.0)
    cx, cy, cz = _smooth_grid(n=30, R=82.0)
    cx, cy, cz = _inject_spike(cx, cy, cz, 12, 12, magnitude=200.0)

    report = C.detect_propagated_defects((sx, sy, sz), (cx, cy, cz))
    assert report["verdict"] == "PROPAGATION_DETECTED"
    assert report["propagated_defect_count"] >= 1
    assert [12, 12] in report["propagated_sample_grid_coords"] or any(
        abs(r - 12) <= 1 and abs(c - 12) <= 1 for r, c in report["propagated_sample_grid_coords"])
    assert report["same_shape"] is True
    assert report["schema"] == "argus-copy-out-in-propagation-report-v1"


def test_no_propagation_when_candidate_is_clean():
    sx, sy, sz = _smooth_grid(n=30)
    sx, sy, sz = _inject_spike(sx, sy, sz, 12, 12, magnitude=200.0)
    cx, cy, cz = _smooth_grid(n=30, R=82.0)

    report = C.detect_propagated_defects((sx, sy, sz), (cx, cy, cz))
    assert report["verdict"] == "NO_CANDIDATE_DEFECTS"
    assert report["propagated_defect_count"] == 0
    assert report["source_defect_count"] >= 1


def test_candidate_only_defect_is_distinguished_from_propagation():
    """A NEW anomaly introduced by the copy itself, unrelated to any source defect, must not be reported as \"propagated\" -- that would blur exactly the distinction this control exists to make."""
    sx, sy, sz = _smooth_grid(n=30)
    sx, sy, sz = _inject_spike(sx, sy, sz, 5, 5, magnitude=200.0)
    cx, cy, cz = _smooth_grid(n=30, R=82.0)
    cx, cy, cz = _inject_spike(cx, cy, cz, 24, 24, magnitude=200.0)

    report = C.detect_propagated_defects((sx, sy, sz), (cx, cy, cz), correspondence_radius=1)
    assert report["verdict"] == "CANDIDATE_DEFECTS_NOT_SOURCE_LOCAL"
    assert report["propagated_defect_count"] == 0
    assert report["candidate_only_defect_count"] >= 1
    assert [24, 24] in report["candidate_only_sample_grid_coords"] or any(
        abs(r - 24) <= 1 and abs(c - 24) <= 1
        for r, c in report["candidate_only_sample_grid_coords"])


def test_correspondence_radius_tolerates_a_multi_cell_regrid_shift():
    """A Laplacian defect score naturally raises its immediate 4-neighbours too (a spike pulls their local smoothness estimate up), so a shift of exactly one cell already overlaps without any explicit..."""
    sx, sy, sz = _smooth_grid(n=30)
    sx, sy, sz = _inject_spike(sx, sy, sz, 12, 12, magnitude=200.0)
    cx, cy, cz = _smooth_grid(n=30, R=82.0)
    cx, cy, cz = _inject_spike(cx, cy, cz, 15, 12, magnitude=200.0)

    zero_radius = C.detect_propagated_defects((sx, sy, sz), (cx, cy, cz), correspondence_radius=0)
    wide_radius = C.detect_propagated_defects((sx, sy, sz), (cx, cy, cz), correspondence_radius=2)
    assert zero_radius["propagated_defect_count"] == 0
    assert wide_radius["propagated_defect_count"] >= 1
    assert wide_radius["verdict"] == "PROPAGATION_DETECTED"


def test_mismatched_shapes_compare_only_the_overlap_and_say_so():
    sx, sy, sz = _smooth_grid(n=30)
    sx, sy, sz = _inject_spike(sx, sy, sz, 5, 5, magnitude=200.0)
    cx, cy, cz = _smooth_grid(n=20, R=82.0)
    cx, cy, cz = _inject_spike(cx, cy, cz, 5, 5, magnitude=200.0)

    report = C.detect_propagated_defects((sx, sy, sz), (cx, cy, cz))
    assert report["same_shape"] is False
    assert report["correspondence_method"] == "grid_index_overlap_region"
    assert report["compared_region_shape"] == [20, 20]
    assert report["verdict"] == "PROPAGATION_DETECTED"


def test_no_candidate_defects_short_circuits_before_source_comparison():
    sx, sy, sz = _smooth_grid(n=20)
    cx, cy, cz = _smooth_grid(n=20, R=82.0)
    report = C.detect_propagated_defects((sx, sy, sz), (cx, cy, cz))
    assert report["verdict"] == "NO_CANDIDATE_DEFECTS"
    assert report["candidate_defect_count"] == 0
    assert report["propagated_fraction_of_candidate_defects"] == 0.0
