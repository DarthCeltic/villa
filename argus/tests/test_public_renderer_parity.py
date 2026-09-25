"""Renderer parity on synthetic data: two independent implementations of the same contract must agree.

This is not a comparison with any external renderer. It checks the rendering primitives this release
ships (sampling along a sheet normal, seam-free tiling, physical resampling) against small,
independent reference implementations written here in plain loops, on a synthetic volume, and it
shows that each check can fail. A user comparing their own renderer to ARGUS can reuse these
functions: pass the same volume, points and normals through both and compare the arrays.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from argus.core import compositor as C
from argus.core import depth_composite as DC
from argus.core import physical_resample as PR



def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


# ------------------------------------------------------------------ reference implementations

def _trilinear(vol: np.ndarray, p) -> float:
    """Plain-loop trilinear interpolation at one (z, y, x) point; NaN outside the volume."""
    if not np.all(np.isfinite(p)) or np.any(np.asarray(p) < 0) or np.any(np.asarray(p) > np.asarray(vol.shape) - 1):
        return float("nan")
    z, y, x = (float(v) for v in p)
    z0, y0, x0 = (min(int(np.floor(v)), s - 2) for v, s in zip((z, y, x), vol.shape))
    fz, fy, fx = z - z0, y - y0, x - x0
    acc = 0.0
    for dz, wz in ((0, 1 - fz), (1, fz)):
        for dy, wy in ((0, 1 - fy), (1, fy)):
            for dx, wx in ((0, 1 - fx), (1, fx)):
                acc += wz * wy * wx * float(vol[z0 + dz, y0 + dy, x0 + dx])
    return acc


def _reference_stack(vol, points, normals, layers):
    pts = points.reshape(-1, 3)
    nrm = normals.reshape(-1, 3)
    out = np.full((len(layers), pts.shape[0]), np.nan)
    for i, k in enumerate(layers):
        for j in range(pts.shape[0]):
            n = np.linalg.norm(nrm[j])
            if n <= 1e-12:
                continue
            out[i, j] = _trilinear(vol, pts[j] + k * nrm[j] / n)
    return out.reshape((len(layers),) + points.shape[:-1])


def _reference_axis_weights(n_src, src_pitch, dst_pitch, n_dst):
    """Overlap length of source cell i and output cell j, divided by the output pitch."""
    W = np.zeros((n_dst, n_src))
    for j in range(n_dst):
        for i in range(n_src):
            overlap = min((j + 1) * dst_pitch, (i + 1) * src_pitch) - max(j * dst_pitch, i * src_pitch)
            if overlap > 0:
                W[j, i] = overlap / dst_pitch
    return W


# ------------------------------------------------------------------ sampling along a normal

def _oblique_case():
    vol = _rng(20260921).normal(0.5, 0.2, size=(24, 28, 30)).astype(np.float32)
    yy, xx = np.meshgrid(np.linspace(6, 20, 5), np.linspace(6, 22, 6), indexing="ij")
    points = np.stack([np.full(yy.shape, 12.0) + 0.3 * xx / 10, yy, xx], axis=-1)
    normals = np.stack([np.ones_like(yy), 0.25 * np.ones_like(yy), -0.15 * np.ones_like(yy)], axis=-1)
    return vol, points, normals


def test_sampling_along_oblique_normals_matches_a_plain_loop_reference():
    vol, points, normals = _oblique_case()
    layers = list(range(-3, 4))
    got = DC.sample_along_normal(vol, points, normals, layers).stack
    ref = _reference_stack(vol, points, normals, layers)
    assert got.shape == ref.shape
    assert np.allclose(got, ref, atol=1e-4, equal_nan=True)


def test_points_that_leave_the_volume_are_nan_in_both_implementations():
    vol, points, normals = _oblique_case()
    layers = [-30, 0, 30]
    got = DC.sample_along_normal(vol, points, normals, layers).stack
    ref = _reference_stack(vol, points, normals, layers)
    assert np.isnan(got[0]).all() and np.isnan(ref[0]).all()
    assert np.isnan(got[2]).all() and np.isnan(ref[2]).all()
    assert np.isfinite(got[1]).all() and np.allclose(got[1], ref[1], atol=1e-4)


def test_the_parity_check_can_fail_a_half_voxel_shift_is_caught():
    vol, points, normals = _oblique_case()
    layers = [0]
    got = DC.sample_along_normal(vol, points + np.array([0.0, 0.5, 0.0]), normals, layers).stack
    ref = _reference_stack(vol, points, normals, layers)
    assert not np.allclose(got, ref, atol=1e-4)


def test_flipping_the_normal_reverses_the_layer_order_exactly():
    vol, points, normals = _oblique_case()
    layers = list(range(-2, 3))
    forward = DC.sample_along_normal(vol, points, normals, layers).stack
    flipped = DC.sample_along_normal(vol, points, -normals, layers).stack
    assert np.allclose(forward, flipped[::-1], atol=1e-5)


def test_identical_coordinates_hash_identically_and_a_shift_changes_the_hash():
    vol, points, normals = _oblique_case()
    a = DC.sample_along_normal(vol, points, normals, [0, 1]).coords
    b = DC.sample_along_normal(vol, points, normals, [0, 1]).coords
    c = DC.sample_along_normal(vol, points + 0.01, normals, [0, 1]).coords
    assert DC.coordinates_hash(a) == DC.coordinates_hash(b)
    assert DC.coordinates_hash(a) != DC.coordinates_hash(c)


# ------------------------------------------------------------------ tiling parity

def _mean3(a: np.ndarray) -> np.ndarray:
    p = np.pad(a, 1, mode="edge")
    out = np.zeros(a.shape, dtype=np.float64)
    for dy in range(3):
        for dx in range(3):
            out += p[dy:dy + a.shape[0], dx:dx + a.shape[1]]
    return (out / 9.0).astype(np.float32)


def _twice(a: np.ndarray) -> np.ndarray:
    return _mean3(_mean3(a))


def _tiled(image: np.ndarray, fn, patch: int, halo: int) -> np.ndarray:
    def run_patch(tile, mask):
        window = np.zeros((tile.rh, tile.rw), dtype=np.float32)
        y0, x0 = tile.ry0 + tile.vy0, tile.rx0 + tile.vx0
        window[tile.vy0:tile.vy0 + tile.vh, tile.vx0:tile.vx0 + tile.vw] = image[y0:y0 + tile.vh, x0:x0 + tile.vw]
        return fn(window)
    return C.composite(image.shape[0], image.shape[1], patch, halo, run_patch)


def _image():
    return _rng(3).normal(0.5, 0.2, size=(70, 83)).astype(np.float32)


def test_tiled_rendering_equals_the_whole_image_away_from_the_border():
    image = _image()
    inner = (slice(3, -3), slice(3, -3))
    assert np.allclose(_tiled(image, _mean3, 24, 3)[inner], _mean3(image)[inner], atol=1e-6)
    assert np.allclose(_tiled(image, _twice, 24, 4)[4:-4, 4:-4], _twice(image)[4:-4, 4:-4], atol=1e-6)


def test_cores_partition_the_image_exactly_once():
    tiles = C.plan_tiles(70, 83, 24, 3)
    assert (C.coverage(tiles, 70, 83) == 1).all()


def test_the_seam_check_can_fail_a_halo_smaller_than_the_filter_support_disagrees_at_the_seams():
    image = _image()
    whole = _twice(image)
    seamed = _tiled(image, _twice, 24, 1)                      # a two-pass 3x3 filter needs a halo of 2
    assert not np.allclose(seamed[4:-4, 4:-4], whole[4:-4, 4:-4], atol=1e-6)
    seams = np.abs(seamed - whole)
    assert seams.max() > 1e-3


def test_a_patch_function_that_returns_the_wrong_shape_is_refused():
    with pytest.raises(ValueError):
        C.composite(20, 20, 16, 2, lambda tile, mask: np.zeros((3, 3)))


# ------------------------------------------------------------------ physical resampling parity

@pytest.mark.parametrize("n_src,src_pitch,dst_pitch", [(37, 2.4, 9.6), (50, 3.24, 9.362), (41, 9.6, 2.4)])
def test_axis_weights_match_an_independent_overlap_reference(n_src, src_pitch, dst_pitch):
    W, n_dst = PR.axis_weights(n_src, src_pitch, dst_pitch)
    assert np.allclose(W, _reference_axis_weights(n_src, src_pitch, dst_pitch, n_dst), atol=1e-12)


def test_resampling_to_the_same_pitch_is_the_identity_and_conserves_the_mean():
    a = _rng(5).normal(0.5, 0.2, size=(20, 24))
    assert np.allclose(PR.resample_2d(a, 2.4, 2.4), a, atol=1e-12)
    coarse = PR.resample_2d(a, 2.4, 9.6)
    assert coarse.shape == (5, 6)
    assert coarse.mean() == pytest.approx(a[:20, :24].mean(), abs=1e-9)


def test_a_thin_stroke_survives_resampling_as_partial_occupancy_not_as_zero():
    stroke = np.zeros((32, 32))
    stroke[16, :] = 1.0
    occupancy = PR.resample_occupancy(stroke, 3.24, 9.362)
    assert occupancy.max() > 0.2 and occupancy.max() < 1.0


def test_the_resampling_parity_check_can_fail_a_shifted_grid_disagrees():
    W, _ = PR.axis_weights(40, 2.4, 9.6)
    shifted = np.roll(W, 1, axis=1)
    assert not np.allclose(shifted, _reference_axis_weights(40, 2.4, 9.6, W.shape[0]), atol=1e-12)


# ------------------------------------------------------------------ the parity report replays

def test_the_parity_report_replays_to_identical_bytes():
    def report():
        vol, points, normals = _oblique_case_seeded()
        got = DC.sample_along_normal(vol, points, normals, [-1, 0, 1]).stack
        return hashlib.sha256(np.ascontiguousarray(got).tobytes()).hexdigest()
    assert report() == report()


def _oblique_case_seeded():
    rng = np.random.default_rng(7)
    vol = rng.normal(0.5, 0.2, size=(24, 28, 30)).astype(np.float32)
    yy, xx = np.meshgrid(np.linspace(6, 20, 5), np.linspace(6, 22, 6), indexing="ij")
    points = np.stack([np.full(yy.shape, 12.0), yy, xx], axis=-1)
    normals = np.stack([np.ones_like(yy), np.zeros_like(yy), np.zeros_like(yy)], axis=-1)
    return vol, points, normals
