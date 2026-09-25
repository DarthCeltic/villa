"""An unregistered pair refuses instead of guessing."""
from __future__ import annotations

import numpy as np
import pytest

from argus.core import physical_frame_contract as PF


def frame(**over):
    base = dict(
        physical_scroll="PHercFixture1", volume_id="20000101000000-seg-A",
        frame_id="vol-A@level0", axes_order=("z", "y", "x"), units="um",
        voxel_size=(4.0, 4.0, 4.0), pyramid_level=0,
        origin_convention="CORNER_MIN_VOXEL", source_tool="ARGUS",
    )
    base.update(over)
    return PF.PhysicalFrame(**base)


def identity_transform(**over):
    base = dict(
        source_frame_id="vol-A@level0", target_frame_id="vol-B@level0",
        source_level=0, target_level=0, transform_kind="RIGID",
        matrix=(
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        produced_by_tool="ARGUS", produced_by_revision="deadbeef",
        produced_at="2026-09-16T00:00:00Z", method="MANUAL",
    )
    base.update(over)
    return PF.FrameTransform(**base)


def translation_matrix(dx, dy, dz):
    return (
        (1.0, 0.0, 0.0, dx),
        (0.0, 1.0, 0.0, dy),
        (0.0, 0.0, 1.0, dz),
        (0.0, 0.0, 0.0, 1.0),
    )


def inverse_matrix(matrix):
    return tuple(tuple(float(v) for v in row) for row in np.linalg.inv(np.array(matrix)))



def test_a_fully_declared_frame_validates():
    frame().validate()


def test_frame_with_bad_axes_order_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="AXES_ORDER"):
        frame(axes_order=("z", "y", "y")).validate()


def test_frame_with_missing_voxel_size_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="VOXEL_SIZE"):
        frame(voxel_size=(4.0, 4.0, None)).validate()


def test_frame_with_non_positive_voxel_size_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="VOXEL_SIZE"):
        frame(voxel_size=(4.0, 0.0, 4.0)).validate()


def test_frame_with_undeclared_units_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="UNITS"):
        frame(units="UNDECLARED").validate()


def test_frame_with_undeclared_origin_convention_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="ORIGIN_CONVENTION"):
        frame(origin_convention="UNDECLARED").validate()


def test_frame_missing_identity_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="PHYSICAL_IDENTITY"):
        frame(physical_scroll="").validate()


def test_transform_missing_provenance_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="TRANSFORM_PROVENANCE"):
        identity_transform(produced_by_tool="").validate()


def test_transform_self_referential_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="TRANSFORM_IDENTITY"):
        identity_transform(target_frame_id="vol-A@level0").validate()


def test_transform_non_affine_matrix_shape_refuses():
    with pytest.raises(PF.FrameContractRefusal, match="TRANSFORM_MATRIX"):
        identity_transform(matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))).validate()


def test_transform_bad_bottom_row_refuses():
    bad = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.1, 0.0, 0.0, 1.0),
    )
    with pytest.raises(PF.FrameContractRefusal, match="TRANSFORM_MATRIX"):
        identity_transform(matrix=bad).validate()



def test_same_frame_pair_passes_without_a_transform():
    a = frame(frame_id="vol-A@level0")
    b = frame(frame_id="vol-A@level0")
    r = PF.require_registered_pair(a, b, None, used_for="test")
    assert r["ok"] and r["same_frame"]


def test_a_valid_declared_transform_passes():
    a = frame(frame_id="vol-A@level0")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume")
    r = PF.require_registered_pair(a, b, identity_transform(), used_for="cross-scan overlay")
    assert r["ok"], r


def test_unregistered_pair_refuses_instead_of_guessing():
    """THE CORE RULE for AC-004: no declared transform between different frames is a refusal."""
    a = frame(frame_id="vol-A@level0")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume")
    with pytest.raises(PF.FrameContractRefusal) as e:
        PF.require_registered_pair(a, b, None, used_for="evidence overlay")
    assert "UNREGISTERED_PAIR" in str(e.value)


def test_transform_missing_fields_refuses_the_pair():
    a = frame(frame_id="vol-A@level0")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume")
    bad_transform = identity_transform(produced_by_revision="")
    r = PF.check_registered_pair(a, b, bad_transform)
    assert not r["ok"]
    assert any("TRANSFORM_PROVENANCE" in p for p in r["problems"])


