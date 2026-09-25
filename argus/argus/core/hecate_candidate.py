"""Governed plan for the pinned public Hecate package; this module never runs it."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from argus.core import paths
from argus.core import science_candidates, scroll_ids

SCHEMA = "argus-hecate-plan-v1"
MAX_SOURCE_SCRIPT_BYTES = 4 << 20
SOURCE_REVISION = "9cb86e500e944b11a06a7020403cde5dffb5bcb2"
SOURCE_BLOB_SHA1 = "a2d3494361d81107d4bd9268cfe377d80f22cd0a"


class HecatePlanRefusal(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_blob_id(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _candidate(spacing_um: float) -> dict:
    target = "hecate_24um" if abs(spacing_um - 2.4) <= 0.048 else (
        "hecate_96um" if abs(spacing_um - 9.6) <= 0.192 else None)
    if target is None:
        raise HecatePlanRefusal("spacing_um must match a released 2.4 or 9.6 um checkpoint within 2%")
    return next(row for row in science_candidates.inventory()["candidates"] if row["id"] == target)


def plan(*, scroll: str, acquisition_id: str, input_render: str, spacing_um: float,
         python_executable: str, source_script: str, checkpoint: str, output_png: str | None = None,
         output_3d: str | None = None, array: str = "0", device: str = "cuda",
         reverse: bool = False, batch_size: int = 1, stride: int | None = None,
         precision: str = "fp32") -> dict:
    try:
        canonical = scroll_ids.resolve(scroll)
    except KeyError as exc:
        raise HecatePlanRefusal(str(exc)) from None
    if not acquisition_id.strip():
        raise HecatePlanRefusal("acquisition_id is required")
    candidate = _candidate(float(spacing_um))
    try:
        if not (paths.plain_local_path(python_executable) and paths.plain_local_path(source_script)):
            raise ValueError("not a plain local path")
        runtime = Path(python_executable).resolve()
        source = Path(source_script).resolve()
    except (OSError, ValueError, RuntimeError):
        raise HecatePlanRefusal("python_executable and source_script must name the interpreter and the pinned Hecate source") from None
    for label, value in (("checkpoint", checkpoint), ("input_render", input_render), ("output_png", output_png), ("output_3d", output_3d)):
        if value and not paths.plain_local_path(value):
            raise HecatePlanRefusal("%s must be a plain local path" % label)
    weights = Path(checkpoint).resolve()
    volume = Path(input_render).resolve()
    if not runtime.is_file():
        raise HecatePlanRefusal("python_executable and source_script must name the interpreter and the pinned Hecate source")
    try:
        pinned = source.is_file() and source.stat().st_size <= MAX_SOURCE_SCRIPT_BYTES and _git_blob_id(source) == SOURCE_BLOB_SHA1
    except OSError:
        pinned = False
    if not pinned:
        raise HecatePlanRefusal("python_executable and source_script must name the interpreter and the pinned Hecate source")
    if not weights.is_file() or _sha256(weights) != candidate["artifact"]["sha256"]:
        raise HecatePlanRefusal("checkpoint is absent or differs from the pinned SHA-256")
    if not volume.exists() or not (volume.suffix.lower() == ".npy" or
                                   (volume.is_dir() and volume.suffix.lower() == ".zarr")):
        raise HecatePlanRefusal("input_render must be an existing uint8 ZYX .npy or local Zarr directory")
    if not output_png and not output_3d:
        raise HecatePlanRefusal("at least one of output_png or output_3d is required")
    outputs: dict[str, str] = {}
    for name, raw, suffix in (("png", output_png, ".png"), ("volume", output_3d, ".zarr")):
        if not raw:
            continue
        path = Path(raw).resolve()
        if path.suffix.lower() != suffix or path.exists():
            raise HecatePlanRefusal("%s output must be a new %s path" % (name, suffix))
        if name == "png" and path.with_suffix(".json").exists():
            raise HecatePlanRefusal("PNG metadata output already exists; plans never overwrite")
        outputs[name] = str(path)
    if precision not in {"fp32", "bf16"}:
        raise HecatePlanRefusal("precision must be fp32 or bf16")
    if batch_size < 1 or (stride is not None and stride < 1):
        raise HecatePlanRefusal("batch_size and explicit stride must be positive")
    argv = [str(runtime), str(source), "--checkpoint", str(weights), "--input", str(volume),
            "--array", array, "--spacing-um", str(float(spacing_um)), "--device", device,
            "--batch-size", str(int(batch_size)), "--precision", precision]
    if output_png:
        argv += ["--output", outputs["png"]]
    if output_3d:
        argv += ["--output-3d", outputs["volume"]]
    if reverse:
        argv.append("--reverse")
    if stride is not None:
        argv += ["--stride", str(int(stride))]
    selected_candidates = science_candidates.inventory(canonical)["candidates"]
    selected = next(row for row in selected_candidates if row["id"] == candidate["id"])
    payload = {
        "schema": SCHEMA, "state": "PLANNED_PROVIDER_EXECUTION", "read_only": True,
        "provider": candidate["id"], "source_revision": SOURCE_REVISION,
        "source_blob_sha1": SOURCE_BLOB_SHA1,
        "material": {"physical_scroll": canonical, "acquisition_id": acquisition_id,
                     "surface_render_spacing_um": float(spacing_um)},
        "runtime": {"python_executable": str(runtime), "device": device},
        "inputs": {"surface_render": str(volume), "array": array,
                   "checkpoint": str(weights), "checkpoint_sha256": candidate["artifact"]["sha256"]},
        "outputs": outputs, "argv": argv,
        "scientific_state": "CANDIDATE_CONTROL_REQUIRED",
        "exposure": selected["selected_scroll_exposure"],
        "promotion": "never automatic; strict-load, known-label and independent-control receipts required",
    }
    payload["plan_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return payload
