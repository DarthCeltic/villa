"""Fixtures for the exact physical resampler, each aimed at a way the rounded-block path failed."""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from argus.core.physical_resample import (
    axis_weights, grid_report, resample_2d, resample_3d, resample_occupancy, resample_support,
)

SRC = 2.5
DST = 8.2



def test_achieved_pitch_is_exact_not_rounded():
    r = grid_report(2048, SRC, DST)
    assert r["achieved_pitch_um"] == DST
    assert r["residual_pitch_error_percent"] == 0.0
    assert abs(3 * SRC - DST) / DST > 0.03


def test_output_extent_never_exceeds_measured_material():
    r = grid_report(2048, SRC, DST)
    assert r["dst_extent_um"] <= r["src_extent_um"]
    assert r["extent_loss_um"] < DST


def test_weights_are_row_stochastic():
    W, n = axis_weights(2048, SRC, DST)
    sums = W.sum(axis=1)
    assert np.allclose(sums, 1.0, atol=1e-9), (sums.min(), sums.max())



def test_impulse_conserves_mass():
    """An integrating kernel must conserve total signal, up to the cropped remainder."""
    a = np.zeros((256, 256))
    a[100, 140] = 1.0
    out = resample_2d(a, SRC, DST)
    scale = (DST / SRC) ** 2
    assert out.sum() * scale == pytest.approx(1.0, rel=1e-6)


def test_impulse_lands_at_the_right_physical_place():
    a = np.zeros((256, 256))
    a[100, 140] = 1.0
    out = resample_2d(a, SRC, DST)
    ry, rx = np.unravel_index(np.argmax(out), out.shape)
    cy, cx = (100 + 0.5) * SRC, (140 + 0.5) * SRC
    assert ry * DST <= cy < (ry + 1) * DST
    assert rx * DST <= cx < (rx + 1) * DST


def test_impulse_is_not_duplicated():
    a = np.zeros((256, 256))
    a[100, 140] = 1.0
    out = resample_2d(a, SRC, DST)
    assert (out > 1e-9).sum() <= 4



@pytest.mark.parametrize("width", [1, 2])
def test_thin_stroke_survives_as_nonzero_occupancy(width):
    """THE fixture."""
    ink = np.zeros((256, 256), dtype=bool)
    ink[:, 120:120 + width] = True
    occ = resample_occupancy(ink, SRC, DST)
    assert occ.max() > 0.0, "a %d-pixel stroke was erased entirely" % width
    assert occ.max() < 1.0
    rows_with_signal = (occ.max(axis=1) > 0).mean()
    assert rows_with_signal > 0.99


@pytest.mark.parametrize("width", [1, 2])
def test_old_majority_threshold_would_have_erased_it(width):
    """Pins the defect being corrected, so nobody reintroduces the threshold as a tidy-up."""
    ink = np.zeros((256, 256), dtype=bool)
    ink[:, 120:120 + width] = True
    occ = resample_occupancy(ink, SRC, DST)
    assert (occ > 0.5).sum() == 0, "this fixture no longer demonstrates the old failure"
    assert (occ > 0.0).sum() > 0


def test_stroke_occupancy_scales_with_width():
    ink1 = np.zeros((256, 256), dtype=bool); ink1[:, 120:121] = True
    ink2 = np.zeros((256, 256), dtype=bool); ink2[:, 120:122] = True
    o1, o2 = resample_occupancy(ink1, SRC, DST), resample_occupancy(ink2, SRC, DST)
    assert o2.sum() > o1.sum() * 1.5



def test_coordinate_round_trip_within_half_a_cell():
    """An output cell's physical centre must map back into the source it integrated."""
    _, n_dst = axis_weights(2048, SRC, DST)
    for j in (0, 1, n_dst // 3, n_dst // 2, n_dst - 1):
        centre_um = (j + 0.5) * DST
        i = int(centre_um // SRC)
        assert 0 <= i < 2048
        back = int((i + 0.5) * SRC // DST)
        assert abs(back - j) <= 1


def test_grids_share_physical_origin():
    W, _ = axis_weights(64, SRC, DST)
    assert W[0, 0] > 0, "output cell 0 must overlap source cell 0; the grids share origin 0"



def test_depth_integrates_rather_than_selects():
    """61 planes to 21 must AVERAGE, not pick 21 and discard 40."""
    vol = np.zeros((61, 8, 8))
    vol[30] = 1.0
    out = resample_3d(vol, (SRC, SRC, SRC), (DST, DST, DST), (21, None, None))
    assert out.sum() > 0, "the signal plane was discarded -- this is the nearest-selection bug"

    covs = []
    for ax, n_dst_ax in zip(range(3), out.shape):
        W, _ = axis_weights(vol.shape[ax], SRC, DST, n_dst_ax)
        covs.append(np.clip(W.sum(axis=0) * DST / SRC, 0.0, 1.0))
    expected = float(np.einsum("ijk,i,j,k->", vol, covs[0], covs[1], covs[2]))
    assert out.sum() * DST ** 3 == pytest.approx(expected * SRC ** 3, rel=1e-6)


def test_no_source_plane_is_ignored():
    """Every source plane must influence some output plane."""
    n_src = 61
    W, n_dst = axis_weights(n_src, SRC, DST, 21)
    touched = (W.sum(axis=0) > 0)
    covered_extent = n_dst * DST
    for i in range(n_src):
        if (i + 1) * SRC <= covered_extent:
            assert touched[i], "source plane %d influences no output plane" % i



def test_support_shrinks_at_a_boundary_and_never_grows():
    mask = np.zeros((256, 256), dtype=bool)
    mask[50:200, 50:200] = True
    sup = resample_support(mask, SRC, DST)
    frac = resample_2d(mask.astype(float), SRC, DST)
    assert sup.sum() <= (frac > 0).sum(), "support grew into partially covered cells"
    assert sup.sum() > 0
    assert not sup[(frac > 0.4) & (frac < 0.6)].any()


def test_support_and_occupancy_use_different_rules_on_purpose():
    a = np.zeros((256, 256), dtype=bool)
    a[:, 120:121] = True
    occ = resample_occupancy(a, SRC, DST)
    sup = resample_support(a, SRC, DST)
    assert occ.max() > 0 and sup.sum() == 0, (
        "a one-pixel stroke is real INK occupancy but is not full SUPPORT; collapsing the two "
        "rules would either erase the stroke or invent support")
