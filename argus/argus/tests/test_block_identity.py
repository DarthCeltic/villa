"""Each test is a way a block could be reused when it is not the same computation."""
from __future__ import annotations

import json

import numpy as np
import pytest

from argus.core import block_identity as BI

PLAN = {"crop": 64, "stride": 32, "fade": 8, "tta": [1.0, 2.0],
        "block": 160, "halo": 24, "level": 1}
COORDS = (100, 740, 200, 840)


def _key(**over):
    base = dict(acquisition_manifest_sha256="a" * 64, checkpoint_sha256="b" * 64,
                dependency_sha256={"metrics": "c" * 64, "resample": "d" * 64},
                label_identity_sha256="e" * 64, orientation="forward", pitch_um=1.0,
                plan=dict(PLAN), compression_declaration="UNRESOLVED")
    base.update(over)
    return BI.BlockKey(**base)


def _arrays(shape=(64, 64), p_fill=0.25, c_fill=True):
    return np.full(shape, p_fill, np.float32), np.full(shape, c_fill, bool)


def _write(tmp_path, name="b_100_200.npz", key=None, coords=COORDS, **kw):
    k = key or _key()
    p_arr, c_arr = _arrays(**kw)
    return BI.write_block_atomic(tmp_path / name, p_arr=p_arr, c_arr=c_arr,
                                 coords=coords, key=k), k



def test_a_committed_block_is_accepted(tmp_path):
    p, k = _write(tmp_path)
    assert BI.verify_block(p, key=k, expected_coords=COORDS).accepted
    assert BI.marker_path(p).is_file()


def test_the_marker_binds_the_actual_block_bytes(tmp_path):
    p, k = _write(tmp_path)
    m = json.loads(BI.marker_path(p).read_text(encoding="utf-8"))
    assert m["block_sha256"] == BI.sha_path(p)
    assert m["committed"] is True



def test_a_block_with_no_marker_is_incomplete(tmp_path):
    """A crash between promoting the block and writing the marker lands exactly here."""
    p, k = _write(tmp_path)
    BI.marker_path(p).unlink()
    v = BI.verify_block(p, key=k, expected_coords=COORDS)
    assert not v.accepted and "no commit marker" in v.reasons[0]


def test_a_marker_with_no_block_is_refused(tmp_path):
    p, k = _write(tmp_path)
    p.unlink()
    assert not BI.verify_block(p, key=k, expected_coords=COORDS).accepted


def test_a_copied_marker_cannot_validate_a_foreign_block(tmp_path):
    """THE DEFECT."""
    good, k = _write(tmp_path, "good.npz")
    other, _ = _write(tmp_path, "other.npz", p_fill=0.9)
    BI.marker_path(other).write_text(BI.marker_path(good).read_text(encoding="utf-8"),
                                     encoding="utf-8")
    v = BI.verify_block(other, key=k, expected_coords=COORDS)
    assert not v.accepted and "copied marker" in " ".join(v.reasons)


def test_a_tampered_block_is_refused_without_a_caller_hash(tmp_path):
    """Content hashing is no longer optional -- the marker always carries one."""
    p, k = _write(tmp_path)
    p.write_bytes(p.read_bytes() + b"\x00")
    v = BI.verify_block(p, key=k, expected_coords=COORDS)
    assert not v.accepted and "does not match its commit marker" in " ".join(v.reasons)


def test_a_stale_block_beside_a_fresh_marker_is_refused(tmp_path):
    good, k = _write(tmp_path, "b.npz")
    stale, _ = _write(tmp_path, "stale.npz", p_fill=0.75)
    good.write_bytes(stale.read_bytes())
    assert not BI.verify_block(good, key=k, expected_coords=COORDS).accepted


def test_no_partial_or_lock_survives_a_successful_write(tmp_path):
    _write(tmp_path)
    assert not list(tmp_path.glob("*.partial*"))
    assert not list(tmp_path.glob("*.lock"))
    assert not list(tmp_path.glob("*.tmp"))


def test_a_concurrent_writer_is_refused_not_interleaved(tmp_path):
    dst = tmp_path / "b.npz"
    lock = dst.with_name(dst.name + ".lock")
    lock.write_text("other-writer", encoding="utf-8")
    p_arr, c_arr = _arrays()
    with pytest.raises(BI.BlockLocked):
        BI.write_block_atomic(dst, p_arr=p_arr, c_arr=c_arr, coords=COORDS, key=_key())
    lock.unlink()



