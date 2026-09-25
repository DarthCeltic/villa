"""Depth compositing: fixed physical offsets, identical coordinates, gated interior-only composites."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core import depth_composite as dc
from argus.core import signed_normal as sn
from argus.core.signed_normal import SignDecision


def _decision(level=sn.TRUSTED, sign=1, fold=0.0, confirmed=True):
    return SignDecision(sign=sign, method=sn.WINDING_FIELD, confidence_level=level,
                        agreement_fraction=0.99, n_valid=100, mean_winding_delta=0.2,
                        fold_region_fraction=fold,
                        independent_evidence=[sn.MANUAL_VERIFICATION] if confirmed else [])


def _ramp_volume(shape=(20, 24, 28)):
    z, y, x = np.meshgrid(*(np.arange(s, dtype=np.float32) for s in shape), indexing="ij")
    return 100.0 * z + 10.0 * y + x


def _points_normals(n=5, normal=(1.0, 0.0, 0.0)):
    pts = np.tile(np.array([[10.0, 12.0, 14.0]]), (n, 1)) + np.arange(n)[:, None] * np.array([0.0, 0.5, 0.25])
    return pts, np.tile(np.array([normal]), (n, 1))


def test_offsets_exact_mapping():
    out = dc.physical_offsets_to_layers([0.0, 7.5, -15.0], 7.5)
    assert [k for _, k, _ in out] == [0, 1, -2]
    assert all(abs(r) < 1e-9 for _, _, r in out)
    assert out[0][0] == 0.0


def test_offsets_within_tolerance_report_residual():
    (off, k, res), = dc.physical_offsets_to_layers([8.5], 7.5)
    assert k == 1 and res == pytest.approx(1.0)


def test_offsets_refused_when_between_layers():
    with pytest.raises(dc.OffsetRefused):
        dc.physical_offsets_to_layers([11.25], 7.5)
    with pytest.raises(dc.OffsetRefused):
        dc.physical_offsets_to_layers([10.0], 7.5, tol_fraction=0.1)


@pytest.mark.parametrize("pitch", [0, -3.0, float("nan"), float("inf")])
def test_offsets_refused_for_bad_pitch(pitch):
    with pytest.raises(dc.OffsetRefused):
        dc.physical_offsets_to_layers([7.5], pitch)


def test_offsets_refused_for_nonfinite_offset_or_bad_tolerance():
    with pytest.raises(dc.OffsetRefused):
        dc.physical_offsets_to_layers([float("nan")], 7.5)
    with pytest.raises(dc.OffsetRefused):
        dc.physical_offsets_to_layers([7.5], 7.5, tol_fraction=0.5)


def test_sample_along_normal_on_linear_ramp():
    vol = _ramp_volume()
    pts, nrm = _points_normals()
    layers = [-2, -1, 0, 1, 3]
    res = dc.sample_along_normal(vol, pts, nrm, layers)
    assert res.stack.shape == (5, 5)
    assert res.coords.dtype == np.float32 and res.coords.shape == (5, 5, 3)
    for i, k in enumerate(layers):
        expected = 100.0 * (pts[:, 0] + k) + 10.0 * pts[:, 1] + pts[:, 2]
        np.testing.assert_allclose(res.stack[i], expected, rtol=0, atol=1e-2)


def test_sample_along_normal_normalises_normals_and_keeps_grid_shape():
    vol = _ramp_volume()
    pts = np.full((3, 4, 3), [10.0, 12.0, 14.0])
    nrm = np.full((3, 4, 3), [0.0, 0.0, 5.0])
    res = dc.sample_along_normal(vol, pts, nrm, [0, 2])
    assert res.stack.shape == (2, 3, 4)
    np.testing.assert_allclose(res.stack[1] - res.stack[0], 2.0, atol=1e-3)


def test_sample_along_normal_out_of_bounds_and_bad_normals_are_nan():
    vol = _ramp_volume()
    pts = np.array([[10.0, 12.0, 14.0], [10.0, 12.0, 14.0], [0.0, 0.0, 0.0]])
    nrm = np.array([[1.0, 0, 0], [0.0, 0.0, 0.0], [1.0, 0, 0]])
    res = dc.sample_along_normal(vol, pts, nrm, [0, -1])
    assert np.isfinite(res.stack[0, 0])
    assert np.isnan(res.stack[0, 1])
    assert np.isfinite(res.stack[0, 2]) and np.isnan(res.stack[1, 2])


def test_sample_along_normal_rejects_bad_inputs():
    vol = _ramp_volume()
    pts, nrm = _points_normals()
    with pytest.raises(ValueError):
        dc.sample_along_normal(vol[0], pts, nrm, [0])
    with pytest.raises(ValueError):
        dc.sample_along_normal(vol, pts, nrm[:2], [0])
    with pytest.raises(ValueError):
        dc.sample_along_normal(vol, pts, nrm, [])


def test_coordinates_hash_equality_and_inequality():
    pts, nrm = _points_normals()
    a = dc.sample_along_normal(_ramp_volume(), pts, nrm, [0, 1]).coords
    b = dc.sample_along_normal(_ramp_volume() * 2, pts, nrm, [0, 1]).coords
    assert dc.identical_coordinates(a, b)
    assert dc.coordinates_hash(a) == dc.coordinates_hash(b.copy())
    moved = a.copy()
    moved[0, 0, 0] += 1e-3
    assert not dc.identical_coordinates(a, moved)
    assert not dc.identical_coordinates(a, a.reshape(-1, 3))
    assert len(dc.coordinates_hash(a)) == 64


def test_symmetric_composite_of_symmetric_stack():
    layer_values = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
    stack = np.broadcast_to(layer_values[:, None, None], (5, 2, 3)).copy()
    np.testing.assert_allclose(dc.symmetric_composite(stack, 2, 2, mode="mean"), 9.0 / 5)
    np.testing.assert_allclose(dc.symmetric_composite(stack, 2, 1, mode="mean"), 7.0 / 3)
    np.testing.assert_allclose(dc.symmetric_composite(stack, 2, 2, mode="max"), 3.0)
    np.testing.assert_allclose(dc.symmetric_composite(stack, 2, 2, mode="min"), 1.0)
    np.testing.assert_allclose(dc.symmetric_composite(stack, 2, 0), 3.0)


def test_symmetric_composite_is_nan_safe_and_validates():
    stack = np.array([[[1.0, np.nan]], [[3.0, np.nan]], [[np.nan, np.nan]]])
    np.testing.assert_allclose(dc.symmetric_composite(stack, 1, 1), [[2.0, np.nan]])
    np.testing.assert_allclose(dc.symmetric_composite(stack, 1, 1, mode="max"), [[3.0, np.nan]])
    with pytest.raises(ValueError):
        dc.symmetric_composite(stack, 1, 2)
    with pytest.raises(ValueError):
        dc.symmetric_composite(stack, 1, 1, mode="median")


def _asymmetric_stack():
    values = np.arange(7, dtype=np.float64)
    return np.broadcast_to(values[:, None], (7, 4)).copy()


def test_interior_composite_refuses_untrusted_decisions():
    stack = _asymmetric_stack()
    for level in (sn.UNCERTAIN, sn.HINT_ONLY, sn.REFUSED):
        with pytest.raises(dc.InteriorRefused, match=level):
            dc.interior_composite(stack, 3, _decision(level=level), depth_layers=2)
    with pytest.raises(dc.InteriorRefused, match="fold"):
        dc.interior_composite(stack, 3, _decision(fold=0.5), depth_layers=2)


def test_interior_composite_uses_correct_side_for_sign():
    stack = _asymmetric_stack()
    plus = dc.interior_composite(stack, 3, _decision(sign=1), depth_layers=2)
    minus = dc.interior_composite(stack, 3, _decision(sign=-1), depth_layers=2)
    np.testing.assert_allclose(plus, 4.0)
    np.testing.assert_allclose(minus, 2.0)
    np.testing.assert_allclose(dc.interior_composite(stack, 3, _decision(sign=1), depth_layers=2, mode="max"), 5.0)
    with pytest.raises(ValueError):
        dc.interior_composite(stack, 3, _decision(sign=1), depth_layers=4)


def test_both_orientations_differ_for_asymmetric_volume_and_are_labelled_unsigned():
    stack = _asymmetric_stack()
    res = dc.both_orientations(stack, 3, 2, "mean")
    pos, neg = res
    assert not np.allclose(pos, neg)
    np.testing.assert_allclose(res.positive_side, 4.0)
    np.testing.assert_allclose(res.negative_side, 2.0)
    assert res.label == dc.UNSIGNED_LABEL and res.sign_trusted is False


def test_overlay_sample_uses_identical_coordinates():
    vol = _ramp_volume()
    pred = np.random.default_rng(7).random(vol.shape).astype(np.float32)
    pts, nrm = _points_normals()
    ct = dc.sample_along_normal(vol, pts, nrm, [-1, 0, 1])
    ov = dc.overlay_sample(pred, pts, nrm, [-1, 0, 1])
    assert dc.identical_coordinates(ct.coords, ov.coords)
    assert ov.stack.shape == ct.stack.shape


def test_compare_refuses_mismatched_coordinates():
    vol = _ramp_volume()
    pts, nrm = _points_normals()
    ct = dc.sample_along_normal(vol, pts, nrm, [0, 1])
    other = dc.overlay_sample(vol, pts + 0.5, nrm, [0, 1])
    with pytest.raises(dc.CoordinateMismatch):
        dc.compare_identical_coordinates(ct.stack, ct.coords, other.stack, other.coords)


def test_compare_identical_stacks_gives_unit_correlation_and_descriptive_dict():
    vol = _ramp_volume()
    pts, nrm = _points_normals(n=8)
    ct = dc.sample_along_normal(vol, pts, nrm, [-1, 0, 1])
    res = dc.compare_identical_coordinates(ct.stack, ct.coords, ct.stack, ct.coords, layer_indices=[-1, 0, 1])
    assert res["prediction_is_ground_truth"] is False
    assert res["n_layers"] == 3 and len(res["per_layer"]) == 3
    for row in res["per_layer"]:
        assert row["pearson_r"] == pytest.approx(1.0)
        assert row["ct_finite_fraction"] == 1.0
    assert res["per_layer"][0]["layer_index"] == -1
    assert res["coordinates_sha256"] == dc.coordinates_hash(ct.coords)


def test_compare_is_nan_safe_and_makes_no_ink_claim():
    rng = np.random.default_rng(11)
    vol = rng.random((16, 16, 16)).astype(np.float32)
    pts = np.array([[8.0, 8.0, 8.0], [8.0, 9.0, 8.0], [8.0, 10.0, 8.0], [99.0, 0.0, 0.0]])
    nrm = np.tile([[1.0, 0, 0]], (4, 1))
    ct = dc.sample_along_normal(vol, pts, nrm, [0, 1])
    pred = dc.overlay_sample(-vol, pts, nrm, [0, 1])
    res = dc.compare_identical_coordinates(ct.stack, ct.coords, pred.stack, pred.coords)
    assert res["per_layer"][0]["ct_finite_fraction"] == pytest.approx(0.75)
    assert res["per_layer"][0]["n_paired"] == 3
    assert res["per_layer"][0]["pearson_r"] == pytest.approx(-1.0)
    assert res["prediction_is_ground_truth"] is False

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield str(k)
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    assert not any("ink" in k.lower() for k in keys(res))
    assert dc.EVIDENCE_LABEL not in str(res)


def test_compare_constant_layer_has_no_correlation():
    vol = np.ones((10, 10, 10), dtype=np.float32)
    pts, nrm = _points_normals(n=4)
    s = dc.sample_along_normal(vol, pts, nrm, [0])
    res = dc.compare_identical_coordinates(s.stack, s.coords, s.stack, s.coords)
    assert res["per_layer"][0]["pearson_r"] is None


def test_an_unconfirmed_orientation_is_refused_for_a_one_sided_composite():
    stack = _asymmetric_stack()
    with pytest.raises(dc.InteriorRefused, match="ORIENTATION_UNCONFIRMED"):
        dc.interior_composite(stack, 3, _decision(confirmed=False), depth_layers=2)


def test_one_sided_depth_is_capped_at_the_safe_range():
    stack = np.tile(np.arange(12, dtype=np.float32)[:, None, None], (1, 2, 2))
    dc.interior_composite(stack, 2, _decision(sign=1), depth_layers=sn.ONE_SIDED_MAX_DEPTH_LAYERS)
    with pytest.raises(dc.OffsetRefused, match="neighbouring sheet"):
        dc.interior_composite(stack, 2, _decision(sign=1), depth_layers=sn.ONE_SIDED_MAX_DEPTH_LAYERS + 1)


def test_the_default_composite_is_symmetric_until_orientation_is_confirmed():
    stack = _asymmetric_stack()
    img, how = dc.default_composite(stack, 3, _decision(confirmed=False), half_width=2)
    np.testing.assert_allclose(img, dc.symmetric_composite(stack, 3, 2))
    assert how["composite"] == "SYMMETRIC" and how["orientation_status"] == sn.ORIENTATION_UNCONFIRMED
    img2, how2 = dc.default_composite(stack, 3, _decision(sign=1, confirmed=True), half_width=2)
    np.testing.assert_allclose(img2, 4.0)
    assert how2["composite"] == "ONE_SIDED_INTERIOR" and how2["depth_layers"] == 2


def test_the_default_composite_caps_the_one_sided_depth():
    stack = np.tile(np.arange(20, dtype=np.float32)[:, None, None], (1, 2, 2))
    _, how = dc.default_composite(stack, 3, _decision(sign=1, confirmed=True), half_width=9)
    assert how["depth_layers"] == sn.ONE_SIDED_MAX_DEPTH_LAYERS
