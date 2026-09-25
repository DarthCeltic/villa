"""The preflight that refuses a job whose input region is empty."""
from __future__ import annotations

import json

import numpy as np
import pytest
import zarr

from argus.core import job_preflight as JP


def _array(tmp_path, fill):
    p = tmp_path / "vol.zarr"
    a = zarr.open(str(p), mode="w", shape=(16, 16, 16), dtype="uint8")
    a[:] = fill
    return p


def _spec(tmp_path, path, roi=None):
    return JP.JobSpec(name="t", input_path=str(path), roi=roi,
                      output_path=str(tmp_path / "out"))


def test_an_empty_region_is_refused_and_the_refusal_names_the_fraction(tmp_path):
    p = _array(tmp_path, 0)
    r = JP.check(_spec(tmp_path, p))
    assert r["ok"] is False
    assert any("EMPTY INPUT" in x and "0.0000%" in x for x in r["problems"]), r["problems"]
    with pytest.raises(JP.PreflightRefusal):
        JP.require(_spec(tmp_path, p))


def test_a_region_with_data_passes_the_support_check(tmp_path):
    p = _array(tmp_path, 7)
    s = JP.support_probe(str(p))
    assert s["probed"] and s["has_support"] and s["nonzero_fraction"] == 1.0


def test_support_is_measured_over_the_requested_roi_not_the_whole_array(tmp_path):
    """The 2026-09-11 mistake exactly: the array held data, the chosen ROI did not."""
    p = tmp_path / "vol.zarr"
    a = zarr.open(str(p), mode="w", shape=(16, 16, 16), dtype="uint8")
    a[:] = 0
    a[8:16, :, :] = 3
    assert JP.support_probe(str(p), (8, 16, 0, 16, 0, 16))["has_support"] is True
    empty = JP.support_probe(str(p), (0, 8, 0, 16, 0, 16))
    assert empty["has_support"] is False
    assert empty["nonzero_fraction"] == 0.0


def test_a_barely_populated_region_is_still_refused(tmp_path):
    """Not zero: a region that is 99.9% padding produces output that is about the padding."""
    p = tmp_path / "vol.zarr"
    a = zarr.open(str(p), mode="w", shape=(16, 16, 16), dtype="uint8")
    a[:] = 0
    a[0, 0, 0] = 1
    s = JP.support_probe(str(p), (0, 16, 0, 16, 0, 16))
    assert 0 < s["nonzero_fraction"] < JP.MIN_SUPPORT_FRACTION
    assert s["has_support"] is False


def test_a_zarr_group_is_refused_with_the_levels_it_actually_holds(tmp_path):
    """A loader that resolves a pyramid level for you gives a run on different data than the receipt names."""
    root = tmp_path / "pyr.zarr"
    g = zarr.open_group(str(root), mode="w")
    g.create_array("5", shape=(4, 4, 4), dtype="uint8")
    s = JP.support_probe(str(root))
    assert s["probed"] is False
    assert "GROUP" in s["why"] and "'5'" in s["why"]


def test_a_missing_input_is_a_problem_and_not_an_exception(tmp_path):
    r = JP.check(_spec(tmp_path, tmp_path / "nope.zarr"))
    assert r["ok"] is False
    assert any("does not exist" in x for x in r["problems"])


def test_input_identity_hashes_the_metadata_and_not_the_array(tmp_path):
    p = _array(tmp_path, 1)
    i = JP.input_identity(str(p))
    assert i["exists"] and i["metadata_sha256"] and len(i["metadata_sha256"]) == 64
    assert i["metadata_file"].endswith("zarr.json")


def test_absent_floors_report_themselves_absent_rather_than_inventing_a_number(tmp_path,
                                                                              monkeypatch):
    monkeypatch.setattr(JP, "FLOORS_PATH", tmp_path / "nothing.json")
    assert JP.floors()["measured"] is False


def test_a_floor_below_what_is_free_refuses_the_job(tmp_path, monkeypatch):
    f = tmp_path / "floors.json"
    f.write_text(json.dumps({"measured": True, "ram_floor_mb": 10 ** 9}), encoding="utf-8")
    monkeypatch.setattr(JP, "FLOORS_PATH", f)
    r = JP.check(_spec(tmp_path, _array(tmp_path, 5)))
    assert any("below the measured floor" in x for x in r["problems"]), r["problems"]


def test_the_shipped_floors_are_an_unmeasured_example():
    """The public build ships example floors that are not enforced until measured locally."""
    f = JP.floors()
    assert f.get("measured") is False, f
    assert f.get("measured_from"), f
    for k in ("ram_floor_mb", "disk_floor_mb", "vram_floor_mb"):
        assert isinstance(f.get(k), int) and f[k] > 0, (k, f)
