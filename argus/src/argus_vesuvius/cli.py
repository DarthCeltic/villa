from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .corpus import append_gate_event
from .diagnostic_baselines import write_diagnostic_evidence
from .geometry import Region3D
from .model_inventory import EXPECTED_MODEL_REPOSITORIES, write_inventory
from .oracle import GatePolicy, gate_experiment, load_record, write_decision
from .pair import load_manifest, verify_paired_roi, write_pair
from .runner import run_experiment
from .surface_baseline import run_curated_surface_baseline, run_surface_compatibility_baseline
from .volume import summarize_roi
from .volume import read_roi


def _json_print(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _artifact(name: str, path: Path) -> dict[str, str]:
    return {
        "name": name,
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _smoke_record(experiment_id: str, score: float, artifact_path: Path) -> dict:
    return {
        "schema_version": "argus_vesuvius.experiment.v1",
        "experiment_id": experiment_id,
        "status": "complete",
        "code_revision": "synthetic-smoke",
        "config_sha256": hashlib.sha256(experiment_id.encode("utf-8")).hexdigest(),
        "seed": 1729,
        "deterministic": True,
        "training_regions": [
            {"volume_id": "synthetic-scroll", "bbox_zyx": [0, 0, 0, 8, 8, 8]}
        ],
        "evaluation_regions": [
            {"volume_id": "synthetic-scroll", "bbox_zyx": [8, 0, 0, 16, 8, 8]}
        ],
        "metrics": {"pseudo_label_dice": score, "eval_samples": 512},
        "artifacts": [_artifact(experiment_id, artifact_path)],
    }


def cmd_gate(args: argparse.Namespace) -> int:
    baseline = load_record(args.baseline)
    candidate = load_record(args.candidate)
    policy = GatePolicy(
        primary_metric=args.primary_metric,
        minimum_delta=args.minimum_delta,
        require_deterministic=not args.allow_nondeterministic,
        require_same_evaluation_regions=not args.allow_eval_region_change,
        require_artifact_hashes=not args.allow_unhashed_artifacts,
        require_local_artifacts=not args.allow_unverified_artifacts,
        minimum_eval_samples=args.minimum_eval_samples,
    )
    decision = gate_experiment(baseline, candidate, policy)
    write_decision(args.output, decision)
    if args.corpus:
        append_gate_event(
            args.corpus, baseline=baseline, candidate=candidate, decision=decision
        )
    _json_print(decision.to_mapping())
    return {"accept": 0, "reject": 1, "blocked": 2}[decision.decision]


def cmd_inspect_volume(args: argparse.Namespace) -> int:
    bbox = tuple(int(value) for value in args.bbox.split(":"))
    region = Region3D.from_bbox(args.volume_id, bbox)
    summary = summarize_roi(args.uri, region, args.dataset)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _json_print(summary)
    return 0


def cmd_inventory_models(args: argparse.Namespace) -> int:
    expected = tuple(args.expected) if args.expected else EXPECTED_MODEL_REPOSITORIES
    inventory = write_inventory(args.root, args.output, expected)
    _json_print(inventory)
    return 0 if inventory["all_complete"] else 2


def cmd_pair(args: argparse.Namespace) -> int:
    evidence = verify_paired_roi(
        load_manifest(args.input),
        load_manifest(args.label),
        sample_id=args.sample_id,
    )
    write_pair(args.output, evidence)
    _json_print(evidence)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    evidence = run_experiment(
        command,
        metrics_path=args.metrics,
        evidence_path=args.evidence,
        cwd=args.cwd,
        timeout_seconds=args.timeout,
    )
    _json_print(evidence)
    return 0 if evidence["status"] == "complete" else 2


def cmd_surface_baseline(args: argparse.Namespace) -> int:
    result = run_surface_compatibility_baseline(
        model_dir=args.model_dir,
        input_manifest_path=args.input,
        label_manifest_path=args.label,
        pair_manifest_path=args.pair,
        output_dir=args.output_dir,
        checkpoint_sha256=args.checkpoint_sha256,
    )
    _json_print(result)
    return 0


def cmd_diagnostic_baselines(args: argparse.Namespace) -> int:
    label_manifest = load_manifest(args.label)
    region = Region3D.from_bbox(
        label_manifest["region"]["volume_id"], label_manifest["region"]["bbox_zyx"]
    )
    label = read_roi(label_manifest["uri"], region, label_manifest.get("dataset"))
    result = write_diagnostic_evidence(args.prediction, label, args.output, seed=args.seed)
    _json_print(result)
    return 0


def cmd_curated_surface_baseline(args: argparse.Namespace) -> int:
    bbox = tuple(int(value) for value in args.bbox.split(":"))
    result = run_curated_surface_baseline(
        model_dir=args.model_dir,
        image_path=args.image,
        label_path=args.label,
        sample_id=args.sample_id,
        bbox_zyx=bbox,
        output_dir=args.output_dir,
        checkpoint_sha256=args.checkpoint_sha256,
        seed=args.seed,
    )
    _json_print(result)
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    baseline_artifact = args.output_dir / "baseline_artifact.bin"
    candidate_artifact = args.output_dir / "candidate_artifact.bin"
    baseline_artifact.write_bytes(b"argus-smoke-baseline\n")
    candidate_artifact.write_bytes(b"argus-smoke-candidate\n")
    baseline = _smoke_record("smoke-baseline", 0.42, baseline_artifact)
    candidate = _smoke_record("smoke-candidate", 0.48, candidate_artifact)
    baseline_path = args.output_dir / "baseline.json"
    candidate_path = args.output_dir / "candidate.json"
    decision_path = args.output_dir / "gate_result.json"
    corpus_path = args.output_dir / "corpus_events.jsonl"
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    decision = gate_experiment(baseline, candidate, GatePolicy(minimum_delta=0.01))
    write_decision(decision_path, decision)
    append_gate_event(corpus_path, baseline=baseline, candidate=candidate, decision=decision)
    result = {
        "status": "passed" if decision.accepted else "failed",
        "decision": decision.to_mapping(),
        "artifacts": {
            "baseline": str(baseline_path),
            "candidate": str(candidate_path),
            "gate": str(decision_path),
            "corpus": str(corpus_path),
        },
    }
    _json_print(result)
    return 0 if decision.accepted else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="argus")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    gate = subparsers.add_parser("gate", help="compare an experiment with its baseline")
    gate.add_argument("baseline", type=Path)
    gate.add_argument("candidate", type=Path)
    gate.add_argument("--output", type=Path, required=True)
    gate.add_argument("--corpus", type=Path)
    gate.add_argument("--primary-metric", default="pseudo_label_dice")
    gate.add_argument("--minimum-delta", type=float, default=0.0)
    gate.add_argument("--minimum-eval-samples", type=int, default=1)
    gate.add_argument("--allow-nondeterministic", action="store_true")
    gate.add_argument("--allow-eval-region-change", action="store_true")
    gate.add_argument("--allow-unhashed-artifacts", action="store_true")
    gate.add_argument("--allow-unverified-artifacts", action="store_true")
    gate.set_defaults(handler=cmd_gate)

    inspect_volume = subparsers.add_parser("inspect-volume", help="read and hash one ROI")
    inspect_volume.add_argument("uri")
    inspect_volume.add_argument("--volume-id", required=True)
    inspect_volume.add_argument("--bbox", required=True, help="z0:y0:x0:z1:y1:x1")
    inspect_volume.add_argument("--dataset")
    inspect_volume.add_argument("--output", type=Path)
    inspect_volume.set_defaults(handler=cmd_inspect_volume)

    inventory = subparsers.add_parser("inventory-models", help="record model download state")
    inventory.add_argument("root", type=Path)
    inventory.add_argument("--output", type=Path, required=True)
    inventory.add_argument(
        "--expected",
        action="append",
        help="expected direct child directory; repeat for a custom repository set",
    )
    inventory.set_defaults(handler=cmd_inventory_models)

    pair = subparsers.add_parser("pair", help="verify aligned input and label ROI manifests")
    pair.add_argument("--input", type=Path, required=True)
    pair.add_argument("--label", type=Path, required=True)
    pair.add_argument("--sample-id", required=True)
    pair.add_argument("--output", type=Path, required=True)
    pair.set_defaults(handler=cmd_pair)

    run = subparsers.add_parser("run", help="run one bounded external experiment")
    run.add_argument("--metrics", type=Path, required=True)
    run.add_argument("--evidence", type=Path, required=True)
    run.add_argument("--cwd", type=Path)
    run.add_argument("--timeout", type=int, default=86_400)
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(handler=cmd_run)

    surface = subparsers.add_parser(
        "surface-baseline", help="run the official surface nnU-Net compatibility smoke"
    )
    surface.add_argument("--model-dir", type=Path, required=True)
    surface.add_argument("--input", type=Path, required=True)
    surface.add_argument("--label", type=Path, required=True)
    surface.add_argument("--pair", type=Path, required=True)
    surface.add_argument("--output-dir", type=Path, required=True)
    surface.add_argument("--checkpoint-sha256", required=True, help="pinned sha256 of fold_0/checkpoint_best.pth; verified before the checkpoint is unpickled")
    surface.set_defaults(handler=cmd_surface_baseline)

    diagnostics = subparsers.add_parser(
        "diagnostic-baselines", help="compare a segmentation with deterministic trivial baselines"
    )
    diagnostics.add_argument("--prediction", type=Path, required=True)
    diagnostics.add_argument("--label", type=Path, required=True)
    diagnostics.add_argument("--output", type=Path, required=True)
    diagnostics.add_argument("--seed", type=int, default=1729)
    diagnostics.set_defaults(handler=cmd_diagnostic_baselines)

    curated = subparsers.add_parser(
        "curated-surface-baseline", help="run a fixed native-resolution curated surface crop"
    )
    curated.add_argument("--model-dir", type=Path, required=True)
    curated.add_argument("--image", type=Path, required=True)
    curated.add_argument("--label", type=Path, required=True)
    curated.add_argument("--sample-id", required=True)
    curated.add_argument("--bbox", default="64:64:64:256:256:256")
    curated.add_argument("--output-dir", type=Path, required=True)
    curated.add_argument("--checkpoint-sha256", required=True, help="pinned sha256 of fold_0/checkpoint_best.pth; verified before the checkpoint is unpickled")
    curated.add_argument("--seed", type=int, default=1729)
    curated.set_defaults(handler=cmd_curated_surface_baseline)

    smoke = subparsers.add_parser("smoke", help="exercise the complete gate/corpus path")
    smoke.add_argument("--output-dir", type=Path, default=Path("artifacts/smoke"))
    smoke.set_defaults(handler=cmd_smoke)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
