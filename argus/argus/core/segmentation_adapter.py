"""The canonical raw-CT -> 3-D surface entry point."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import re
import string
from typing import Any, Callable, Mapping

import numpy as np

from .surface_contract import SurfaceDeclaration, SurfaceRefusal, require
from . import paths

CONTRACT = "argus-raw-ct-segmentation-adapter-v1"
GENERATED_LOCALLY = "GENERATED_LOCALLY"
INVALID_COORDINATE_SENTINELS = ((-1, -1, -1),)


def official_inventory(physical_scroll: str | None = None) -> dict[str, Any]:
    """List the identity-bound official segmentation seals available to Workbench."""
    selected = physical_scroll.strip() if physical_scroll else None
    root = paths.find_artifact("official_segmentation_seals")
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for seal_path in sorted(root.glob("SEAL_*.json")):
            try:
                seal = json.loads(seal_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            input_path = str((seal.get("input") or {}).get("path") or "")
            marker_part = next((part for part in input_path.replace("\\", "/").split("/")
                                if part.lower().startswith("pherc")), None)
            marker_match = re.match(r"(PHerc[0-9A-Za-z]+)", marker_part or "", re.IGNORECASE)
            marker = marker_match.group(1) if marker_match else None
            if not marker:
                continue
            model = seal.get("model") or {}
            rows.append({
                "physical_scroll": marker,
                "seal": seal_path.name,
                "receipt_id": seal.get("id"),
                "model": model.get("repo_id"),
                "revision": model.get("revision"),
                "semantic_state": model.get("semantic_state"),
                "claim_ceiling": "MECHANICS_ONLY",
                "not_a_scientific_result": seal.get("not_a_scientific_result"),
                "source": str(seal_path),
            })
    matches = [row for row in rows if not selected or row["physical_scroll"].casefold() == selected.casefold()]
    return {
        "schema": "argus-segmentation-inventory-v1",
        "selected_scroll": selected,
        "available": bool(matches),
        "rows": matches,
        "registered_scrolls": sorted({row["physical_scroll"] for row in rows}),
        "why": None if matches else (
            f"No identity-matched official segmentation seal is registered for {selected}."
            if selected else "Select a physical scroll before opening segmentation output."
        ),
        "read_only": True,
        "claim_ceiling": "MECHANICS_ONLY",
    }


class SegmentationRefusal(ValueError):
    """Raised when a raw-CT segmentation result cannot be safely admitted."""


@dataclasses.dataclass(frozen=True)
class RawCTBinding:
    """Identity and physical contract for the raw volume supplied to a producer."""

    physical_scroll: str
    segment_id: str
    volume_id: str
    source_url: str
    array_path: str
    volume_shape: tuple[int, int, int]
    pitch_um: float
    energy_kev: float
    roi_origin: tuple[int, int, int] = (0, 0, 0)
    acquisition_id: str | None = None
    frame_id: str | None = None
    volume_sha256: str | None = None

    def validate(self) -> None:
        if not self.physical_scroll.strip() or not self.segment_id.strip():
            raise SegmentationRefusal("MATERIAL_IDENTITY: scroll and segment are required")
        if not self.volume_id.strip() or not self.source_url.strip() or not self.array_path.strip():
            raise SegmentationRefusal(
                "RAW_CT_DECLARATION: volume_id, source_url and array_path are required")
        if len(self.volume_shape) != 3 or any(int(v) <= 1 for v in self.volume_shape):
            raise SegmentationRefusal("RAW_CT_DECLARATION: volume_shape must be a 3-D extent")
        if len(self.roi_origin) != 3 or any(int(v) < 0 for v in self.roi_origin):
            raise SegmentationRefusal("RAW_CT_DECLARATION: roi_origin is invalid")
        if self.pitch_um <= 0 or self.energy_kev <= 0:
            raise SegmentationRefusal("RAW_CT_DECLARATION: pitch and energy must be positive")
        if not self.volume_sha256 or len(self.volume_sha256) != 64 or any(
                c not in string.hexdigits for c in self.volume_sha256):
            raise SegmentationRefusal("RAW_CT_DECLARATION: volume_sha256 must be full SHA-256")


@dataclasses.dataclass(frozen=True)
class AdmittedProducer:
    """An identified producer callable; names and revisions are part of provenance."""

    name: str
    revision: str
    licence: str
    run: Callable[[RawCTBinding], Mapping[str, Any]]

    def validate(self) -> None:
        if not self.name.strip() or not self.revision.strip() or not self.licence.strip():
            raise SegmentationRefusal(
                "PRODUCER_DECLARATION: name, immutable revision and licence are required")
        if not callable(self.run):
            raise SegmentationRefusal("PRODUCER_DECLARATION: producer is not callable")


@dataclasses.dataclass(frozen=True)
class SegmentationResult:
    declaration: SurfaceDeclaration
    validity: np.ndarray
    arrays: dict[str, np.ndarray]
    receipt: dict[str, Any]


def _sha256_array(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def _as_array(output: Mapping[str, Any], key: str) -> np.ndarray:
    value = output.get(key)
    if value is None:
        raise SegmentationRefusal("SEGMENTATION_OUTPUT_INCOMPLETE: missing %s" % key)
    a = np.asarray(value)
    if a.ndim != 2:
        raise SegmentationRefusal("SEGMENTATION_OUTPUT_INVALID: %s must be 2-D" % key)
    if a.size == 0:
        raise SegmentationRefusal("SEGMENTATION_OUTPUT_INVALID: %s is empty" % key)
    if not np.issubdtype(a.dtype, np.number):
        raise SegmentationRefusal("SEGMENTATION_OUTPUT_INVALID: %s is not numeric" % key)
    return a


def _check_coordinates(binding: RawCTBinding, arrays: Mapping[str, np.ndarray], validity: np.ndarray) -> None:
    shape = arrays["z"].shape
    if any(a.shape != shape for a in arrays.values()):
        raise SegmentationRefusal("SEGMENTATION_OUTPUT_INVALID: coordinate planes differ in shape")
    if validity.shape != shape:
        raise SegmentationRefusal("SEGMENTATION_OUTPUT_INVALID: validity shape differs from coordinates")
    valid = validity.astype(bool)
    for name in ("z", "y", "x"):
        a = arrays[name].astype(np.float64, copy=False)
        if not np.isfinite(a).all():
            raise SegmentationRefusal(
                "SEGMENTATION_OUTPUT_INVALID: non-finite %s coordinate" % name)
        if name == "z":
            axis = 0
        elif name == "y":
            axis = 1
        else:
            axis = 2
        if ((a[valid] < 0) | (a[valid] >= binding.volume_shape[axis])).any():
            raise SegmentationRefusal(
                "SEGMENTATION_OUTPUT_INVALID: %s coordinate is outside the bound raw CT" % name)
    for sentinel in INVALID_COORDINATE_SENTINELS:
        sentinel_cells = (
            (arrays["z"] == sentinel[0]) & (arrays["y"] == sentinel[1])
            & (arrays["x"] == sentinel[2]) & valid)
        if sentinel_cells.any():
            raise SegmentationRefusal(
                "SEGMENTATION_OUTPUT_INVALID: sentinel %s appears in a valid cell" % (sentinel,))


def _write_once(path: pathlib.Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(path), flags)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        raise


def run(binding: RawCTBinding, producer: AdmittedProducer, *, output_dir: str | pathlib.Path | None = None) -> SegmentationResult:
    """Run one admitted producer and bind its result to the raw CT contract."""
    binding.validate()
    producer.validate()
    try:
        output = producer.run(binding)
    except SegmentationRefusal:
        raise
    except Exception as exc:
        raise SegmentationRefusal(
            "SEGMENTATION_PRODUCER_FAILED: %s: %s" % (type(exc).__name__, exc)) from exc
    if not isinstance(output, Mapping):
        raise SegmentationRefusal("SEGMENTATION_OUTPUT_INCOMPLETE: producer returned no mapping")

    coords = {name: _as_array(output, name) for name in ("z", "y", "x")}
    validity = np.asarray(output.get("validity"))
    if validity.ndim != 2 or validity.dtype.kind not in "biu":
        raise SegmentationRefusal(
            "SEGMENTATION_OUTPUT_INCOMPLETE: validity must be a 2-D integer/bool mask")
    _check_coordinates(binding, coords, validity)

    required = ("surface_id", "normal_orientation", "face", "face_established_by",
                "recto_shaved_or_missing", "source_licence", "redistribution_permitted",
                "generation_command", "coordinate_frame", "output_shape", "output_hashes")
    missing = [key for key in required if output.get(key) in (None, "")]
    if missing:
        raise SegmentationRefusal(
            "SURFACE_DECLARATION_INCOMPLETE: missing %s" % ", ".join(missing))
    coordinate_frame = str(output["coordinate_frame"]).strip()
    if coordinate_frame.upper() in {"", "UNKNOWN", "UNDECLARED"}:
        raise SegmentationRefusal(
            "SEGMENTATION_OUTPUT_INVALID: coordinate_frame must be explicit")
    output_shape = output["output_shape"]
    if (not isinstance(output_shape, (list, tuple)) or len(output_shape) != 2
            or any(not isinstance(v, (int, np.integer)) or int(v) <= 0 for v in output_shape)
            or tuple(int(v) for v in output_shape) != coords["z"].shape):
        raise SegmentationRefusal(
            "SEGMENTATION_OUTPUT_INVALID: output_shape must match the coordinate planes")
    if str(output["source_licence"]) != producer.licence:
        raise SegmentationRefusal(
            "PRODUCER_DECLARATION: output source_licence does not match producer licence")
    calculated_hashes = {
        "z": _sha256_array(coords["z"]),
        "y": _sha256_array(coords["y"]),
        "x": _sha256_array(coords["x"]),
        "validity": _sha256_array(validity),
    }
    output_hashes = output["output_hashes"]
    if not isinstance(output_hashes, Mapping):
        raise SegmentationRefusal("SEGMENTATION_OUTPUT_INVALID: output_hashes must be a mapping")
    for key, expected in calculated_hashes.items():
        actual = output_hashes.get(key)
        if not isinstance(actual, str) or len(actual) != 64 or any(
                c not in string.hexdigits for c in actual):
            raise SegmentationRefusal(
                "SEGMENTATION_OUTPUT_INVALID: output_hashes.%s must be full SHA-256" % key)
        if actual.lower() != expected:
            raise SegmentationRefusal(
                "SEGMENTATION_OUTPUT_INVALID: output_hashes.%s does not match output" % key)
    declaration = SurfaceDeclaration(
        physical_scroll=binding.physical_scroll,
        segment_id=binding.segment_id,
        surface_id=str(output["surface_id"]),
        ct_volume_id=binding.volume_id,
        tifxyz_path=str(output.get("tifxyz_path") or "<in-memory-produced-tifxyz>"),
        normal_orientation=str(output["normal_orientation"]),
        face=str(output["face"]),
        face_established_by=str(output["face_established_by"]),
        recto_shaved_or_missing=str(output["recto_shaved_or_missing"]),
        pitch_um=float(output.get("pitch_um", binding.pitch_um)),
        energy_kev=float(output.get("energy_kev", binding.energy_kev)),
        pyramid_level=int(output.get("pyramid_level", 0)),
        source_licence=str(output["source_licence"]),
        redistribution_permitted=str(output["redistribution_permitted"]),
        parent_geometry=output.get("parent_geometry"),
        generation_command=str(output["generation_command"]),
        hashes={
            "volume_sha256": binding.volume_sha256,
            "z_sha256": _sha256_array(coords["z"]),
            "y_sha256": _sha256_array(coords["y"]),
            "x_sha256": _sha256_array(coords["x"]),
            "validity_sha256": _sha256_array(validity),
        },
    )
    try:
        require(declaration)
    except SurfaceRefusal as exc:
        raise SegmentationRefusal("SURFACE_DECLARATION_REFUSED: %s" % exc) from exc

    receipt = {
        "contract": CONTRACT,
        "artifact_state": GENERATED_LOCALLY,
        "physical_scroll": binding.physical_scroll,
        "segment_id": binding.segment_id,
        "ct_volume_id": binding.volume_id,
        "source_url": binding.source_url,
        "array_path": binding.array_path,
        "roi_origin": list(binding.roi_origin),
        "volume_shape": list(binding.volume_shape),
        "acquisition_id": binding.acquisition_id,
        "frame_id": binding.frame_id,
        "producer": {"name": producer.name, "revision": producer.revision,
                     "licence": producer.licence},
        "coordinate_frame": coordinate_frame,
        "output_shape": list(output_shape),
        "output_hashes": dict(calculated_hashes),
        "surface": dataclasses.asdict(declaration),
        "array_shapes": {k: list(v.shape) for k, v in {**coords, "validity": validity}.items()},
        "claim_ceiling": "MECHANICS_ONLY",
        "scientific_qualification": "NOT_ESTABLISHED",
    }
    if output_dir is not None:
        _write_once(pathlib.Path(output_dir) / "SEGMENTATION_RECEIPT.json", receipt)
    return SegmentationResult(declaration=declaration, validity=validity,
                              arrays=dict(coords), receipt=receipt)


def selftest() -> bool:
    """Small sabotage suite used by the release profile; returns False on any missed refusal."""
    binding = RawCTBinding("PHercTest", "w00", "vol-1", "s3://example/vol.zarr", "0",
                           (16, 16, 16), 9.362, 113.0, volume_sha256="a" * 64)
    base = {"surface_id": "generated-w00", "normal_orientation": "UNDECLARED", "face": "UNKNOWN",
            "face_established_by": "UNDECLARED", "recto_shaved_or_missing": "UNKNOWN",
            "source_licence": "ARGUS", "redistribution_permitted": "YES",
            "generation_command": "argus-test-producer@deadbeef"}
    coords = {"z": np.full((3, 3), 7.0), "y": np.full((3, 3), 7.0), "x": np.full((3, 3), 7.0),
              "validity": np.ones((3, 3), dtype=np.uint8)}
    base.update(
        coordinate_frame="RAW_CT_VOXEL_ZYX",
        output_shape=[3, 3],
        output_hashes={k: _sha256_array(v) for k, v in coords.items()},
    )
    def producer(_binding):
        return dict(base, **coords)
    checks = [run(binding, AdmittedProducer("test", "deadbeef", "ARGUS", producer))]
    for key, replacement in (("z", -1.0), ("x", 99.0)):
        bad = dict(coords)
        bad[key] = np.full((3, 3), replacement)
        try:
            run(binding, AdmittedProducer("test", "deadbeef", "ARGUS",
                                         lambda _b, bad=bad: dict(base, **bad)))
            return False
        except SegmentationRefusal:
            pass
    try:
        run(binding, AdmittedProducer("test", "deadbeef", "ARGUS",
                                     lambda _b: dict(base, **{k: v for k, v in coords.items() if k != "y"})))
        return False
    except SegmentationRefusal:
        pass
    return len(checks) == 1 and checks[0].receipt["claim_ceiling"] == "MECHANICS_ONLY"


if __name__ == "__main__":
    raise SystemExit(0 if selftest() else 1)
