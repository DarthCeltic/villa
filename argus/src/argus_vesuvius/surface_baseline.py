from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .geometry import Region3D
from .pair import load_manifest
from .volume import read_roi


def surface_dice(prediction, label) -> dict[str, Any]:
    import numpy as np

    prediction = np.asarray(prediction)
    label = np.asarray(label)
    if prediction.shape != label.shape:
        raise ValueError(f"prediction/label shape mismatch: {prediction.shape} != {label.shape}")
    valid = label != 2
    truth = (label == 1) & valid
    predicted = (prediction == 1) & valid
    intersection = int(np.count_nonzero(truth & predicted))
    truth_count = int(np.count_nonzero(truth))
    predicted_count = int(np.count_nonzero(predicted))
    denominator = truth_count + predicted_count
    dice = 1.0 if denominator == 0 else (2.0 * intersection) / denominator
    return {
        "surface_dice": float(dice),
        "valid_voxels": int(np.count_nonzero(valid)),
        "ignored_voxels": int(np.count_nonzero(~valid)),
        "truth_surface_voxels": truth_count,
        "predicted_surface_voxels": predicted_count,
        "intersection_voxels": intersection,
    }


def verify_checkpoint_pin(path: Path, expected_sha256: str) -> str:
    """The checkpoint is a pickle: loading it runs whatever code it names."""
    expected = (expected_sha256 or "").strip().lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise RuntimeError("refusing to unpickle an unpinned checkpoint: pass --checkpoint-sha256 <64 hex characters>")
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise RuntimeError("checkpoint sha256 %s does not match the pin %s; not loading it" % (digest.hexdigest(), expected))
    return expected


def _load_predictor(model_dir: Path, checkpoint_sha256: str):
    import inspect

    import torch
    from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
    from nnunetv2.utilities.find_class_by_name import recursive_find_python_class
    from nnunetv2.utilities.label_handling.label_handling import determine_num_input_channels
    from nnunetv2.utilities.plans_handling.plans_handler import PlansManager
    import nnunetv2.training.nnUNetTrainer as trainer_package

    checkpoint_path = model_dir / "fold_0" / "checkpoint_best.pth"
    verify_checkpoint_pin(checkpoint_path, checkpoint_sha256)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    init_args = checkpoint["init_args"]
    plans_manager = PlansManager(init_args["plans"])
    configuration_manager = plans_manager.get_configuration(init_args["configuration"])
    dataset_json = init_args["dataset_json"]
    trainer_class = recursive_find_python_class(
        trainer_package.__path__[0], checkpoint["trainer_name"], "nnunetv2.training.nnUNetTrainer"
    )
    if trainer_class is None:
        raise RuntimeError(f"could not resolve trainer {checkpoint['trainer_name']}")
    input_channels = determine_num_input_channels(
        plans_manager, configuration_manager, dataset_json
    )
    output_channels = plans_manager.get_label_manager(dataset_json).num_segmentation_heads
    signature = inspect.signature(trainer_class.build_network_architecture)
    if "plans_manager" in signature.parameters:
        network = trainer_class.build_network_architecture(
            plans_manager,
            configuration_manager,
            input_channels,
            output_channels,
            enable_deep_supervision=False,
        )
    else:
        network = trainer_class.build_network_architecture(
            configuration_manager.network_arch_class_name,
            configuration_manager.network_arch_init_kwargs,
            configuration_manager.network_arch_init_kwargs_req_import,
            input_channels,
            output_channels,
            enable_deep_supervision=False,
        )
    network.load_state_dict(checkpoint["network_weights"])
    predictor = nnUNetPredictor(
        tile_step_size=1.0,
        use_gaussian=False,
        use_mirroring=False,
        perform_everything_on_device=False,
        device=torch.device("cpu"),
        verbose=False,
        verbose_preprocessing=False,
        allow_tqdm=False,
    )
    predictor.manual_initialization(
        network,
        plans_manager,
        configuration_manager,
        [checkpoint["network_weights"]],
        dataset_json,
        checkpoint["trainer_name"],
        checkpoint.get("inference_allowed_mirroring_axes"),
    )
    return predictor, checkpoint_path


