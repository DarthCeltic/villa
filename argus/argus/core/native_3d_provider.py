"""Governed plan for native full-3D ink inference (`ink_3d_dino_guided` over a tifxyz); never runs it."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus.core import eligible_target_operation_gate as target_gate
from argus.core import paths, science_candidates, scroll_ids

SCHEMA = "argus-native-3d-provider-plan-v1"
ACQUISITION_SCHEMA = "argus-native-3d-chunk-acquisition-plan-v1"
REFUSAL_SCHEMA = "argus-native-3d-refusal-receipt-v1"
PLAN_RECEIPT_SCHEMA = "argus-native-3d-plan-receipt-v1"
RECEIPT_DIR_NAME = "native_3d_provider"

CANDIDATE_ID = "ink_3d_dino_guided"
INFERENCE_MODULE = "vesuvius.ink_detection.inference.infer_full3d_tifxyz"
ACQUISITION_MODULE = "vesuvius.ink_detection.preprocessing.download_required_zarr_chunks"

UPSTREAM = {
    "repo": "ScrollPrize/villa",
    "inspected_main_commit": "88d4aa8d4be6455cf51e4b07f14a83865df9157c",
    "direct_3d_landing_commit": "960d76ac7f32557c2ecf323323288d8f23bb71f1",
    "known_open_defects": [
        "PR #1772 input_pad_depth_to not honoured (open)",
        "PR #1826 finalize_outputs empty-chunk skipping (open)",
        "PR #1824 finalize_outputs CLI NameError (open)",
        "PR #1825 multiclass output scaling (open)",
    ],
}
NOT_CLAIMED = ("qualified detector", "unseen-scroll generalization",
               "independent physical ground truth")

PURPOSES = ("apparatus", "development", "qualification")

CODES = (
    "SCROLL_UNREGISTERED", "TIFXYZ_INCOMPLETE", "TIFXYZ_IDENTITY_MISMATCH",
    "VOLUME_SOURCE_MISSING", "VOLUME_SOURCE_MISMATCH", "CHECKPOINT_MISSING",
    "CHECKPOINT_HASH_MISMATCH", "CHECKPOINT_CONFIG_MISSING", "PITCH_INCOMPATIBLE",
    "EXPOSED_FOR_QUALIFICATION", "BOUND_TOO_LARGE", "RESOURCE_PROFILE_UNSUPPORTED",
    "OUTPUT_EXISTS", "PYRAMID_UNSUPPORTED", "REQUEST_INVALID",
)

NATIVE_PITCH_UM = 2.4
PITCH_TOLERANCE_FRACTION = 0.08

CLEAN_EXPOSURE_STATES = frozenset({"CLEAN_CLOSED"})

CHECKPOINT_CONFIG_MARKER = b"full_3d"
TIFXYZ_REQUIRED = ("x.tif", "y.tif", "z.tif")
VOLUME_SOURCE_FILE = "volume_source.txt"
TIFXYZ_META_FILE = "meta.json"
_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
_BLOCK = 8 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

INFERENCE_PATCH_SIZE_ZYX = (256, 256, 256)
DEFAULT_CHUNK_SHAPE_ZYX = (128, 128, 128)
DEFAULT_BYTES_PER_VOXEL = 2
CHUNK_BYTES_STATE = "ASSUMED_NOT_MEASURED"
BYTES_PER_GB = 1_000_000_000

RESOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "consumer_6gb": {
        "vram_gib": 6.0, "host_ram_gib": 16.0,
        "patch_size": list(INFERENCE_PATCH_SIZE_ZYX), "batch_size": 1,
        "max_target_chunks": 512, "cache_max_gb": 3.0,
        "bbox_margin_voxels": 128,
        "tta_default": False, "tta_allowed": False,
        "enforce_torch_memory_cap_gib": 5.5,
        "vram_spill_note": (
            "Some GPU drivers silently spill CUDA allocations into system RAM, so a run "
            "that completes has not necessarily fit; ARGUS enforces the budget with "
            "torch.cuda.set_per_process_memory_fraction(cap / total_gib) and reports peak "
            "allocated, never just completion."),
        "figures_state": "ASSUMED_NOT_MEASURED",
    },
    "workstation_24gb": {
        "vram_gib": 24.0, "host_ram_gib": 64.0,
        "patch_size": list(INFERENCE_PATCH_SIZE_ZYX), "batch_size": 2,
        "max_target_chunks": 4096, "cache_max_gb": 20.0,
        "bbox_margin_voxels": 128,
        "tta_default": False, "tta_allowed": True,
        "enforce_torch_memory_cap_gib": 22.0,
        "vram_spill_note": (
            "Same silent-spill hazard as the consumer profile: enforce the cap and report peak "
            "allocated."),
        "figures_state": "ASSUMED_NOT_MEASURED",
    },
}

CLAIM_CEILING_EXPOSED = (
    "APPARATUS_ONLY: this scroll is exposed to the candidate's training (directly or through its "
    "DINO backbone) or its exposure history is unproven, so any output tests apparatus and "
    "pipeline mechanics only; it is not unseen-scroll generalization and cannot qualify a detector.")
CLAIM_CEILING_CLEAN = (
    "CANDIDATE_CONTROL_REQUIRED: exposure closure is recorded clean, but no independent control "
    "has passed; the output is a candidate result, not a qualified detector and not unseen-scroll "
    "generalization.")

REQUIRED_CONTROLS = (
    "governed action authorization: execution_authorized is False in this plan",
    "eligible_target_operation_gate.require(operation_class=INK_INFERENCE) must PERMIT for this "
    "scroll and exact volume before execution",
    "run argv_plan_only first and archive its receipt before argv_execute",
    "strict-load the checkpoint's embedded full_3d config at run time, EMA weights preferred; the "
    "marker scan in this plan is a weak structural check only",
    "enforce enforce_torch_memory_cap_gib via torch.cuda.set_per_process_memory_fraction and "
    "sample nvidia-smi memory.used; report peak allocated, not completion",
    "check free-space floors on every drive that receives the cache or output before starting",
    "verify output depth handling against known defect PR #1772 (input_pad_depth_to); do not "
    "assume it is fixed",
    "hash the produced output and receipt it before any downstream use",
)


class Native3DRefusal(Exception):
    def __init__(self, code: str, message: str, evidence: Mapping[str, Any] | None = None):
        if code not in CODES:
            raise ValueError("unknown refusal code %r" % (code,))
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message
        self.evidence = dict(evidence or {})


@dataclass(frozen=True)
class TifxyzIdentity:
    directory: str
    file_sha256: dict
    meta_sha256: str | None
    volume_source: str
    volume_source_file_sha256: str
    scroll_segment: str | None
    scroll_segment_state: str
    pinned_by_caller: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CheckpointIdentity:
    path: str
    size_bytes: int
    sha256: str
    sha256_expected: str
    matches_registered_candidate_artifact: bool
    config_marker: dict

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ChunkBound:
    target_chunks: int | None
    max_target_chunks: int
    cache_max_gb: float
    chunk_shape_zyx: tuple
    bytes_per_voxel: int
    chunk_bytes_assumption: int
    chunk_bytes_state: str
    margin_voxels: int
    estimated_download_bytes_upper_bound: int
    bound_basis: str

    def as_dict(self) -> dict:
        out = asdict(self)
        out["chunk_shape_zyx"] = list(self.chunk_shape_zyx)
        return out


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def _refuse(code: str, message: str, **evidence: Any) -> Native3DRefusal:
    return Native3DRefusal(code, message, evidence)


def _resolve_scroll(scroll: str) -> str:
    try:
        return scroll_ids.resolve(scroll)
    except KeyError as exc:
        raise _refuse("SCROLL_UNREGISTERED", str(exc), scroll=str(scroll)) from None


def _profile(name: str, *, tta: bool = False) -> dict[str, Any]:
    if name not in RESOURCE_PROFILES:
        raise _refuse("RESOURCE_PROFILE_UNSUPPORTED", "unknown resource profile %r" % (name,),
                      profile=str(name), supported=sorted(RESOURCE_PROFILES))
    profile = copy.deepcopy(RESOURCE_PROFILES[name])
    if tta and not profile["tta_allowed"]:
        raise _refuse("RESOURCE_PROFILE_UNSUPPORTED",
                      "test-time augmentation is not allowed on profile %r" % (name,),
                      profile=name, tta=True)
    return profile


def _check_request(acquisition_id: Any, purpose: Any, resolution: Any, python_executable: Any) -> None:
    if not isinstance(acquisition_id, str) or not acquisition_id.strip():
        raise _refuse("REQUEST_INVALID", "acquisition_id is required", field="acquisition_id")
    if purpose not in PURPOSES:
        raise _refuse("REQUEST_INVALID", "purpose must be one of %s" % (PURPOSES,),
                      field="purpose", got=str(purpose))
    if not isinstance(python_executable, str) or not python_executable.strip():
        raise _refuse("REQUEST_INVALID", "python_executable is required",
                      field="python_executable")
    if isinstance(resolution, bool) or not isinstance(resolution, int) or resolution < 0:
        raise _refuse("REQUEST_INVALID", "resolution must be a non-negative integer",
                      field="resolution", got=str(resolution))
    if resolution != 0:
        raise _refuse("PYRAMID_UNSUPPORTED",
                      "only level 0 is planned; pyramid support is NOT_VERIFIED at the pinned commit",
                      resolution=resolution, upstream_pyramid_support="NOT_VERIFIED")


def read_checkpoint_config_marker(path) -> dict:
    """Weak structural check: does the file's raw bytes contain the ASCII marker `full_3d`."""
    return _stream_checkpoint(Path(path), want_sha=False)[1]


