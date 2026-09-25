"""Guards for the metric -> flat-frame warp."""
from __future__ import annotations

import pytest
import numpy as np

from argus.core import surface_metric as SM


def _sheet(sx=1.6, sy=0.75, sh=0.3, n=96):
    gu, gv = np.meshgrid(np.arange(n, dtype=np.float64), np.arange(n, dtype=np.float64),
                         indexing="ij")
    return sx * gu + sh * gv, sy * gv, np.full((n, n), 100.0)


def test_sqrt_squares_back_to_the_metric():
    E, F, G = 2.3, 0.7, 1.1
    a, b, d = SM.sym_sqrt_2x2(E, F, G)
    assert abs((a * a + b * b) - E) < 1e-12
    assert abs((a * b + b * d) - F) < 1e-12
    assert abs((b * b + d * d) - G) < 1e-12


def test_isotropic_metric_gives_isotropic_root():
    """The eigenvector-free form exists for this case: eigenvectors are arbitrary at isotropy, and isotropy is most of a nearly-flat sheet."""
    a, b, d = SM.sym_sqrt_2x2(4.0, 0.0, 4.0)
    assert abs(a - 2.0) < 1e-12 and abs(d - 2.0) < 1e-12 and abs(b) < 1e-12


def test_metric_recovered_from_xyz_alone():
    sx, sy, sh = 1.6, 0.75, 0.3
    E, F, G, good = SM.first_fundamental_form(*_sheet(sx, sy, sh))
    Jt = np.array([[sx, 0.0], [sh, sy]])
    M = Jt @ Jt.T
    assert np.abs(E[good] - M[0, 0]).max() < 1e-8
    assert np.abs(F[good] - M[0, 1]).max() < 1e-8
    assert np.abs(G[good] - M[1, 1]).max() < 1e-8


def test_correction_leaves_a_square_lattice():
    """J = R.S, so warping by inv(S) and mapping through J leaves a rotation -- a SQUARE lattice."""
    for sx, sy, sh in [(1.0, 1.0, 0.0), (1.4, 1.0, 0.0), (1.3, 0.8, 0.25)]:
        J = np.array([[sx, sh], [0.0, sy]])
        M = J.T @ J
        a, b, d = SM.sym_sqrt_2x2(M[0, 0], M[0, 1], M[1, 1])
        du, dv = SM.flat_lattice_offsets(a, b, d, 8, 1.0)
        pts = np.stack([du.ravel(), dv.ravel()], 0)
        phys = J @ pts
        phys = phys - phys.mean(axis=1, keepdims=True)
        gram = phys @ phys.T / pts.shape[1]
        assert np.abs(gram / gram[0, 0] - np.eye(2)).max() < 1e-9


def test_identity_patch_is_exactly_the_plain_cut():
    """REGRESSION."""
    x, y, z = _sheet()
    xi, _, _, _ = SM.identity_patch(x, y, z, 48, 48, 32)
    assert np.abs(xi - x[48 - 16:48 + 16, 48 - 16:48 + 16]).max() < 1e-9


def test_lattice_is_not_transposed():
    """REGRESSION."""
    du, dv = SM.flat_lattice_offsets(1.0, 0.0, 1.0, 5, 1.0)
    assert du[0, 0] < du[-1, 0]
    assert abs(du[0, 0] - du[0, -1]) < 1e-12
    assert dv[0, 0] < dv[0, -1]
    assert abs(dv[0, 0] - dv[-1, 0]) < 1e-12


def test_flat_sheet_needs_no_correction():
    """A sheet already at reference geometry must come back as (near) the plain cut: the correction must not do something to data that has nothing wrong with it."""
    n = 96
    gu, gv = np.meshgrid(np.arange(n, dtype=np.float64), np.arange(n, dtype=np.float64),
                         indexing="ij")
    p, _why = SM.reference_pitch_um("example/fragment-trained-checkpoint")
    x, y, z = p * gu, p * gv, np.full((n, n), 10.0)
    E, F, G, _ = SM.first_fundamental_form(x, y, z)
    xw, _, _, _, info = SM.flatten_tifxyz_patch(x, y, z, 48, 48, 16, E, F, G, pitch_ref_um=p)
    assert abs(info["pitch_ref_grid"] - 1.0) < 1e-9
    xi, _, _, _ = SM.identity_patch(x, y, z, 48, 48, 16)
    assert np.abs(xw - xi).max() < 1e-6


