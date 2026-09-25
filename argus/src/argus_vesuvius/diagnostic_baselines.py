from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


def compare_segmentation_baselines(prediction, label, *, seed: int = 1729) -> dict[str, Any]:
    import numpy as np

    prediction = np.asarray(prediction)
    label = np.asarray(label)
    if prediction.shape != label.shape:
        raise ValueError(f"prediction/label shape mismatch: {prediction.shape} != {label.shape}")

    valid = label != 2
    truth = (label == 1) & valid
    model = (prediction == 1) & valid
    valid_count = int(np.count_nonzero(valid))
    truth_count = int(np.count_nonzero(truth))
    model_count = int(np.count_nonzero(model))
    if not valid_count:
        raise ValueError("label has no non-ignore voxels")

    prevalence = truth_count / valid_count
    model_prevalence = model_count / valid_count

    def dice(mask) -> float:
        mask = np.asarray(mask, dtype=bool) & valid
        predicted_count = int(np.count_nonzero(mask))
        intersection = int(np.count_nonzero(mask & truth))
        denominator = truth_count + predicted_count
        return 1.0 if denominator == 0 else (2.0 * intersection) / denominator

    rng = np.random.default_rng(seed)
    random_valid = np.zeros(valid_count, dtype=bool)
    if model_count:
        random_valid[rng.choice(valid_count, size=model_count, replace=False)] = True
    random_mask = np.zeros(label.shape, dtype=bool)
    random_mask[valid] = random_valid

    all_positive_dice = dice(valid)
    all_negative_dice = dice(np.zeros(label.shape, dtype=bool))
    model_dice = dice(model)
    seeded_random_dice = dice(random_mask)
    expected_random_dice = (
        0.0
        if prevalence + model_prevalence == 0
        else (2.0 * prevalence * model_prevalence) / (prevalence + model_prevalence)
    )
    beats_all_positive = model_dice > all_positive_dice
    return {
        "schema_version": "argus_vesuvius.segmentation_diagnostics.v1",
        "seed": seed,
        "valid_voxels": valid_count,
        "ignored_voxels": int(np.count_nonzero(~valid)),
        "truth_surface_voxels": truth_count,
        "label_prevalence": prevalence,
        "model_surface_voxels": model_count,
        "model_output_prevalence": model_prevalence,
        "dice": {
            "model": model_dice,
            "all_positive": all_positive_dice,
            "all_negative": all_negative_dice,
            "random_expected_at_model_prevalence": expected_random_dice,
            "random_seeded_exact_count": seeded_random_dice,
        },
        "delta": {
            "model_minus_all_positive": model_dice - all_positive_dice,
            "model_minus_random_expected": model_dice - expected_random_dice,
            "model_minus_random_seeded": model_dice - seeded_random_dice,
        },
        "verdict": (
            "beats_trivial_all_positive"
            if beats_all_positive
            else "does_not_beat_trivial_all_positive_on_dice"
        ),
        "claim_boundary": (
            "This comparison evaluates one patch only and is diagnostic: it compares the "
            "model with trivial baselines on one metric and makes no broader claim."
        ),
        "training_eligible": False,
        "promotion_eligible": False,
    }


def write_diagnostic_evidence(
    prediction_path: Path,
    label,
    output_path: Path,
    *,
    seed: int = 1729,
) -> dict[str, Any]:
    import numpy as np

    prediction = np.load(prediction_path, allow_pickle=False)
    result = compare_segmentation_baselines(prediction, label, seed=seed)
    result["captured_at"] = dt.datetime.now(dt.UTC).isoformat()
    result["prediction"] = {
        "path": str(prediction_path.resolve()),
        "sha256": hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