def _stream_checkpoint(path: Path, *, want_sha: bool) -> tuple[str | None, dict]:
    if not path.is_file():
        raise _refuse("CHECKPOINT_MISSING", "checkpoint is not an existing file",
                      checkpoint=str(path))
    digest = hashlib.sha256() if want_sha else None
    keep = len(CHECKPOINT_CONFIG_MARKER) - 1
    tail, offset, first, zip_container = b"", 0, None, False
    with path.open("rb") as stream:
        while True:
            block = stream.read(_BLOCK)
            if not block:
                break
            if offset == 0:
                zip_container = block[:4] == b"PK\x03\x04"
            if digest is not None:
                digest.update(block)
            if first is None:
                window = tail + block
                index = window.find(CHECKPOINT_CONFIG_MARKER)
                if index >= 0:
                    first = offset - len(tail) + index
                tail = window[-keep:]
            offset += len(block)
            if first is not None and digest is None:
                break
    marker = {"marker": CHECKPOINT_CONFIG_MARKER.decode("ascii"), "found": first is not None,
              "first_offset": first, "bytes_scanned": offset, "zip_container": zip_container,
              "strength": "WEAK_STRUCTURAL"}
    return (digest.hexdigest() if digest is not None else None), marker


def inspect_checkpoint(path, sha256_expected: str, *, registered_sha256: str | None = None
                       ) -> CheckpointIdentity:
    target = Path(path).resolve()
    expected = str(sha256_expected or "").strip().lower()
    if not _SHA256_RE.match(expected):
        raise _refuse("REQUEST_INVALID", "checkpoint_sha256_expected must be 64 hex characters",
                      field="checkpoint_sha256_expected")
    actual, marker = _stream_checkpoint(target, want_sha=True)
    size = target.stat().st_size
    if actual != expected:
        raise _refuse("CHECKPOINT_HASH_MISMATCH", "checkpoint sha256 differs from the pinned expectation",
                      checkpoint=str(target), expected=expected, actual=actual, size_bytes=size)
    if not marker["found"]:
        raise _refuse("CHECKPOINT_CONFIG_MISSING",
                      "no embedded full_3d config marker found in the checkpoint bytes",
                      checkpoint=str(target), sha256=actual, config_marker=marker)
    return CheckpointIdentity(
        path=str(target), size_bytes=size, sha256=actual, sha256_expected=expected,
        matches_registered_candidate_artifact=bool(registered_sha256 and actual == registered_sha256),
        config_marker=marker)


