"""Bounded retry of transient remote chunk reads in ink inference (#1666)."""

from __future__ import annotations

import aiohttp
import numpy as np
import pytest
import torch
import zarr

from vesuvius.ink_detection import volume_io
from vesuvius.ink_detection.config import InkConfig
from vesuvius.ink_detection.inference.infer import FlatPatchReader, main
from vesuvius.ink_detection.models.model import make_model
from vesuvius.ink_detection.volume_io import (
    READ_MAX_ATTEMPTS,
    RemoteReadError,
    read_bbox_with_padding,
    read_with_retry,
)

from .test_model_foundation import _config_mapping


def _truncated() -> Exception:
    return aiohttp.ClientPayloadError(
        "Response payload is not completed: <ContentLengthError: 400, "
        "message='Not enough data to satisfy content length header "
        "(received 173631 of 507920 bytes).'>"
    )


class FlakyArray:
    """Array stand-in whose first ``failures`` reads raise ``error``."""

    def __init__(self, data, failures, error_factory=_truncated):
        self.data = data
        self.shape = data.shape
        self.dtype = data.dtype
        self.failures = failures
        self.error_factory = error_factory
        self.calls = 0

    def __getitem__(self, key):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error_factory()
        return self.data[key]


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(volume_io.time, "sleep", recorded.append)
    return recorded


def _volume():
    return np.arange(4 * 6 * 8, dtype=np.uint8).reshape(4, 6, 8)


def _reader(array, depth_axis_first=True):
    reader = FlatPatchReader(
        input_path="https://example.invalid/segment.zarr",
        resolution="0",
        depth_axis_first=depth_axis_first,
        height=6,
        width=8,
        layer_indices=np.arange(4),
        output_depth=4,
        preprocessing="divide_255",
    )
    reader._array = array
    return reader


def test_flat_reader_recovers_after_two_truncated_reads(sleeps):
    data = _volume()
    array = FlakyArray(data, failures=2)
    block = _reader(array)._read_raw(0, 6, 0, 8)
    assert array.calls == 3
    assert np.array_equal(block, np.transpose(data, (1, 2, 0)))
    assert sleeps == [0.5, 1.0]


def test_bbox_reader_recovers_after_two_truncated_reads(sleeps):
    data = _volume()
    array = FlakyArray(data, failures=2)
    out, valid = read_bbox_with_padding(array, (0, 0, 0, 4, 6, 8))
    assert array.calls == 3
    assert np.array_equal(out, data)
    assert valid is not None


def test_exhaustion_is_a_terminal_error_naming_read_and_attempts(sleeps):
    array = FlakyArray(_volume(), failures=10**6)
    with pytest.raises(RemoteReadError) as info:
        _reader(array)._read_raw(0, 6, 0, 8)
    message = str(info.value)
    assert array.calls == READ_MAX_ATTEMPTS
    assert f"after {READ_MAX_ATTEMPTS} attempts" in message
    assert "y=0:6 x=0:8" in message and "segment.zarr" in message
    assert "173631 of 507920" in message
    assert isinstance(info.value.__cause__, aiohttp.ClientPayloadError)
    assert len(sleeps) == READ_MAX_ATTEMPTS - 1
    assert max(sleeps) <= volume_io.READ_BACKOFF_MAX_SECONDS


@pytest.mark.parametrize(
    "error_factory",
    (
        # Bounds message that also contains " 500" and "ssl": must not be
        # mistaken for a transient failure by the text markers.
        lambda: IndexError("index 500 out of bounds for ssl axis"),
        lambda: ValueError("invalid config 503 timeout"),
        lambda: KeyError("0/0/0/0"),
        lambda: FileNotFoundError("missing chunk 502"),
        lambda: PermissionError("403 forbidden ssl"),
        lambda: RuntimeError("plain bug"),
    ),
)
def test_deterministic_errors_are_attempted_exactly_once(
    sleeps, error_factory
):
    for read in (
        lambda a: _reader(a)._read_raw(0, 6, 0, 8),
        lambda a: read_bbox_with_padding(a, (0, 0, 0, 4, 6, 8)),
    ):
        array = FlakyArray(_volume(), failures=10**6, error_factory=error_factory)
        with pytest.raises(type(error_factory())):
            read(array)
        assert array.calls == 1
    assert sleeps == []


def test_local_reads_are_unchanged(tmp_path, sleeps):
    data = _volume()
    path = tmp_path / "local.zarr"
    local = zarr.open(
        str(path), mode="w", shape=data.shape, chunks=data.shape,
        dtype="u1", zarr_format=2,
    )
    local[:] = data
    block = _reader(local)._read_raw(0, 6, 0, 8)
    assert np.array_equal(block, np.transpose(data, (1, 2, 0)))
    out, _ = read_bbox_with_padding(local, (-1, 0, 0, 5, 6, 8), fill_value=9)
    assert np.array_equal(out[1:5], data) and (out[0] == 9).all()
    assert sleeps == []
    assert read_with_retry(lambda: 7, description="x") == 7


def test_failed_inference_writes_no_output(tmp_path, monkeypatch, sleeps):
    config_mapping = _config_mapping("vesuvius_unet_2p5d", depth=3, side=16)
    config_mapping["image_normalization"] = "robust_mad"
    config = InkConfig.from_mapping(config_mapping)
    model = make_model(config)
    checkpoint = tmp_path / "model.pth"
    torch.save({"config": config_mapping, "model": model.state_dict()}, checkpoint)
    input_path = tmp_path / "surface.zarr"
    array = zarr.open(
        str(input_path), mode="w", shape=(3, 16, 16), chunks=(3, 16, 16),
        dtype="u1", zarr_format=2,
    )
    array[:] = 1

    def always_truncated(self, y0, y1, x0, x1):
        raise _truncated()

    monkeypatch.setattr(FlatPatchReader, "_read_raw_once", always_truncated)
    output = tmp_path / "out" / "prediction.tif"
    with pytest.raises(RemoteReadError, match="after 4 attempts"):
        main([
            str(input_path), str(checkpoint), str(output),
            "--workers", "0", "--no-compile", "--blend-mode", "constant",
        ])
    assert not output.exists()
    assert not list(output.parent.glob("*")) if output.parent.exists() else True
