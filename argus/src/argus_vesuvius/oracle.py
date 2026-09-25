from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .geometry import Region3D

SCHEMA_VERSION = "argus_vesuvius.experiment.v1"


@dataclass(frozen=True)
class GatePolicy:
    primary_metric: str = "pseudo_label_dice"
    minimum_delta: float = 0.0
    require_deterministic: bool = True
    require_same_evaluation_regions: bool = True
    require_artifact_hashes: bool = True
    require_local_artifacts: bool = True
    minimum_eval_samples: int = 1


@dataclass
class GateDecision:
    decision: str
    accepted: bool
    primary_metric: str
    baseline_score: float | None
    candidate_score: float | None
    delta: float | None
    blockers: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "argus_vesuvius.gate.v1"

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


def load_record(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"experiment record must be an object: {path}")
    return data


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _regions(record: dict[str, Any], key: str, blockers: list[str]) -> list[Region3D]:
    raw = record.get(key)
    if not isinstance(raw, list) or not raw:
        blockers.append(f"{key} must be a non-empty list")
        return []
    parsed: list[Region3D] = []
    for index, item in enumerate(raw):
        try:
            if not isinstance(item, dict):
                raise ValueError("entry must be an object")
            parsed.append(Region3D.from_mapping(item))
        except (TypeError, ValueError) as exc:
            blockers.append(f"{key}[{index}] invalid: {exc}")
    return parsed


def _score(record: dict[str, Any], metric: str, blockers: list[str], label: str) -> float | None:
    metrics = record.get("metrics")
    if not isinstance(metrics, dict):
        blockers.append(f"{label}.metrics must be an object")
        return None
    raw = metrics.get(metric)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        blockers.append(f"{label}.metrics.{metric} must be numeric")
        return None
    if not math.isfinite(value):
        blockers.append(f"{label}.metrics.{metric} must be finite")
        return None
    if "dice" in metric.lower() and not 0.0 <= value <= 1.0:
        blockers.append(f"{label}.metrics.{metric} must be between 0 and 1")
        return None
    return value


def _validate_record(
    record: dict[str, Any], label: str, policy: GatePolicy, blockers: list[str]
) -> tuple[list[Region3D], list[Region3D], float | None]:
    if record.get("schema_version") != SCHEMA_VERSION:
        blockers.append(f"{label}.schema_version must be {SCHEMA_VERSION}")
    if record.get("status") != "complete":
        blockers.append(f"{label}.status must be complete")
    for field_name in ("experiment_id", "code_revision", "config_sha256"):
        if not str(record.get(field_name, "")).strip():
            blockers.append(f"{label}.{field_name} is required")
    if policy.require_deterministic:
        if record.get("deterministic") is not True:
            blockers.append(f"{label}.deterministic must be true")
        if not isinstance(record.get("seed"), int):
            blockers.append(f"{label}.seed must be an integer")
    if policy.require_artifact_hashes:
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            blockers.append(f"{label}.artifacts must contain at least one hashed artifact")
        else:
            for index, artifact in enumerate(artifacts):
                sha = artifact.get("sha256") if isinstance(artifact, dict) else None
                if not isinstance(sha, str) or len(sha) != 64:
                    blockers.append(f"{label}.artifacts[{index}].sha256 must be 64 hex characters")
                else:
                    try:
                        int(sha, 16)
                    except ValueError:
                        blockers.append(f"{label}.artifacts[{index}].sha256 is not hexadecimal")
                if policy.require_local_artifacts:
                    raw_path = artifact.get("path") if isinstance(artifact, dict) else None
                    if not isinstance(raw_path, str) or not raw_path.strip():
                        blockers.append(f"{label}.artifacts[{index}].path is required")
                    else:
                        artifact_path = Path(raw_path)
                        if not artifact_path.is_file():
                            blockers.append(
                                f"{label}.artifacts[{index}].path does not exist: {artifact_path}"
                            )
                        elif isinstance(sha, str) and len(sha) == 64:
                            digest = hashlib.sha256()
                            with artifact_path.open("rb") as handle:
                                while chunk := handle.read(8 * 1024 * 1024):
                                    digest.update(chunk)
                            if digest.hexdigest() != sha.lower():
                                blockers.append(
                                    f"{label}.artifacts[{index}] content does not match sha256"
                                )
    metrics = record.get("metrics", {})
    eval_samples = metrics.get("eval_samples") if isinstance(metrics, dict) else None
    if not isinstance(eval_samples, int) or eval_samples < policy.minimum_eval_samples:
        blockers.append(
            f"{label}.metrics.eval_samples must be >= {policy.minimum_eval_samples}"
        )
    train = _regions(record, "training_regions", blockers)
    evaluation = _regions(record, "evaluation_regions", blockers)
    for train_region in train:
        for eval_region in evaluation:
            if train_region.overlaps(eval_region):
                blockers.append(
                    "spatial data leakage: training region "
                    f"{train_region.to_mapping()} overlaps evaluation region {eval_region.to_mapping()}"
                )
    return train, evaluation, _score(record, policy.primary_metric, blockers, label)


def gate_experiment(
    baseline: dict[str, Any], candidate: dict[str, Any], policy: GatePolicy | None = None
) -> GateDecision:
    policy = policy or GatePolicy()
    blockers: list[str] = []
    regressions: list[str] = []
    _, baseline_eval, baseline_score = _validate_record(baseline, "baseline", policy, blockers)
    _, candidate_eval, candidate_score = _validate_record(candidate, "candidate", policy, blockers)

    baseline_eval_set = set(baseline_eval)
    candidate_eval_set = set(candidate_eval)
    if policy.require_same_evaluation_regions and baseline_eval_set != candidate_eval_set:
        blockers.append("candidate evaluation regions differ from the baseline evaluation regions")

    baseline_samples = baseline.get("metrics", {}).get("eval_samples", 0)
    candidate_samples = candidate.get("metrics", {}).get("eval_samples", 0)
    if isinstance(baseline_samples, int) and isinstance(candidate_samples, int):
        if candidate_samples < baseline_samples:
            regressions.append(
                f"evaluation sample count dropped from {baseline_samples} to {candidate_samples}"
            )

    delta = None
    if baseline_score is not None and candidate_score is not None:
        delta = candidate_score - baseline_score
        if delta < policy.minimum_delta:
            regressions.append(
                f"{policy.primary_metric} delta {delta:.12g} is below required {policy.minimum_delta:.12g}"
            )

    if blockers:
        decision = "blocked"
    elif regressions:
        decision = "reject"
    else:
        decision = "accept"

    return GateDecision(
        decision=decision,
        accepted=decision == "accept",
        primary_metric=policy.primary_metric,
        baseline_score=baseline_score,
        candidate_score=candidate_score,
        delta=delta,
        blockers=blockers,
        regressions=regressions,
        evidence={
            "baseline_experiment_id": baseline.get("experiment_id"),
            "candidate_experiment_id": candidate.get("experiment_id"),
            "baseline_record_sha256": canonical_sha256(baseline),
            "candidate_record_sha256": canonical_sha256(candidate),
            "evaluation_region_count": len(candidate_eval_set),
            "policy": asdict(policy),
        },
    )


def write_decision(path: Path, decision: GateDecision) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decision.to_mapping(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
