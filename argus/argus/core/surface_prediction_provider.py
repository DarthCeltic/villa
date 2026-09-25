"""Governed plan for recto surface-prediction checkpoints; this module never runs them."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from argus.core import canonical_io, scroll_ids

SCHEMA = "argus-surface-prediction-plan-v1"

AMBIGUOUS_UNSEALED_NAME = "surface_recto_3dunet"

KNOWN_CHECKPOINTS: dict[str, dict[str, Any]] = {
    "surface_m7_nnunet": {
        "repo_id": "scrollprize/surface_m7_nnunet",
        "input_domain": "kaggle_curated_fragment",
        "reference_pitch_um": canonical_io.FRAG_PITCH_UM,
        "reference_pitch_evidence": (
            "argus.core.canonical_io.FRAG_PITCH_UM -- the detached Kaggle fragment pitch, the "
            "pitch of the curated fragment images this checkpoint's baseline command reads."),
        "checkpoint_sha256": "17465b77591b794638e671f1a9f79c4cf1e79821f302e6fc235e3725e5da7d7e",
        "checkpoint_sha256_provenance": "pinned checkpoint hash, not upstream-published",
        "evidence": (
            "no evaluation ships with the public release. NON-PROMOTABLE: the published "
            "checkpoint does not name its training samples or a split manifest, so train/test "
            "overlap cannot be ruled out."),
        "scientific_state": "NON_PROMOTABLE",
        "claim_ceiling": "DIAGNOSTIC_ONLY",
    },
    "surface_recto": {
        "repo_id": "scrollprize/surface_recto",
        "input_domain": "raw_scroll_ct",
        "reference_pitch_um": None,
        "reference_pitch_evidence": (
            "UNDECLARED_BY_UPSTREAM. The published checkpoint declares no training-reference "
            "pitch; its plans.json reports only a normalised spacing, so no pitch match is "
            "checked."),
        "checkpoint_sha256": "2e7e4be80b2e3640ad5cf6bbf84628808c67bf6ef955288b27c2b34be80c776f",
        "checkpoint_sha256_provenance": "pinned checkpoint hash at the pinned revision",
        "revision": "86f026f8be537db336e2854650db0b40c004ad49",
        "evidence": (
            "no evaluation ships with the public release. A plan is mechanics only: it "
            "establishes nothing about ink, nothing about face semantics, and contributes "
            "nothing to qualification."),
        "scientific_state": "PLAN_ONLY",
        "claim_ceiling": "MECHANICS_ONLY",
    },
}

PITCH_TOLERANCE_FRACTION = 0.02


class SurfacePlanRefusal(RuntimeError):
    """Raised instead of silently routing to the wrong checkpoint or inventing a pitch match."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def known_checkpoints() -> dict[str, dict[str, Any]]:
    """Read-only view of what this provider knows, for callers deciding what to ask for."""
    return {k: dict(v) for k, v in KNOWN_CHECKPOINTS.items()}


def _resolve_checkpoint(requested_checkpoint: str) -> dict[str, Any]:
    if requested_checkpoint == AMBIGUOUS_UNSEALED_NAME:
        raise SurfacePlanRefusal(
            "SURFACE_CHECKPOINT_AMBIGUOUS: %r is listed alongside scrollprize/surface_recto in "
            "the published model listing, but it has no pinned weight hash. Only "
            "'surface_recto' (scrollprize/surface_recto) may be requested."
            % requested_checkpoint)
    if requested_checkpoint not in KNOWN_CHECKPOINTS:
        raise SurfacePlanRefusal(
            "SURFACE_CHECKPOINT_UNKNOWN: %r is not a registered checkpoint. Known: %s"
            % (requested_checkpoint, sorted(KNOWN_CHECKPOINTS)))
    return KNOWN_CHECKPOINTS[requested_checkpoint]


def _check_spacing(checkpoint_id: str, entry: dict[str, Any], spacing_um: float) -> None:
    if spacing_um is None or float(spacing_um) <= 0:
        raise SurfacePlanRefusal(
            "SPACING_REQUIRED: spacing_um must be a declared positive physical pitch for the "
            "input; an unstated pitch is never assumed.")
    reference = entry["reference_pitch_um"]
    if reference is None:
        return
    if abs(float(spacing_um) - float(reference)) > float(reference) * PITCH_TOLERANCE_FRACTION:
        raise SurfacePlanRefusal(
            "SPACING_MISMATCH: %s's reference pitch is %.3f um (%s); %.3f um is "
            "outside the %.0f%% tolerance of the material this checkpoint expects." % (checkpoint_id, reference, entry["reference_pitch_evidence"],
                                    float(spacing_um), PITCH_TOLERANCE_FRACTION * 100))