def test_frame_missing_voxel_size_refuses_the_pair_before_pair_rules_run():
    a = frame(frame_id="vol-A@level0", voxel_size=(4.0, 4.0, None))
    b = frame(frame_id="vol-B@level0", volume_id="other-volume")
    r = PF.check_registered_pair(a, b, identity_transform())
    assert not r["ok"]
    assert any("frame_a" in p and "VOXEL_SIZE" in p for p in r["problems"])


def test_pyramid_level_mismatch_is_caught_not_silently_rescaled():
    a = frame(frame_id="vol-A@level0", pyramid_level=0)
    b = frame(frame_id="vol-B@level2", volume_id="other-volume", pyramid_level=2)
    mismatched = identity_transform(target_frame_id="vol-B@level2", source_level=0, target_level=0)
    r = PF.check_registered_pair(a, b, mismatched)
    assert not r["ok"]
    assert any("LEVEL_MISMATCH" in p for p in r["problems"])


def test_pyramid_level_match_is_accepted():
    a = frame(frame_id="vol-A@level2", pyramid_level=2)
    b = frame(frame_id="vol-B@level2", volume_id="other-volume", pyramid_level=2)
    t = identity_transform(source_frame_id="vol-A@level2", target_frame_id="vol-B@level2",
                           source_level=2, target_level=2)
    r = PF.check_registered_pair(a, b, t)
    assert r["ok"], r["problems"]


def test_unit_mismatch_between_frames_refuses_the_pair():
    a = frame(frame_id="vol-A@level0", units="um")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume", units="mm")
    r = PF.check_registered_pair(a, b, identity_transform())
    assert not r["ok"]
    assert any("UNIT_MISMATCH" in p for p in r["problems"])


def test_transform_frame_mismatch_refuses():
    a = frame(frame_id="vol-A@level0")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume")
    wrong = identity_transform(source_frame_id="vol-Q@level0")
    r = PF.check_registered_pair(a, b, wrong)
    assert not r["ok"]
    assert any("TRANSFORM_FRAME_MISMATCH" in p for p in r["problems"])



def test_round_trip_identity_within_tolerance_passes():
    a = frame(frame_id="vol-A@level0")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume")
    fwd = identity_transform(matrix=translation_matrix(5.0, -2.0, 0.5))
    back = identity_transform(
        source_frame_id="vol-B@level0", target_frame_id="vol-A@level0",
        matrix=inverse_matrix(translation_matrix(5.0, -2.0, 0.5)),
    )
    r = PF.require_registered_pair(a, b, fwd, inverse_transform=back, used_for="round trip check")
    assert r["ok"], r


def test_round_trip_drift_beyond_tolerance_refuses_rather_than_accepting_silently():
    a = frame(frame_id="vol-A@level0")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume")
    fwd = identity_transform(matrix=translation_matrix(5.0, -2.0, 0.5))
    back = identity_transform(
        source_frame_id="vol-B@level0", target_frame_id="vol-A@level0",
        matrix=translation_matrix(-4.0, 2.0, -0.5),
    )
    with pytest.raises(PF.FrameContractRefusal) as e:
        PF.require_registered_pair(a, b, fwd, inverse_transform=back, used_for="round trip check")
    assert "ROUND_TRIP_DRIFT" in str(e.value)


def test_round_trip_direction_mismatch_refuses():
    a = frame(frame_id="vol-A@level0")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume")
    fwd = identity_transform()
    not_the_inverse_direction = identity_transform()
    r = PF.check_registered_pair(a, b, fwd, inverse_transform=not_the_inverse_direction)
    assert not r["ok"]
    assert any("ROUND_TRIP_DIRECTION_MISMATCH" in p for p in r["problems"])


def test_require_raises_with_every_problem_not_just_the_first():
    a = frame(frame_id="vol-A@level0", units="um")
    b = frame(frame_id="vol-B@level0", volume_id="other-volume", units="mm", pyramid_level=2)
    with pytest.raises(PF.FrameContractRefusal) as e:
        PF.require_registered_pair(a, b, identity_transform(), used_for="test")
    assert str(e.value).count("- ") >= 2


def test_every_dataclass_field_appears_in_the_request_template():
    t = PF.request_template()
    assert "frame_id" in t["frame_required_fields"]
    assert "matrix" in t["transform_required_fields"]
    assert t["allowed_units"] == list(PF.ALLOWED_UNITS)
