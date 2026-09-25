"""Governed plan for current Villa Lasagna ``predict3d``."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import hashlib
import json
import os
import subprocess
from pathlib import Path

from argus.core import scroll_ids

SCHEMA = "argus-lasagna-predict3d-plan-v1"
CANDIDATE_SHA = "5ab585ffee54331a3362b9ce6b2929b20dcde2c6"
REQUIRED_FIX_SHA = "a87a53bed382f522551a1098f90c8da28f195fdf"
DEFAULT_SOURCE = Path(_argus_public_path('home', 'worktrees/villa-candidate'))


class LasagnaPlanRefusal(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LasagnaPlanRefusal("candidate git probe failed: %s" % exc) from None
    if proc.returncode:
        raise LasagnaPlanRefusal("candidate git probe refused: %s" % (proc.stderr or proc.stdout).strip())
    return (proc.stdout or "").strip()


def _source_root() -> Path:
    return Path(os.environ.get("ARGUS_VILLA_CANDIDATE", str(DEFAULT_SOURCE))).resolve()


def candidate_status() -> dict:
    root = _source_root()
    if not root.is_dir():
        return {"state": "MISSING_PROVIDER", "source": str(root),
                "why": "the immutable Villa-main candidate worktree is absent"}
    try:
        head = _git(root, "rev-parse", "HEAD")
        contains = _git(root, "merge-base", "--is-ancestor", REQUIRED_FIX_SHA, "HEAD")
        status = _git(root, "status", "--porcelain", "--untracked-files=all")
    except LasagnaPlanRefusal as exc:
        return {"state": "MISSING_PROVIDER", "source": str(root), "why": str(exc)}
    source = root / "lasagna" / "preprocess_cos_omezarr.py"
    text = source.read_text(encoding="utf-8", errors="replace") if source.is_file() else ""
    controls = {
        "head_matches": head == CANDIDATE_SHA,
        "required_fix_is_ancestor": contains == "",
        "source_clean": not bool(status),
        "seven_raw_channels_declared": "NORMAL_RAW_CHANNEL_COUNT = 7" in text,
        "accumulator_uses_raw_channels": "accumulator_channel_count=LasagnaCosPredict3DAdapter.NORMAL_RAW_CHANNEL_COUNT" in text,
    }
    passed = all(controls.values())
    return {
        "state": "REMOTE_ONLY" if passed else "IDENTITY_CONFLICT",
        "source": str(root), "candidate": head, "required_fix": REQUIRED_FIX_SHA,
        "controls": controls,
        "why": ("candidate source and the PR-1752 seven-channel fix are intact; execution requires an isolated Linux/GPU runtime"
                if passed else "candidate source does not match the frozen revision or seven-channel control"),
    }


def plan(*, scroll: str, acquisition_id: str, input_zarr: str, checkpoint: str,
         output_manifest: str, crop_xyzwhd: list[int] | tuple[int, ...] | None = None,
         device: str = "cuda", tile_size: int | None = None, overlap: int = 64,
         border: int = 16, scaledown: int = 4, cos_scaledown: int = 2) -> dict:
    try:
        canonical = scroll_ids.resolve(scroll)
    except KeyError as exc:
        raise LasagnaPlanRefusal(str(exc)) from None
    if not acquisition_id.strip():
        raise LasagnaPlanRefusal("acquisition_id is required")
    status = candidate_status()
    if status["state"] != "REMOTE_ONLY":
        raise LasagnaPlanRefusal(status["why"])
    source = Path(status["source"]) / "lasagna" / "preprocess_cos_omezarr.py"
    checkpoint_path = Path(checkpoint).resolve()
    if not checkpoint_path.is_file():
        raise LasagnaPlanRefusal("checkpoint does not exist: %s" % checkpoint_path)
    output = Path(output_manifest).resolve()
    if output.exists():
        raise LasagnaPlanRefusal("output already exists; candidate runs never overwrite: %s" % output)
    if output.suffix.lower() != ".json":
        raise LasagnaPlanRefusal("output_manifest must be a new .json path")
    if crop_xyzwhd is not None:
        if len(crop_xyzwhd) != 6 or any(int(v) < 0 for v in crop_xyzwhd):
            raise LasagnaPlanRefusal("crop_xyzwhd must contain six non-negative integers")
        crop_xyzwhd = [int(v) for v in crop_xyzwhd]
    for name, value, minimum in (("overlap", overlap, 0), ("border", border, 0),
                                 ("scaledown", scaledown, 1), ("cos_scaledown", cos_scaledown, 1)):
        if int(value) < minimum:
            raise LasagnaPlanRefusal("%s must be >= %d" % (name, minimum))
    argv = ["python3", str(source), "predict3d", "--input", input_zarr,
            "--output", str(output), "--unet-checkpoint", str(checkpoint_path),
            "--device", device, "--overlap", str(int(overlap)), "--border", str(int(border)),
            "--scaledown", str(int(scaledown)), "--cos-scaledown", str(int(cos_scaledown))]
    if tile_size is not None:
        if int(tile_size) <= 0:
            raise LasagnaPlanRefusal("tile_size must be positive")
        argv += ["--tile-size", str(int(tile_size))]
    if crop_xyzwhd is not None:
        argv += ["--crop-xyzwhd", *[str(v) for v in crop_xyzwhd]]
    payload = {
        "schema": SCHEMA, "state": "PLANNED_REMOTE", "read_only": True,
        "source_revision": CANDIDATE_SHA, "required_fix": REQUIRED_FIX_SHA,
        "source": str(source),
        "material": {"physical_scroll": canonical, "acquisition_id": acquisition_id},
        "inputs": {"zarr": input_zarr, "checkpoint": str(checkpoint_path)},
        "output_manifest": str(output), "argv": argv,
        "expected_products": {
            "raw_accumulator_channels": 7,
            "persisted_normal_channels": ["grad_mag", "nx", "ny"],
            "other": ["cos", "pred_dt"],
        },
        "execution": "isolated Linux/GPU worker; never the stable Villa runtime",
        "promotion": "operator only after identity, output completeness, geometry and differential controls",
    }
    payload["plan_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return payload