def test_depth_scale_is_secant_and_clamped():
    assert abs(SM.depth_scale_from_normal_dev(0.0) - 1.0) < 1e-12
    assert abs(SM.depth_scale_from_normal_dev(60.0) - 2.0) < 1e-9
    assert SM.depth_scale_from_normal_dev(89.0) <= 2.0 + 1e-9


def test_metric_self_consistency():
    ag = SM.metric_self_consistency(*_sheet(1.2, 0.9, 0.15))
    assert ag["agrees"] is True


def test_bilinear_matches_scipy():
    """Cross-check the hand-rolled interpolator against a mature implementation."""
    from scipy import ndimage

    rng = np.random.default_rng(7)
    A = rng.normal(size=(40, 40))
    uu = rng.uniform(0.0, 38.5, size=(12, 12))
    vv = rng.uniform(0.0, 38.5, size=(12, 12))
    mine = SM.bilinear(A, uu, vv)
    theirs = ndimage.map_coordinates(A, [uu, vv], order=1, mode="nearest")
    assert np.abs(mine - theirs).max() < 1e-9


def test_reference_pitch_refuses_an_unknown_checkpoint():
    """REGRESSION, and the most dangerous bug this module had."""
    import pytest

    with pytest.raises(SM.ReferencePitchUnknown):
        SM.reference_pitch_um("some/checkpoint/nobody/declared")

    p, why = SM.reference_pitch_um("example/fragment-trained-checkpoint")
    assert p == 4.0 and "fragment" in why.lower()
    assert SM.reference_pitch_um("example/scroll-pitch-annotated")[0] == 8.0


def test_flatten_requires_an_explicit_reference_pitch():
    """No default: the caller must state the reference and therefore think about it."""
    import inspect

    sig = inspect.signature(SM.flatten_tifxyz_patch)
    assert sig.parameters["pitch_ref_um"].default is inspect.Parameter.empty



def _big_sheet(sx=1.3, sy=0.9, sh=0.2, n=400):
    """A sheet large enough that no patch below reaches its edge."""
    gu, gv = np.meshgrid(np.arange(n, dtype=np.float64), np.arange(n, dtype=np.float64),
                         indexing="ij")
    return sx * gu + sh * gv, sy * gv, np.full((n, n), 100.0)


def _physical_extent(x, y, z, cu, cv, n, E, F, G, pitch):
    """Diagonal physical span of the sampled patch, in microns."""
    xw, yw, zw, inside, _ = SM.flatten_tifxyz_patch(x, y, z, cu, cv, n, E, F, G,
                                                    pitch_ref_um=pitch)
    assert inside.all(), ("patch was clamped at the sheet boundary -- widen the sheet or "
                          "shrink the patch; this reading would be meaningless")
    d = np.array([xw[-1, -1] - xw[0, 0], yw[-1, -1] - yw[0, 0], zw[-1, -1] - zw[0, 0]])
    return float(np.linalg.norm(d))


def test_extent_is_linear_in_the_reference_pitch():
    """Double the reference pitch -> the patch must cover exactly twice the physical distance."""
    x, y, z = _big_sheet()
    E, F, G, _ = SM.first_fundamental_form(x, y, z)
    e1 = _physical_extent(x, y, z, 200, 200, 8, E, F, G, 3.24)
    e2 = _physical_extent(x, y, z, 200, 200, 8, E, F, G, 6.48)
    assert abs(e2 / e1 - 2.0) < 1e-9, "extent/pitch exponent is not 1 (got ratio %.6f)" % (e2 / e1)


def test_extent_is_invariant_to_the_surface_scale():
    """Scale the SHEET by k and the sampled physical extent must NOT change."""
    for k in (0.5, 2.0, 3.7):
        x, y, z = _big_sheet()
        E0, F0, G0, _ = SM.first_fundamental_form(x, y, z)
        base = _physical_extent(x, y, z, 200, 200, 8, E0, F0, G0, 3.24)

        xs, ys, zs = x * k, y * k, z * k
        E1, F1, G1, _ = SM.first_fundamental_form(xs, ys, zs)
        scaled = _physical_extent(xs, ys, zs, 200, 200, 8, E1, F1, G1, 3.24)
        assert abs(scaled / base - 1.0) < 1e-6, (
            "surface scaled by %s changed the sampled extent by %.6f; the correction is not "
            "absorbing the surface scale exactly once" % (k, scaled / base))