def _read_source_line(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict").rstrip("\r\n")


def inspect_tifxyz(tifxyz_dir, *, volume_source: str, expected_volume_source: str,
                   scroll: str, expected_sha256: Mapping[str, str] | None = None) -> TifxyzIdentity:
    """Hash the segment's geometry files and bind its volume_source.txt to the eligible volume."""
    directory = Path(tifxyz_dir).resolve()
    if not directory.is_dir():
        raise _refuse("TIFXYZ_INCOMPLETE", "tifxyz_dir is not a directory", tifxyz_dir=str(directory))
    missing = [name for name in TIFXYZ_REQUIRED if not (directory / name).is_file()]
    if missing:
        raise _refuse("TIFXYZ_INCOMPLETE", "tifxyz is missing required geometry files",
                      tifxyz_dir=str(directory), missing=missing)
    source_file = directory / VOLUME_SOURCE_FILE
    if not source_file.is_file():
        raise _refuse("VOLUME_SOURCE_MISSING", "%s is absent" % VOLUME_SOURCE_FILE,
                      tifxyz_dir=str(directory))
    recorded = _read_source_line(source_file)
    if not recorded.strip():
        raise _refuse("VOLUME_SOURCE_MISSING", "%s is empty" % VOLUME_SOURCE_FILE,
                      tifxyz_dir=str(directory))
    hashes: dict[str, str] = {}
    for name in TIFXYZ_REQUIRED:
        member = directory / name
        with member.open("rb") as stream:
            head = stream.read(4)
        if head not in _TIFF_MAGIC:
            raise _refuse("TIFXYZ_INCOMPLETE", "%s does not carry a TIFF header" % name,
                          tifxyz_dir=str(directory), file=name)
        hashes[name] = _file_sha256(member)
    meta_sha = None
    meta_file = directory / TIFXYZ_META_FILE
    if meta_file.is_file():
        try:
            if not isinstance(json.loads(meta_file.read_text(encoding="utf-8")), dict):
                raise ValueError("meta.json is not a JSON object")
        except ValueError as exc:
            raise _refuse("TIFXYZ_IDENTITY_MISMATCH", "meta.json is unreadable: %s" % exc,
                          tifxyz_dir=str(directory)) from None
        meta_sha = _file_sha256(meta_file)
    pinned = dict(expected_sha256 or {})
    observed = {**hashes, **({TIFXYZ_META_FILE: meta_sha} if meta_sha else {})}
    wrong = {name: {"expected": want, "actual": observed.get(name)}
             for name, want in pinned.items() if observed.get(name) != want}
    if wrong:
        raise _refuse("TIFXYZ_IDENTITY_MISMATCH", "tifxyz bytes differ from the caller's pinned hashes",
                      tifxyz_dir=str(directory), differing=wrong)
    declared = str(volume_source or "").rstrip("\r\n")
    expected = str(expected_volume_source or "").rstrip("\r\n")
    if not expected:
        raise _refuse("REQUEST_INVALID", "expected_volume_source is required",
                      field="expected_volume_source")
    if recorded != expected or declared != expected:
        raise _refuse("VOLUME_SOURCE_MISMATCH",
                      "volume source differs from the exact eligible volume identity",
                      expected=expected, volume_source_txt=recorded, declared=declared,
                      txt_equals_expected=recorded == expected,
                      declared_equals_expected=declared == expected)
    segment = target_gate.scroll_segment_from_source(expected)
    segment_state = "UNPARSEABLE_NOT_USED"
    if segment is not None:
        try:
            resolved = scroll_ids.resolve(segment)
        except KeyError:
            segment_state = "UNRESOLVABLE_NOT_USED"
        else:
            segment_state = "MATCH"
            if resolved != _resolve_scroll(scroll):
                raise _refuse("VOLUME_SOURCE_MISMATCH",
                              "the volume source names a different scroll than the one declared",
                              expected=expected, scroll_segment=segment, segment_resolves_to=resolved,
                              declared_scroll=_resolve_scroll(scroll))
    return TifxyzIdentity(
        directory=str(directory), file_sha256=hashes,
        meta_sha256=meta_sha, volume_source=expected,
        volume_source_file_sha256=_file_sha256(source_file), scroll_segment=segment,
        scroll_segment_state=segment_state, pinned_by_caller=bool(pinned))


def check_pitch(pitch_um: Any) -> dict:
    ok = (not isinstance(pitch_um, bool) and isinstance(pitch_um, (int, float))
          and math.isfinite(pitch_um) and pitch_um > 0)
    if not ok:
        raise _refuse("PITCH_INCOMPATIBLE", "pitch_um must be a declared positive number",
                      pitch_um=str(pitch_um))
    band = NATIVE_PITCH_UM * PITCH_TOLERANCE_FRACTION
    if abs(float(pitch_um) - NATIVE_PITCH_UM) > band:
        raise _refuse("PITCH_INCOMPATIBLE",
                      "%.4f um is outside the candidate's native family of about %.1f um"
                      % (float(pitch_um), NATIVE_PITCH_UM),
                      pitch_um=float(pitch_um), native_pitch_um=NATIVE_PITCH_UM,
                      tolerance_fraction=PITCH_TOLERANCE_FRACTION)
    return {"pitch_um": float(pitch_um), "native_pitch_um": NATIVE_PITCH_UM,
            "tolerance_fraction": PITCH_TOLERANCE_FRACTION}


def _candidate_row(canonical: str) -> dict:
    row = next((r for r in science_candidates.inventory(canonical)["candidates"]
                if r["id"] == CANDIDATE_ID), None)
    if row is None:
        raise _refuse("REQUEST_INVALID", "candidate %s is absent from the science inventory"
                      % CANDIDATE_ID, field="candidate")
    return row


def _exposure(row: Mapping[str, Any], canonical: str, purpose: str) -> dict:
    state = row["selected_scroll_exposure"]
    clean = state in CLEAN_EXPOSURE_STATES
    if purpose == "qualification" and not clean:
        raise _refuse("EXPOSED_FOR_QUALIFICATION",
                      "exposure state %s cannot serve qualification" % state,
                      exposure_state=state, candidate=CANDIDATE_ID, scroll=canonical,
                      clean_states=sorted(CLEAN_EXPOSURE_STATES))
    return {"state": state, "clean": clean, "candidate": CANDIDATE_ID,
            "source": "argus.core.science_candidates.inventory"}


def _bbox_bounds(target_bbox: Any) -> tuple[tuple, tuple]:
    if isinstance(target_bbox, Mapping):
        lo, hi = target_bbox.get("min"), target_bbox.get("max")
    elif isinstance(target_bbox, Sequence) and not isinstance(target_bbox, (str, bytes)) \
            and len(target_bbox) == 2:
        lo, hi = target_bbox
    else:
        raise ValueError("target_bbox must be {'min': zyx, 'max': zyx} or a (min, max) pair")
    lo, hi = _int3(lo, "bbox min"), _int3(hi, "bbox max")
    if any(v < 0 for v in lo) or any(h <= l for l, h in zip(lo, hi)):
        raise ValueError("target_bbox needs 0 <= min < max on every axis (max exclusive)")
    return lo, hi


def _int3(value: Any, label: str) -> tuple:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise ValueError("%s must be three integers (z, y, x)" % label)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise ValueError("%s must be integers" % label)
    return tuple(int(v) for v in value)


def count_target_chunks(target_bbox, chunk_shape, margin_voxels) -> int:
    """Level-0 chunks touched by a half-open zyx bbox grown by `margin_voxels` on every side."""
    lo, hi = _bbox_bounds(target_bbox)
    chunk = _int3(chunk_shape, "chunk_shape")
    if any(c <= 0 for c in chunk):
        raise ValueError("chunk_shape must be positive")
    if isinstance(margin_voxels, bool) or not isinstance(margin_voxels, int) or margin_voxels < 0:
        raise ValueError("margin_voxels must be a non-negative integer")
    total = 1
    for low, high, size in zip(lo, hi, chunk):
        first = max(low - margin_voxels, 0) // size
        last = (high - 1 + margin_voxels) // size
        total *= last - first + 1
    return total


def compute_bound(target_bbox, profile: Mapping[str, Any], *, chunk_shape=None,
                  bytes_per_voxel: int | None = None) -> ChunkBound:
    shape = tuple(chunk_shape) if chunk_shape is not None else DEFAULT_CHUNK_SHAPE_ZYX
    per_voxel = DEFAULT_BYTES_PER_VOXEL if bytes_per_voxel is None else bytes_per_voxel
    if isinstance(per_voxel, bool) or not isinstance(per_voxel, int) or per_voxel < 1:
        raise ValueError("bytes_per_voxel must be a positive integer")
    extent = _int3(shape, "chunk_shape")
    if any(c <= 0 for c in extent):
        raise ValueError("chunk_shape must be positive")
    chunk_bytes = per_voxel * math.prod(extent)
    margin = int(profile["bbox_margin_voxels"])
    cap = int(profile["max_target_chunks"])
    if target_bbox is None:
        count, basis, chunks = None, "PROFILE_CAP_NO_BBOX", cap
    else:
        count = count_target_chunks(target_bbox, shape, margin)
        basis, chunks = "BBOX_PLUS_MARGIN", count
    return ChunkBound(
        target_chunks=count, max_target_chunks=cap, cache_max_gb=float(profile["cache_max_gb"]),
        chunk_shape_zyx=tuple(shape), bytes_per_voxel=per_voxel, chunk_bytes_assumption=chunk_bytes,
        chunk_bytes_state=CHUNK_BYTES_STATE, margin_voxels=margin,
        estimated_download_bytes_upper_bound=chunks * chunk_bytes, bound_basis=basis)


def enforce_bound(bound: ChunkBound) -> ChunkBound:
    if bound.target_chunks is not None and bound.target_chunks > bound.max_target_chunks:
        raise _refuse("BOUND_TOO_LARGE", "target touches more chunks than the profile allows",
                      **bound.as_dict(), reason="CHUNKS_EXCEED_CAP")
    if bound.estimated_download_bytes_upper_bound > bound.cache_max_gb * BYTES_PER_GB:
        raise _refuse("BOUND_TOO_LARGE", "estimated download upper bound exceeds the cache cap",
                      **bound.as_dict(), reason="BYTES_EXCEED_CACHE_CAP")
    return bound


def _bound_for(target_bbox, profile, chunk_shape, bytes_per_voxel) -> ChunkBound:
    try:
        return enforce_bound(compute_bound(target_bbox, profile, chunk_shape=chunk_shape,
                                           bytes_per_voxel=bytes_per_voxel))
    except ValueError as exc:
        raise _refuse("REQUEST_INVALID", str(exc), field="target_bbox") from None


def _num(value: float) -> str:
    return "%g" % value


def _inference_argv(python_executable: str, tifxyz: Path, checkpoint: Path, output: Path,
                    cache_dir: Path, profile: Mapping[str, Any], resolution: int, tta: bool,
                    *, plan_only: bool) -> list[str]:
    argv = [python_executable, "-m", INFERENCE_MODULE, str(tifxyz), str(checkpoint), str(output)]
    if plan_only:
        argv.append("--plan-only")
    argv += ["--cache-dir", str(cache_dir), "--cache-max-gb", _num(profile["cache_max_gb"]),
             "--max-target-chunks", str(profile["max_target_chunks"]),
             "--resolution", str(resolution)]
    if tta:
        argv.append("--tta")
    return argv


def _sealed(payload: dict) -> dict:
    payload["plan_sha256"] = _canonical_sha256(payload)
    return payload


def plan(*, scroll: str, acquisition_id: str, tifxyz_dir, checkpoint, checkpoint_sha256_expected: str,
         volume_source: str, expected_volume_source: str, pitch_um: float, purpose: str,
         target_bbox=None, profile: str = "consumer_6gb", output_dir, python_executable: str,
         tta: bool = False, resolution: int = 0, cache_dir=None,
         expected_tifxyz_sha256: Mapping[str, str] | None = None, chunk_shape=None,
         bytes_per_voxel: int | None = None) -> dict:
    """A bound, checked instruction for native full-3D inference."""
    canonical = _resolve_scroll(scroll)
    _check_request(acquisition_id, purpose, resolution, python_executable)
    chosen = _profile(profile, tta=bool(tta))
    identity = inspect_tifxyz(tifxyz_dir, volume_source=volume_source,
                              expected_volume_source=expected_volume_source, scroll=canonical,
                              expected_sha256=expected_tifxyz_sha256)
    row = _candidate_row(canonical)
    weights = inspect_checkpoint(checkpoint, checkpoint_sha256_expected,
                                 registered_sha256=(row.get("artifact") or {}).get("sha256"))
    pitch = check_pitch(pitch_um)
    exposure = _exposure(row, canonical, purpose)
    output = Path(output_dir).resolve()
    if output.exists():
        raise _refuse("OUTPUT_EXISTS", "output already exists; plans never overwrite",
                      output_dir=str(output))
    bound = _bound_for(target_bbox, chosen, chunk_shape, bytes_per_voxel)
    cache = Path(cache_dir).resolve() if cache_dir is not None else Path(
        paths.science_cache(RECEIPT_DIR_NAME, "chunk_cache"))
    tifxyz = Path(identity.directory)
    weights_path = Path(weights.path)
    payload = {
        "schema": SCHEMA, "state": "PLANNED_PROVIDER_EXECUTION", "read_only": True,
        "executes": False, "execution_authorized": False,
        "provider": {"candidate_id": CANDIDATE_ID, "lifecycle": row["lifecycle"],
                     "registered_artifact": row.get("artifact"), "native_pitch_um": NATIVE_PITCH_UM},
        "material": {"physical_scroll": canonical, "acquisition_id": acquisition_id.strip(),
                     "pitch_um": pitch["pitch_um"],
                     "pitch_tolerance_fraction": pitch["tolerance_fraction"]},
        "purpose": purpose,
        "identity": {"tifxyz": identity.as_dict(), "checkpoint": weights.as_dict(),
                     "operation_class_for_gate": target_gate.INK_INFERENCE},
        "exposure": exposure,
        "claim_ceiling": CLAIM_CEILING_CLEAN if exposure["clean"] else CLAIM_CEILING_EXPOSED,
        "unseen_scroll_generalization": False,
        "profile": {"name": profile, **chosen},
        "bounds": {**bound.as_dict(),
                   "target_bbox": None if target_bbox is None else [
                       list(v) for v in _bbox_bounds(target_bbox)]},
        "output": {"dir": str(output), "output_level": 0},
        "pyramid": {"state": "DEFERRED", "upstream_support": "NOT_VERIFIED",
                    "why": "level 0 first; pyramid creation is not verified at the pinned upstream "
                           "commit, so no pyramid exists or is claimed"},
        "argv_plan_only": _inference_argv(python_executable, tifxyz, weights_path, output, cache,
                                          chosen, resolution, bool(tta), plan_only=True),
        "argv_execute": _inference_argv(python_executable, tifxyz, weights_path, output, cache,
                                        chosen, resolution, bool(tta), plan_only=False),
        "cache_dir": str(cache),
        "tta": bool(tta), "resolution": resolution,
        "candidate_controls": list(row.get("controls") or []),
        "required_controls": list(REQUIRED_CONTROLS),
        "known_defect_policy": "recorded as a watch list; no listed defect is assumed fixed",
        "upstream": copy.deepcopy(UPSTREAM),
        "not_claimed": list(NOT_CLAIMED),
        "promotion": "never automatic; a plan is not a run and produces no result to promote",
    }
    return _sealed(payload)


def plan_chunk_acquisition(*, scroll: str, acquisition_id: str, datasets_root, volumes_json,
                           output_root, python_executable: str, target_bbox=None,
                           profile: str = "consumer_6gb", patch_filter: str | None = None,
                           patch_size: str | int | None = None,
                           overlap_fraction: float | None = None, label_version: str | None = None,
                           chunk_shape=None, bytes_per_voxel: int | None = None) -> dict:
    """Plan a bounded sparse chunk copy: dry run first, execute only after a governed authorization."""
    canonical = _resolve_scroll(scroll)
    _check_request(acquisition_id, "apparatus", 0, python_executable)
    chosen = _profile(profile)
    if target_bbox is None:
        raise _refuse("BOUND_TOO_LARGE",
                      "the acquisition byte bound cannot be computed without a target_bbox",
                      reason="UNBOUNDED_NO_TARGET_BBOX", max_target_chunks=chosen["max_target_chunks"])
    bound = _bound_for(target_bbox, chosen, chunk_shape, bytes_per_voxel)
    datasets, volumes, output = (Path(datasets_root).resolve(), Path(volumes_json).resolve(),
                                 Path(output_root).resolve())
    if not datasets.is_dir():
        raise _refuse("REQUEST_INVALID", "datasets_root is not a directory", field="datasets_root")
    if not volumes.is_file():
        raise _refuse("REQUEST_INVALID", "volumes_json is not a file", field="volumes_json")
    base = [python_executable, "-m", ACQUISITION_MODULE, "--datasets-root", str(datasets),
            "--volumes-json", str(volumes), "--output-root", str(output)]
    optional = [("--patch-filter", patch_filter), ("--patch-size", patch_size),
                ("--overlap-fraction", overlap_fraction), ("--label-version", label_version)]
    given = [(flag, value) for flag, value in optional if value is not None]
    for flag, value in given:
        base += [flag, str(value)]
    payload = {
        "schema": ACQUISITION_SCHEMA, "state": "PLANNED_PROVIDER_EXECUTION", "read_only": True,
        "executes": False, "execution_authorized": False,
        "material": {"physical_scroll": canonical, "acquisition_id": acquisition_id.strip()},
        "inputs": {"datasets_root": str(datasets), "volumes_json": str(volumes),
                   "volumes_json_sha256": _file_sha256(volumes)},
        "output_root": str(output), "output_root_exists": output.exists(),
        "profile": {"name": profile, **chosen},
        "bounds": {**bound.as_dict(), "target_bbox": [list(v) for v in _bbox_bounds(target_bbox)]},
        "dry_run_first": True, "resumable": True,
        "argv_dry_run": base + ["--dry-run"],
        "argv_execute": list(base),
        "unverified_flags": [flag for flag, _ in given],
        "required_controls": [
            "run argv_dry_run and archive its plan before argv_execute",
            "compare the dry-run chunk count and bytes with bounds before authorizing execution",
            "governed action authorization: execution_authorized is False in this plan"],
        "upstream": copy.deepcopy(UPSTREAM),
        "not_claimed": list(NOT_CLAIMED),
    }
    return _sealed(payload)


_SENSITIVE = ("secret", "token", "password", "passwd", "credential", "api_key", "apikey",
              "access_key", "private", "authorization", "signature", "cookie", "bearer")


def _redact_string(value: str) -> str:
    if "://" not in value:
        return value
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return "[UNPARSEABLE_URL_REDACTED]"
    host = parts[1].rsplit("@", 1)[-1]
    netloc = ("[REDACTED]@" + host) if "@" in parts[1] else parts[1]
    query = "[REDACTED]" if parts.query else ""
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): ("[REDACTED]" if any(word in str(k).lower() for word in _SENSITIVE)
                         else sanitize(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize(v) for v in (sorted(value, key=str) if isinstance(value, (set, frozenset))
                                      else value)]
    if isinstance(value, Path):
        return _redact_string(str(value))
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value))