def _check_domain(checkpoint_id: str, entry: dict[str, Any], input_domain: str) -> None:
    if input_domain != entry["input_domain"]:
        raise SurfacePlanRefusal(
            "DOMAIN_MISMATCH: %s expects %r input; %r was declared. Requesting a checkpoint "
            "outside its input domain is refused rather than silently honoured."
            % (checkpoint_id, entry["input_domain"], input_domain))


def _checked_file(path_str: str, *, must_be_dir: bool = False) -> Path:
    p = Path(path_str).resolve()
    if must_be_dir:
        if not p.is_dir():
            raise SurfacePlanRefusal("PATH_MISSING: %s is not an existing directory" % path_str)
    elif not p.is_file():
        raise SurfacePlanRefusal("PATH_MISSING: %s is not an existing file" % path_str)
    return p


def plan(*, scroll: str, acquisition_id: str, requested_checkpoint: str, input_domain: str,
         spacing_um: float, python_executable: str, checkpoint: str, input_path: str,
         output_dir: str, device: str = "cuda", array: str = "0", bbox: str | None = None,
         batch_size: int = 1) -> dict[str, Any]:
    """A governed plan binding one scroll+acquisition to one surface-prediction checkpoint."""
    try:
        canonical = scroll_ids.resolve(scroll)
    except KeyError as exc:
        raise SurfacePlanRefusal(str(exc)) from None
    if not acquisition_id or not str(acquisition_id).strip():
        raise SurfacePlanRefusal("acquisition_id is required")

    entry = _resolve_checkpoint(requested_checkpoint)
    _check_domain(requested_checkpoint, entry, input_domain)
    _check_spacing(requested_checkpoint, entry, spacing_um)

    runtime = _checked_file(python_executable)
    weights = _checked_file(checkpoint)
    actual_sha256 = _sha256(weights)
    if actual_sha256 != entry["checkpoint_sha256"]:
        raise SurfacePlanRefusal(
            "CHECKPOINT_MISMATCH: %s does not match the pinned sha256 for %s (%s)"
            % (checkpoint, requested_checkpoint, entry["checkpoint_sha256_provenance"]))

    is_zarr_domain = entry["input_domain"] == "raw_scroll_ct"
    resolved_input = _checked_file(input_path, must_be_dir=is_zarr_domain)

    out = Path(output_dir).resolve()
    if out.exists():
        raise SurfacePlanRefusal("OUTPUT_EXISTS: %s already exists; plans never overwrite" % out)

    if requested_checkpoint == "surface_m7_nnunet":
        argv = [str(runtime), "-m", "argus_vesuvius.cli", "curated-surface-baseline",
                "--model-dir", str(weights.parent.parent if weights.parent.name.startswith("fold_")
                                    else weights.parent),
                "--image", str(resolved_input), "--output-dir", str(out),
                "--checkpoint-sha256", entry["checkpoint_sha256"]]
    else:
        argv = [str(runtime), "vesuvius.predict", "--model_path", str(weights.parent),
                "--model_type", "nnunet", "--input_dir", str(resolved_input),
                "--array", array, "--output_dir", str(out), "--batch_size", str(int(batch_size)),
                "--device", device]
        if bbox:
            argv += ["--bbox", bbox]

    payload = {
        "schema": SCHEMA,
        "state": "PLANNED_PROVIDER_EXECUTION",
        "read_only": True,
        "checkpoint": requested_checkpoint,
        "repo_id": entry["repo_id"],
        "material": {"physical_scroll": canonical, "acquisition_id": acquisition_id,
                     "input_domain": input_domain, "spacing_um": float(spacing_um)},
        "runtime": {"python_executable": str(runtime), "device": device},
        "inputs": {"input_path": str(resolved_input), "checkpoint": str(weights),
                   "checkpoint_sha256": actual_sha256},
        "output_dir": str(out),
        "argv": argv,
        "reference_pitch_um": entry["reference_pitch_um"],
        "reference_pitch_evidence": entry["reference_pitch_evidence"],
        "scientific_state": entry["scientific_state"],
        "claim_ceiling": entry["claim_ceiling"],
        "evidence": entry["evidence"],
        "promotion": "never automatic; this is a plan, not a run, and produces no result to promote",
    }
    payload["plan_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return payload
