"""Tests for PitchResolver."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from argus.core.pitch import (
    CANVAS_PROVENANCE_UNRESOLVED,
    INFERRED, METHODS, UNKNOWN, VERIFIED, from_ome, from_pyramid_relation,
    from_segment_meta, from_source_identity, from_tifxyz, refuse_filename_inference,
    identify_volume_by_bbox, resolve, tifxyz_grid_step)

FAILURES = []


def check(name, fn):
    try:
        fn()
        print("  PASS %s" % name)
    except Exception as e:
        print("  FAIL %s -- %s" % (name, e))
        FAILURES.append(name)


def _synthetic(step_voxels=20.0, shape=(64, 48)):
    """A flat surface sampled on a regular grid `step_voxels` apart."""
    gy, gx = np.meshgrid(np.arange(shape[0]), np.arange(shape[1]), indexing="ij")
    x = gx * step_voxels + 1000.0
    y = gy * step_voxels + 2000.0
    z = np.full(shape, 500.0)
    return x, y, z



def t_ome_declaration_wins():
    z = {"multiscales": [{"axes": [{"unit": "micrometer"}] * 3,
                          "datasets": [{"path": "0",
                                        "coordinateTransformations": [
                                            {"scale": [2.399, 2.399, 2.399]}]}]}]}
    e = from_ome(z, "0")
    assert e.status == VERIFIED and abs(e.pitch_um_yx[0] - 2.399) < 1e-9, e


def t_unit_scale_is_absence_not_one_micron():
    z = {"multiscales": [{"axes": [], "datasets": [
        {"path": "0", "coordinateTransformations": [{"scale": [1, 1, 1]}]}]}]}
    e = from_ome(z, "0")
    assert e.status == UNKNOWN, "scale [1,1,1] was read as a 1 um pitch"
    assert "absence of a declaration" in e.detail["why"]


def _relative_pyramid():
    """Level 0 scale [1,1,1]; level 5 scale 32: relative factors, not microns."""
    return {"multiscales": [{"axes": [], "datasets": [
        {"path": "0", "coordinateTransformations": [{"scale": [1, 1, 1]}]},
        {"path": "5", "coordinateTransformations": [{"scale": [32, 32, 32]}]}]}]}


def t_relative_multiscale_factor_is_not_a_pitch():
    e = from_ome(_relative_pyramid(), "5")
    assert e.status == UNKNOWN and e.pitch_um_yx is None, e
    assert e.detail["relative_factor_yx"] == [32.0, 32.0]


def t_level_pitch_is_level0_pitch_times_relative_factor():
    e = from_ome(_relative_pyramid(), "5", level0_pitch_um=8.64)
    assert e.status == INFERRED, e
    assert abs(e.pitch_um_yx[0] - 8.64 * 32) < 1e-9 and abs(e.pitch_um_yx[1] - 8.64 * 32) < 1e-9


def t_physical_level0_scale_keeps_levels_physical():
    z = {"multiscales": [{"axes": [], "datasets": [
        {"path": "0", "coordinateTransformations": [{"scale": [2.4, 2.4, 2.4]}]},
        {"path": "2", "coordinateTransformations": [{"scale": [9.6, 9.6, 9.6]}]}]}]}
    e = from_ome(z, "2", level0_pitch_um=99.0)
    assert e.status == VERIFIED and abs(e.pitch_um_yx[0] - 9.6) < 1e-9, e


def t_meta_scale_is_not_microns():
    e = from_segment_meta({"format": "tifxyz", "scale": [0.05, 0.05]}, (1280, 960))
    assert e.status == UNKNOWN, "a tifxyz sampling density was reported as a pitch"
    assert e.detail["voxels_per_grid_step"] == 20.0
    assert "not a pitch in microns" in e.detail["note"]


def t_source_identity_is_exact_or_nothing():
    known = {"abc": {"pitch_um_yx": [2.399, 2.399], "name": "S3 level 0"}}
    assert from_source_identity("abc", known).status == VERIFIED
    assert from_source_identity("abd", known).status == UNKNOWN


def t_pyramid_ratio_needs_an_anchor_and_controls():
    assert from_pyramid_relation(4.0, None, True).status == UNKNOWN
    assert from_pyramid_relation(4.0, (2.399, 2.399), False).status == UNKNOWN
    e = from_pyramid_relation(4.0, (2.399, 2.399), True)
    assert e.status == VERIFIED and abs(e.pitch_um_yx[0] - 9.596) < 1e-9, e


def t_hierarchy_prefers_stronger_evidence():
    weak = from_pyramid_relation(4.0, (2.399, 2.399), True)
    strong = from_ome({"multiscales": [{"axes": [], "datasets": [
        {"path": "0", "coordinateTransformations": [{"scale": [1.5, 1.5, 1.5]}]}]}]}, "0")
    r = resolve(weak, strong)
    assert r["method"] == "OME_TRANSFORM" and abs(r["pitch_um_yx"][0] - 1.5) < 1e-9, r
    assert len(r["evidence"]) == 2, "the losing evidence was not reported"


def t_unknown_stays_unknown():
    r = resolve(from_ome({}, "0"), from_segment_meta({}, None))
    assert r["status"] == UNKNOWN and r["method"] == "NONE", r
    assert "stays unknown" in r["basis"]


def t_filename_inference_is_recorded_and_refused():
    r = refuse_filename_inference("fixture-seg-A_2um_try2")
    assert r["filename_hint_um"] == 2.0 and r["used"] is False
    assert "not in the hierarchy" in r["why"]



def t_grid_step_recovers_a_synthetic_spacing():
    x, y, z = _synthetic(20.0)
    s = tifxyz_grid_step(x, y, z)
    assert abs(s["y"][0] - 20.0) < 1e-6 and abs(s["x"][0] - 20.0) < 1e-6, s


def t_grid_step_is_robust_to_a_tear():
    x, y, z = _synthetic(20.0)
    z = z.copy()
    z[30:, :] += 5000.0
    s = tifxyz_grid_step(x, y, z)
    assert abs(s["y"][0] - 20.0) < 1e-6, "a single tear moved the median: %s" % (s,)


def t_tifxyz_needs_a_voxel_size_for_microns():
    x, y, z = _synthetic(20.0)
    e = from_tifxyz(x, y, z, voxel_um=None, render_shape_yx=(64 * 20, 48 * 20),
                    grid_shape_yx=(64, 48), meta_scale=0.05)
    assert e.status == UNKNOWN and e.pitch_voxels_yx is not None, e
    assert "separate evidence" in e.detail["why"]


def t_tifxyz_is_INFERRED_not_VERIFIED():
    x, y, z = _synthetic(20.0)
    e = from_tifxyz(x, y, z, voxel_um=2.399, render_shape_yx=(64 * 20, 48 * 20),
                    grid_shape_yx=(64, 48), meta_scale=0.05)
    assert e.status == INFERRED, "a chain with a supplied voxel size was called VERIFIED"
    assert abs(e.pitch_um_yx[0] - 2.399) < 1e-6, e


def t_non_integer_render_ratio_refused():
    """A ratio far from any whole number is refused as a ratio, not as an anisotropy."""
    x, y, z = _synthetic(20.0)
    e = from_tifxyz(x, y, z, voxel_um=2.399, render_shape_yx=(998, 749),
                    grid_shape_yx=(64, 48), meta_scale=0.05)
    ro = e.detail["render_over_grid"]
    assert abs(ro[0] - ro[1]) / max(ro) < 0.01, ("the fixture must be isotropic or it tests "
                                                 "the wrong guard: %s" % ro)
    assert e.status == UNKNOWN and "too far to be boundary cropping" in e.detail["why"], e


def t_near_integer_ratio_accepted_as_boundary_crop():
    """Real renders miss the integer by a few grid rows."""
    x, y, z = _synthetic(20.0, shape=(1304, 1064))
    e = from_tifxyz(x, y, z, voxel_um=2.399,
                    render_shape_yx=(25984, 21248), grid_shape_yx=(1304, 1064),
                    meta_scale=0.05)
    assert e.status == INFERRED, e
    ro = e.detail["render_over_grid_yx"]
    assert abs(ro[0] - 20) > 1e-3, "test is not adversarial: the ratio must NOT be exact"
    assert e.detail["integer_upsample_yx"] == [20, 20], e.detail
    for got in e.pitch_um_yx:
        assert abs(got - 2.399) / 2.399 < 0.01, got


def t_integer_not_measured_ratio_is_used():
    """Rounding to the whole number is more accurate than dividing by a cropped ratio."""
    x, y, z = _synthetic(20.0, shape=(1304, 1064))
    e = from_tifxyz(x, y, z, voxel_um=2.399,
                    render_shape_yx=(25984, 21248), grid_shape_yx=(1304, 1064),
                    meta_scale=0.05)
    by_integer = 20.0 / 20
    by_ratio = 20.0 / (25984 / 1304)
    assert abs(e.pitch_voxels_yx[0] - by_integer) < 1e-9, e.pitch_voxels_yx
    assert abs(e.pitch_voxels_yx[0] - by_ratio) > 1e-6, "the cropped ratio was used"


def t_axes_that_imply_different_render_scales_are_refused():
    """SABOTAGE, and the exact signature upstream measures."""
    x, y, z = _synthetic(20.0, shape=(1304, 1064))
    e = from_tifxyz(x, y, z, voxel_um=2.399,
                    render_shape_yx=(26080, 21280),
                    grid_shape_yx=(1304, 1064), meta_scale=0.05)
    assert e.status == INFERRED, ("the isotropic control must pass, or the sabotage below "
                                  "proves nothing: %s" % e)
    bad = from_tifxyz(x, y, z, voxel_um=2.399,
                      render_shape_yx=(26080, 20440),
                      grid_shape_yx=(1304, 1064), meta_scale=0.05)
    assert bad.status == UNKNOWN, bad
    assert bad.detail["canvas_provenance"] == CANVAS_PROVENANCE_UNRESOLVED, bad.detail
    assert "differ by" in bad.detail["why"], bad.detail


def t_axis_tolerance_scales_with_grid_size():
    """The allowance is DERIVED from croppable cells, not a fixed percentage."""
    big, _, _ = _synthetic(20.0, shape=(1304, 1064))
    e_big = from_tifxyz(*_synthetic(20.0, shape=(1304, 1064)), voxel_um=2.399,
                        render_shape_yx=(26080, 21280), grid_shape_yx=(1304, 1064),
                        meta_scale=0.05)
    e_small = from_tifxyz(*_synthetic(20.0, shape=(354, 331)), voxel_um=2.399,
                          render_shape_yx=(7080, 6620), grid_shape_yx=(354, 331),
                          meta_scale=0.05)
    a_big = e_big.detail["axis_allowed_by_cropping"]
    a_small = e_small.detail["axis_allowed_by_cropping"]
    assert a_small > a_big * 3, ("a 354x331 grid must tolerate far more than a 1304x1064 one "
                                 "(%.5f vs %.5f)" % (a_small, a_big))


def t_anisotropic_grid_step_is_refused_as_not_one_pitch():
    """The OTHER half, and a different failure from the one above."""
    import numpy as np
    gy, gx = 400, 400
    ay = np.arange(gy)[:, None] * 20.6
    ax = np.arange(gx)[None, :] * 20.0
    x = np.broadcast_to(ax, (gy, gx)).astype(np.float64).copy()
    y = np.broadcast_to(ay, (gy, gx)).astype(np.float64).copy()
    z = np.zeros((gy, gx))
    e = from_tifxyz(x, y, z, voxel_um=2.399,
                    render_shape_yx=(gy * 20, gx * 20), grid_shape_yx=(gy, gx),
                    meta_scale=0.05)
    assert e.status == UNKNOWN, e
    assert "not one pitch" in e.detail["why"], e.detail
    assert e.detail["pitch_isotropy_deviation"] > 0.01, e.detail


def t_exact_four_times_rendering_reproduces():
    """The 103 volumes upstream reproduces at exactly 4.0 are the easy case, and must stay easy."""
    e = from_tifxyz(*_synthetic(4.0, shape=(500, 400)), voxel_um=2.399,
                    render_shape_yx=(2000, 1600), grid_shape_yx=(500, 400), meta_scale=0.25)
    assert e.status == INFERRED, e
    assert e.detail["integer_upsample_yx"] == [4, 4], e.detail


def t_float32_lround_is_the_renderers_arithmetic():
    """0.05 is not representable in binary, and the renderer divides by the float32 value."""
    import numpy as np
    from argus.core.pitch import render_canvas
    assert render_canvas(354, 4.2542, 0.05) == int(
        np.floor(354 * (4.2542 / np.float32(0.05)) + 0.5))
    assert render_canvas(1, 0.5 * float(np.float32(0.05)), 0.05) == 1
    assert round(0.5) == 0, "the control is wrong: Python rounds half to even"
    assert render_canvas(0, 4.0, 0.05) == 1, "the formula floors at 1"


def t_declared_canvas_outranks_a_mesh_inference():
    """RANK A."""
    from argus.core.pitch import canvas_authority, canvas_from_zattrs
    z = {"multiscales": [{"datasets": [{"path": "0", "shape": [1, 31000, 29000]}]}]}
    assert canvas_from_zattrs(z)["canvas_yx"] == [31000, 29000]
    ok = canvas_authority(z, (31000, 29000))
    assert ok["status"] == "RESOLVED" and ok["agreement"] is True, ok
    bad = canvas_authority(z, (31000, 28930))
    assert bad["status"] == CANVAS_PROVENANCE_UNRESOLVED, bad
    assert bad["residual_px_yx"] == [0, -70], bad
    assert bad["canvas_yx"] == [31000, 29000], "the declaration must survive the disagreement"
    none = canvas_authority({}, (31000, 29000))
    assert none["status"] == CANVAS_PROVENANCE_UNRESOLVED, none


def t_nothing_is_resized_to_make_dimensions_agree():
    """RULE D, structurally."""
    import ast
    import inspect
    from argus.core import pitch as P
    fn = ast.parse(inspect.getsource(P.canvas_authority).lstrip()).body[0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    called = set()
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call):
                f = n.func
                called.add(getattr(f, "id", None) or getattr(f, "attr", None))
    banned = {"resize", "crop", "pad", "cbrt", "reshape", "clip"}
    assert not (called & banned), "canvas_authority calls %s" % sorted(called & banned)
    z = {"canvas_size": [31000, 29000]}
    out = P.canvas_authority(z, (30000, 28000))
    assert out["canvas_yx"] == [31000, 29000]
    assert out["mesh_canvas_yx"] == [30000, 28000]
    assert "never_adjusted" in out


def t_meta_disagreement_refused():
    """meta.json says 20 voxels per step; the geometry says 40."""
    x, y, z = _synthetic(40.0)
    e = from_tifxyz(x, y, z, voxel_um=2.399, render_shape_yx=(64 * 20, 48 * 20),
                    grid_shape_yx=(64, 48), meta_scale=0.05)
    assert e.status == UNKNOWN and "disagrees with meta.json" in e.detail["why"], e


def t_bbox_identifies_the_only_volume_that_contains_it():
    bbox = [[1000, 1000, 1000], [18000, 17000, 28000]]
    cands = {"small-45um": [3000, 2000, 2000], "big-2.4um": [30000, 20000, 20000]}
    r = identify_volume_by_bbox(bbox, cands)
    assert r["status"] == "IDENTIFIED" and r["volume"] == "big-2.4um", r


def t_bbox_ambiguity_is_reported_not_broken():
    """Several scrolls publish several pitches."""
    bbox = [[0, 0, 0], [100, 100, 100]]
    cands = {"a": [500, 500, 500], "b": [900, 900, 900]}
    r = identify_volume_by_bbox(bbox, cands)
    assert r["status"] == "AMBIGUOUS" and set(r["survivors"]) == {"a", "b"}, r


def t_bbox_outside_every_candidate_is_unknown():
    r = identify_volume_by_bbox([[0, 0, 0], [9e9, 9e9, 9e9]], {"a": [10, 10, 10]})
    assert r["status"] == "UNKNOWN" and "large enough" in r["why"], r


def t_method_order_is_the_declared_hierarchy():
    assert METHODS[:5] == ("OME_TRANSFORM", "SEGMENT_META", "SOURCE_IDENTITY",
                           "PYRAMID_RELATION", "TIFXYZ_GEOMETRY"), METHODS


def main() -> int:
    tests = [(k[2:], v) for k, v in sorted(globals().items())
             if k.startswith("t_") and callable(v)]
    for name, fn in tests:
        check(name.replace("_", " "), fn)
    print("pitch: %d/%d passed" % (len(tests) - len(FAILURES), len(tests)))
    print("selftest: %d/%d passed" % (len(tests) - len(FAILURES), len(tests)))
    return 1 if FAILURES else 0



def test_pitch_selftests():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