def _receipt_root(out_dir) -> Path:
    return Path(out_dir) if out_dir is not None else Path(paths.artifact_write_root()) / RECEIPT_DIR_NAME


def _write_exclusive(directory: Path, stem: str, document: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2, sort_keys=True, default=str) + "\n"
    attempt = 1
    while True:
        target = directory / ("%s.json" % stem if attempt == 1 else "%s_%d.json" % (stem, attempt))
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(text)
        except FileExistsError:
            attempt += 1
            continue
        return target


def write_refusal_receipt(refusal: Native3DRefusal, request: Mapping[str, Any], *,
                          out_dir=None) -> Path:
    body = {"code": refusal.code, "message": refusal.message,
            "evidence": sanitize(refusal.evidence), "request": sanitize(dict(request))}
    document = {"schema": REFUSAL_SCHEMA, "state": "REFUSED", "executed": False,
                **body, "utc": _utc(), "upstream": copy.deepcopy(UPSTREAM),
                "not_claimed": list(NOT_CLAIMED)}
    stem = "NATIVE3D_REFUSED_%s_%s" % (refusal.code, _canonical_sha256(body)[:8])
    return _write_exclusive(_receipt_root(out_dir) / "refusals", stem, document)


def write_plan_receipt(plan_payload: Mapping[str, Any], out_dir=None) -> Path:
    document = {"schema": PLAN_RECEIPT_SCHEMA, "executed": False, "utc": _utc(),
                "plan": sanitize(dict(plan_payload))}
    stem = "NATIVE3D_PLAN_%s" % str(plan_payload.get("plan_sha256", _canonical_sha256(document)))[:8]
    return _write_exclusive(_receipt_root(out_dir) / "plans", stem, document)