def test_a_c_array_covering_nothing_is_refused(tmp_path):
    """c decides which pixels count as scored."""
    p_arr, c_arr = _arrays()
    with pytest.raises(BI.BlockRefused):
        BI.write_block_atomic(tmp_path / "b.npz", p_arr=p_arr,
                              c_arr=np.zeros_like(c_arr), coords=COORDS, key=_key())


def test_a_non_boolean_c_is_refused(tmp_path):
    p_arr, _ = _arrays()
    with pytest.raises(BI.BlockRefused):
        BI.write_block_atomic(tmp_path / "b.npz", p_arr=p_arr,
                              c_arr=np.full(p_arr.shape, 7, np.uint8),
                              coords=COORDS, key=_key())


@pytest.mark.parametrize("p_fill", [-0.1, 1.5, float("nan"), float("inf")])
def test_p_must_be_a_finite_probability(tmp_path, p_fill):
    p_arr, c_arr = _arrays(p_fill=p_fill)
    with pytest.raises(BI.BlockRefused):
        BI.write_block_atomic(tmp_path / "b.npz", p_arr=p_arr, c_arr=c_arr,
                              coords=COORDS, key=_key())


def test_mismatched_p_and_c_shapes_are_refused(tmp_path):
    with pytest.raises(BI.BlockRefused):
        BI.write_block_atomic(tmp_path / "b.npz", p_arr=np.zeros((8, 8), np.float32),
                              c_arr=np.ones((4, 4), bool), coords=COORDS, key=_key())


@pytest.mark.parametrize("coords", [(5, 5, 0, 10), (0, 10, 7, 7), (1, 2, 3)])
def test_degenerate_coords_are_refused(tmp_path, coords):
    p_arr, c_arr = _arrays()
    with pytest.raises(BI.BlockRefused):
        BI.write_block_atomic(tmp_path / "b.npz", p_arr=p_arr, c_arr=c_arr,
                              coords=coords, key=_key())


def test_wrong_coords_are_refused_because_coords_are_now_read(tmp_path):
    """Coordinates are part of the verified identity."""
    p, k = _write(tmp_path)
    v = BI.verify_block(p, key=k, expected_coords=(999, 1639, 200, 840))
    assert not v.accepted and any("coordinates differ" in r for r in v.reasons)



def test_a_swapped_orientation_is_refused(tmp_path):
    p, _ = _write(tmp_path)
    assert not BI.verify_block(p, key=_key(orientation="reversed"),
                               expected_coords=COORDS).accepted


def test_a_changed_checkpoint_is_refused(tmp_path):
    p, _ = _write(tmp_path)
    assert not BI.verify_block(p, key=_key(checkpoint_sha256="f" * 64),
                               expected_coords=COORDS).accepted


def test_a_changed_dependency_is_refused(tmp_path):
    p, _ = _write(tmp_path)
    assert not BI.verify_block(p, key=_key(dependency_sha256={"metrics": "c" * 64,
                                                              "resample": "9" * 64}),
                               expected_coords=COORDS).accepted


@pytest.mark.parametrize("field,value", [
    ("stride", 96), ("crop", 512), ("fade", 0), ("halo", 0), ("block", 320),
    ("tta", [1.0]), ("level", 0)])
def test_any_changed_plan_parameter_is_refused(tmp_path, field, value):
    p, _ = _write(tmp_path)
    plan = dict(PLAN)
    plan[field] = value
    assert not BI.verify_block(p, key=_key(plan=plan), expected_coords=COORDS).accepted


def test_a_changed_compression_declaration_is_refused(tmp_path):
    p, _ = _write(tmp_path)
    assert not BI.verify_block(p, key=_key(compression_declaration="ORIGINAL_UNCOMPRESSED"),
                               expected_coords=COORDS).accepted


def test_a_missing_block_is_refused(tmp_path):
    assert not BI.verify_block(tmp_path / "nope.npz", key=_key(),
                               expected_coords=COORDS).accepted


def test_the_key_hashes_meaning_not_formatting():
    a = _key(plan={"crop": 256, "stride": 128})
    b = _key(plan={"stride": 128, "crop": 256})
    assert a.sha256() == b.sha256()
    assert _key(plan={"crop": 256, "stride": 96}).sha256() != a.sha256()


def test_shape_and_dtype_mismatch_are_refused(tmp_path):
    p, k = _write(tmp_path)
    assert not BI.verify_block(p, key=k, expected_coords=COORDS,
                               expected_shape=(999, 999)).accepted
    assert not BI.verify_block(p, key=k, expected_coords=COORDS,
                               expected_dtype="float64").accepted