def test_grid_offsets_scale_inversely_with_surface_coarseness():
    """A sheet k times coarser must be walked in 1/k as many grid steps."""
    for k in (0.5, 2.0):
        x, y, z = _big_sheet(1.0, 1.0, 0.0)
        E0, F0, G0, _ = SM.first_fundamental_form(x, y, z)
        a0, b0, d0 = SM.sym_sqrt_2x2(E0[200, 200], F0[200, 200], G0[200, 200])
        du0, _ = SM.flat_lattice_offsets(a0, b0, d0, 8, 3.24)

        E1, F1, G1, _ = SM.first_fundamental_form(x * k, y * k, z * k)
        a1, b1, d1 = SM.sym_sqrt_2x2(E1[200, 200], F1[200, 200], G1[200, 200])
        du1, _ = SM.flat_lattice_offsets(a1, b1, d1, 8, 3.24)

        span0 = float(du0.max() - du0.min())
        span1 = float(du1.max() - du1.min())
        assert abs(span1 * k - span0) < 1e-9, (
            "grid span did not scale as 1/k for k=%s (%.6f vs %.6f)" % (k, span1, span0 / k))


def test_a_flat_reference_sheet_is_sampled_at_unit_grid_step():
    """The fixed point of the whole construction: a sheet ALREADY at the reference pitch must be walked one grid step per output pixel."""
    n = 96
    gu, gv = np.meshgrid(np.arange(n, dtype=np.float64), np.arange(n, dtype=np.float64),
                         indexing="ij")
    p, _ = SM.reference_pitch_um("example/fragment-trained-checkpoint")
    x, y, z = p * gu, p * gv, np.full((n, n), 10.0)
    E, F, G, _ = SM.first_fundamental_form(x, y, z)
    a, b, d = SM.sym_sqrt_2x2(E[48, 48], F[48, 48], G[48, 48])
    du, dv = SM.flat_lattice_offsets(a, b, d, 5, p)
    assert abs((du[1, 0] - du[0, 0]) - 1.0) < 1e-9
    assert abs((dv[0, 1] - dv[0, 0]) - 1.0) < 1e-9



def test_the_three_example_pitches_are_distinct_and_each_names_its_evidence():
    """Three different micron figures are in play for one scoring run: what the checkpoint was trained at, what the surface grid is, and what the volume was resampled to."""
    vals = {r: SM.pitch(r)[0] for r in SM.PITCH_ROLES}
    assert vals == {"TRAINING_REFERENCE": 4.0, "SURFACE_GRID": 8.0, "RESAMPLE": 2.0}
    assert len(set(vals.values())) == 3
    for r in SM.PITCH_ROLES:
        assert len(SM.pitch(r)[1]) > 20, r


def test_a_bare_micron_figure_has_no_role_and_is_refused():
    with pytest.raises(SM.PitchRoleMismatch):
        SM.pitch("8.0")


def test_the_correction_ratio_relates_training_reference_to_surface_grid():
    r = SM.pitch_ratio("TRAINING_REFERENCE", "SURFACE_GRID")
    assert abs(r - (4.0 / 8.0)) < 1e-12


def test_the_ratio_is_symmetric_in_the_pair_that_means_something():
    a = SM.pitch_ratio("TRAINING_REFERENCE", "SURFACE_GRID")
    b = SM.pitch_ratio("SURFACE_GRID", "TRAINING_REFERENCE")
    assert abs(a * b - 1.0) < 1e-12


@pytest.mark.parametrize("num,den", [
    ("RESAMPLE", "SURFACE_GRID"),
    ("TRAINING_REFERENCE", "RESAMPLE"),
    ("RESAMPLE", "TRAINING_REFERENCE"),
    ("SURFACE_GRID", "RESAMPLE"),
])
def test_any_pairing_involving_the_resample_pitch_is_refused(num, den):
    """THE SUBSTITUTION THAT WOULD NOT BE NOTICED."""
    with pytest.raises(SM.PitchRoleMismatch):
        SM.pitch_ratio(num, den)


