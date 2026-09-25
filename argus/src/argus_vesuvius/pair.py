from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .oracle import canonical_sha256


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"ROI manifest must be an object: {path}")
    return value


def verify_paired_roi(
    input_manifest: dict[str, Any],
    label_manifest: dict[str, Any],
    *,
    sample_id: str,
) -> dict[str, Any]:
    if not sample_id.strip():
        raise ValueError("sample_id must be non-empty")
    expected_schema = "argus_vesuvius.volume_roi.v1"
    for name, manifest in (("input", input_manifest), ("label", label_manifest)):
        if manifest.get("schema_version") != expected_schema:
            raise ValueError(f"{name} manifest must use {expected_schema}")
        if not isinstance(manifest.get("content_sha256"), str):
            raise ValueError(f"{name} manifest lacks content_sha256")
    input_region = input_manifest.get("region", {}).get("bbox_zyx")
    label_region = label_manifest.get("region", {}).get("bbox_zyx")
    if input_region != label_region:
        raise ValueError(f"input/label bbox mismatch: {input_region} != {label_region}")
    if input_manifest.get("shape") != label_manifest.get("shape"):
        raise ValueError(
            f"input/label shape mismatch: {input_manifest.get('shape')} != {label_manifest.get('shape')}"
        )
    if input_manifest.get("voxel_count") != label_manifest.get("voxel_count"):
        raise ValueError("input/label voxel counts differ")
    evidence = {
        "schema_version": "argus_vesuvius.paired_roi.v1",
        "verified_at": dt.datetime.now(dt.UTC).isoformat(),
        "status": "paired",
        "sample_id": sample_id,
        "bbox_zyx": input_region,
        "shape": input_manifest.get("shape"),
        "voxel_count": input_manifest.get("voxel_count"),
        "input": {
            "volume_id": input_manifest.get("region", {}).get("volume_id"),
            "uri": input_manifest.get("uri"),
            "dataset": input_manifest.get("dataset"),
            "content_sha256": input_manifest.get("content_sha256"),
            "manifest_sha256": canonical_sha256(input_manifest),
        },
        "label": {
            "volume_id": label_manifest.get("region", {}).get("volume_id"),
            "uri": label_manifest.get("uri"),
            "dataset": label_manifest.get("dataset"),
            "content_sha256": label_manifest.get("content_sha256"),
            "manifest_sha256": canonical_sha256(label_manifest),
        },
        "training_eligible": False,
        "promotion_requirement": "model-specific preprocessing and verifier-reviewed split assignment",
    }
    evidence["pair_sha256"] = canonical_sha256(evidence)
    return evidence


def write_pair(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