def run_surface_compatibility_baseline(
    *,
    model_dir: Path,
    input_manifest_path: Path,
    label_manifest_path: Path,
    pair_manifest_path: Path,
    output_dir: Path,
    checkpoint_sha256: str,
) -> dict[str, Any]:
    import numpy as np

    input_manifest = load_manifest(input_manifest_path)
    label_manifest = load_manifest(label_manifest_path)
    pair_manifest = load_manifest(pair_manifest_path)
    if pair_manifest.get("status") != "paired":
        raise ValueError("pair manifest is not verified")
    bbox = tuple(pair_manifest["bbox_zyx"])
    input_region = Region3D.from_bbox(input_manifest["region"]["volume_id"], bbox)
    label_region = Region3D.from_bbox(label_manifest["region"]["volume_id"], bbox)
    input_roi = read_roi(input_manifest["uri"], input_region, input_manifest.get("dataset"))
    label_roi = read_roi(label_manifest["uri"], label_region, label_manifest.get("dataset"))
    if list(input_roi.shape) != pair_manifest["shape"] or input_roi.shape != label_roi.shape:
        raise ValueError("decoded ROI shape no longer matches verified pair manifest")

    predictor, checkpoint_path = _load_predictor(model_dir, checkpoint_sha256)
    started = time.perf_counter()
    prediction = predictor.predict_single_npy_array(
        np.asarray(input_roi, dtype=np.float32)[None],
        {"spacing": (1.0, 1.0, 1.0)},
        save_or_return_probabilities=False,
    )
    elapsed = time.perf_counter() - started
    output_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = output_dir / "surface_prediction.npy"
    np.save(prediction_path, np.asarray(prediction, dtype=np.uint8), allow_pickle=False)
    metrics = surface_dice(prediction, label_roi)
    result = {
        "schema_version": "argus_vesuvius.surface_compatibility.v1",
        "status": "complete",
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "model": "scrollprize/surface_m7_nnunet",
        "checkpoint": str(checkpoint_path.resolve()),
        "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        "pair_manifest": str(pair_manifest_path.resolve()),
        "pair_sha256": pair_manifest["pair_sha256"],
        "bbox_zyx": list(bbox),
        "shape": list(prediction.shape),
        "source_multiscale_level": (input_manifest.get("region") or {}).get("level"),
        "resolution_compatible": None,
        "metric_status": "diagnostic_only",
        "elapsed_seconds": elapsed,
        "metrics": metrics,
        "prediction": {
            "path": str(prediction_path.resolve()),
            "sha256": hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
        },
        "training_eligible": False,
        "promotion_eligible": False,
    }
    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def run_curated_surface_baseline(
    *,
    model_dir: Path,
    image_path: Path,
    label_path: Path,
    sample_id: str,
    bbox_zyx: tuple[int, int, int, int, int, int],
    output_dir: Path,
    checkpoint_sha256: str,
    seed: int = 1729,
) -> dict[str, Any]:
    import numpy as np
    import tifffile

    from .diagnostic_baselines import compare_segmentation_baselines

    image = tifffile.imread(image_path)
    label = tifffile.imread(label_path)
    if image.shape != label.shape or image.ndim != 3:
        raise ValueError(f"curated image/label shape mismatch: {image.shape} != {label.shape}")
    z0, y0, x0, z1, y1, x1 = bbox_zyx
    if min(bbox_zyx) < 0 or z1 > image.shape[0] or y1 > image.shape[1] or x1 > image.shape[2]:
        raise ValueError(f"bbox {bbox_zyx} exceeds curated sample shape {image.shape}")
    if z1 <= z0 or y1 <= y0 or x1 <= x0:
        raise ValueError("bbox must have positive extent")
    image_roi = np.asarray(image[z0:z1, y0:y1, x0:x1])
    label_roi = np.asarray(label[z0:z1, y0:y1, x0:x1])

    predictor, checkpoint_path = _load_predictor(model_dir, checkpoint_sha256)
    started = time.perf_counter()
    prediction = predictor.predict_single_npy_array(
        np.asarray(image_roi, dtype=np.float32)[None],
        {"spacing": (1.0, 1.0, 1.0)},
        save_or_return_probabilities=False,
    )
    elapsed = time.perf_counter() - started
    output_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = output_dir / "surface_prediction.npy"
    np.save(prediction_path, np.asarray(prediction, dtype=np.uint8), allow_pickle=False)
    diagnostics = compare_segmentation_baselines(prediction, label_roi, seed=seed)
    result = {
        "schema_version": "argus_vesuvius.curated_surface_baseline.v1",
        "status": "complete",
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "sample_id": sample_id,
        "source": "hf://buckets/scrollprize/datasets/surfaces/kaggle",
        "image": {
            "path": str(image_path.resolve()),
            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        },
        "label": {
            "path": str(label_path.resolve()),
            "sha256": hashlib.sha256(label_path.read_bytes()).hexdigest(),
        },
        "bbox_zyx": list(bbox_zyx),
        "shape": list(prediction.shape),
        "model": "scrollprize/surface_m7_nnunet",
        "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        "elapsed_seconds": elapsed,
        "resolution_compatible": True,
        "split_assignment": "unknown",
        "metric_status": "diagnostic_until_official_split_is_verified",
        "diagnostics": diagnostics,
        "prediction": {
            "path": str(prediction_path.resolve()),
            "sha256": hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
        },
        "training_eligible": False,
        "promotion_eligible": False,
        "promotion_blockers": [
            "official train/evaluation split for this sample is not locally verified"
        ],
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