def test_a_run_with_no_declared_pitch_for_a_role_refuses_rather_than_guessing():
    with pytest.raises(SM.ReferencePitchUnknown):
        SM.pitch("SURFACE_GRID", table={"TRAINING_REFERENCE": (3.24, "only this one")})




def _isometric_grid(n=40, sx=1.0, sy=1.0, z0=100.0):
    """A grid that IS its own declared pitch exactly: perfect isometry, stretch (1, 1) everywhere."""
    gu, gv = np.meshgrid(np.arange(n, dtype=np.float64), np.arange(n, dtype=np.float64),
                         indexing="ij")
    return sx * gu, sy * gv, np.full((n, n), z0), (sx, sy)


def test_a_perfect_isometry_has_unit_stretch_everywhere():
    x, y, z, scale = _isometric_grid()
    s1, s2, good = SM.flatten_metric_ratio(x, y, z, scale)
    assert good.any()
    assert np.abs(s1[good] - 1.0).max() < 1e-9
    assert np.abs(s2[good] - 1.0).max() < 1e-9


def test_a_perfect_isometry_summarises_at_the_theoretical_minimum():
    x, y, z, scale = _isometric_grid()
    report = SM.flatten_distortion_summary(x, y, z, scale)
    assert report["status"] == "MEASURED"
    assert abs(report["symmetric_dirichlet_energy"]["median"] - 4.0) < 1e-9
    assert abs(report["area_ratio"]["median"] - 1.0) < 1e-9
    assert abs(report["shear_ratio"]["median"] - 1.0) < 1e-9
    assert report["degenerate_boundary_cells"] == 0


def test_uniform_stretch_is_measured_at_the_correct_ratio():
    """A grid physically 2x its declared pitch in one axis: s1 must read back as ~2, not the stretch's square or its reciprocal -- pinning the direction and the exponent."""
    n = 40
    x, y, z, scale = _isometric_grid(n=n, sx=1.0, sy=1.0)
    x2 = x * 2.0
    s1, s2, good = SM.flatten_metric_ratio(x2, y, z, scale)
    interior = good.copy()
    interior[:2, :] = interior[-2:, :] = interior[:, :2] = interior[:, -2:] = False
    assert interior.any()
    assert np.abs(s1[interior] - 2.0).max() < 1e-6
    assert np.abs(s2[interior] - 1.0).max() < 1e-6


def test_shear_ratio_is_one_only_when_conformal():
    n = 40
    x, y, z, scale = _isometric_grid(n=n)
    x2 = x * 3.0
    report = SM.flatten_distortion_summary(x2, y, z, scale)
    assert report["status"] == "MEASURED"
    assert report["shear_ratio"]["median"] > 1.5
    assert report["area_ratio"]["median"] > 1.5


def test_degenerate_boundary_cells_are_counted_and_excluded_not_averaged_in():
    """A handful of near-collapsed cells (as a raster boundary produces) must be named and dropped from the summary rather than dragging the mean/max into meaninglessness."""
    n = 40
    x, y, z, scale = _isometric_grid(n=n)
    y = y.copy()
    y[18:22, 18:22] = y[18, 18]
    report = SM.flatten_distortion_summary(x, y, z, scale, degenerate_stretch=0.5)
    assert report["status"] == "MEASURED"
    assert report["degenerate_boundary_cells"] > 0
    assert report["n_usable_cells"] == report["n_measurable_cells"] - report["degenerate_boundary_cells"]
    assert abs(report["symmetric_dirichlet_energy"]["median"] - 4.0) < 1e-6


def test_all_degenerate_refuses_a_measurement_rather_than_reporting_a_fake_one():
    x, y, z, scale = _isometric_grid(n=6)
    y = np.zeros_like(y)
    report = SM.flatten_distortion_summary(x, y, z, scale, degenerate_stretch=0.5)
    assert report["status"] == "NO_USABLE_CELLS"
    assert "symmetric_dirichlet_energy" not in report


def test_flatten_metric_ratio_refuses_a_non_positive_scale():
    x, y, z, _ = _isometric_grid()
    with pytest.raises(ValueError):
        SM.flatten_metric_ratio(x, y, z, (0.0, 1.0))
    with pytest.raises(ValueError):
        SM.flatten_metric_ratio(x, y, z, (1.0, -2.0))
