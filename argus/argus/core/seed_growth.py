"""Safe seed selection and transactional vc_grow_seg_from_seed execution."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path

POLICY_SCHEMA = "argus-seed-selection-policy-v1"
QUALIFICATION_SCHEMA = "argus-seed-grower-qualification-v1"
PLAN_SCHEMA = "argus-seed-growth-plan-v1"
RECEIPT_SCHEMA = "argus-seed-growth-receipt-v1"
UPSTREAM_FIX_REVISION = "777cb16cf9208b35897b231843174f2440d9f1f1"
REQUIRED_QUALIFICATION_CHECKS = {
    "missing_voxelsize_is_nonzero_exit",
    "zero_exit_without_output_is_refused",
    "complete_tifxyz_is_promoted",
    "source_artifacts_are_unchanged",
}
REQUIRED_TIFXYZ = {"x.tif", "y.tif", "z.tif", "meta.json"}


class SeedGrowthRefusal(RuntimeError):
    pass


def _sha_bytes(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def default_policy_path() -> Path:
    return Path(__file__).resolve().parents[1] / "seed_growth_policy.json"


def load_policy(path: str | Path | None = None) -> dict:
    source = Path(path) if path else default_policy_path()
    policy = json.loads(source.read_text(encoding="utf-8"))
    if policy.get("schema") != POLICY_SCHEMA:
        raise SeedGrowthRefusal(f"expected {POLICY_SCHEMA}")
    weights = policy.get("score_weights", {})
    if not weights or not math.isclose(sum(float(v) for v in weights.values()), 1.0,
                                       rel_tol=0, abs_tol=1e-9):
        raise SeedGrowthRefusal("seed policy weights must sum to 1")
    return policy


def select_seed(candidates: list[dict], policy: dict | None = None) -> dict:
    """Choose one deterministic, high-support seed or refuse an ambiguous set."""
    policy = dict(policy or load_policy())
    accepted, rejected = [], []
    for index, raw in enumerate(candidates):
        candidate = dict(raw)
        try:
            xyz = [int(candidate[key]) for key in ("x", "y", "z")]
            prediction = float(candidate["prediction"])
            support = float(candidate["neighborhood_support"])
            boundary = float(candidate["boundary_distance_voxels"])
            switch_risk = float(candidate["sheet_switch_risk"])
        except (KeyError, TypeError, ValueError):
            rejected.append({"index": index, "reason": "MALFORMED_CANDIDATE"})
            continue
        reasons = []
        if prediction < float(policy["prediction_threshold"]):
            reasons.append("PREDICTION_BELOW_THRESHOLD")
        if support < float(policy["minimum_neighborhood_support"]):
            reasons.append("NEIGHBORHOOD_NOT_SOLID")
        if boundary < float(policy["minimum_boundary_distance_voxels"]):
            reasons.append("TOO_CLOSE_TO_BOUNDARY")
        if switch_risk > float(policy["maximum_sheet_switch_risk"]):
            reasons.append("SHEET_SWITCH_RISK")
        if reasons:
            rejected.append({"index": index, "xyz": xyz, "reasons": reasons})
            continue
        saturation = float(policy["boundary_distance_saturation_voxels"])
        parts = {
            "prediction": prediction,
            "neighborhood_support": support,
            "boundary_distance": min(boundary / saturation, 1.0),
            "sheet_switch_safety": 1.0 - switch_risk,
        }
        score = sum(float(policy["score_weights"][key]) * value
                    for key, value in parts.items())
        accepted.append({"index": index, "xyz": xyz, "score": score,
                         "score_parts": parts, "candidate": candidate})
    accepted.sort(key=lambda row: (-row["score"], row["xyz"]))
    if not accepted:
        raise SeedGrowthRefusal("NO_SAFE_SEED: every candidate failed the seed policy")
    if len(accepted) > 1:
        margin = accepted[0]["score"] - accepted[1]["score"]
        if margin < float(policy["minimum_winner_margin"]):
            raise SeedGrowthRefusal(
                f"AMBIGUOUS_SEED: winner margin {margin:.6f} is below "
                f"{policy['minimum_winner_margin']}"
            )
    winner = accepted[0]
    return {
        "schema": "argus-seed-decision-v1",
        "policy_id": policy["policy_id"],
        "selected_xyz": winner["xyz"],
        "selected_score": round(winner["score"], 9),
        "selected_candidate": winner["candidate"],
        "eligible_count": len(accepted),
        "rejected": rejected,
        "scientific_boundary": policy["scientific_boundary"],
    }


def _load_qualification(path: Path, executable: Path) -> dict:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("schema") != QUALIFICATION_SCHEMA or receipt.get("verdict") != "PASS":
        raise SeedGrowthRefusal("seed grower has no passing qualification receipt")
    if receipt.get("upstream_revision") != UPSTREAM_FIX_REVISION:
        raise SeedGrowthRefusal("seed grower qualification is not bound to Villa PR 1737 fix")
    if receipt.get("executable_sha256") != _sha_bytes(executable):
        raise SeedGrowthRefusal("seed grower executable differs from its qualification receipt")
    checks = receipt.get("checks", {})
    failed = sorted(name for name in REQUIRED_QUALIFICATION_CHECKS if checks.get(name) is not True)
    if failed:
        raise SeedGrowthRefusal(f"seed grower qualification checks are not passing: {failed}")
    return receipt


def plan(*, executable, qualification_receipt, volume, output_path, params_path,
         candidates: list[dict], source_binding: dict, policy_path=None) -> dict:
    executable = Path(executable).resolve()
    qualification_path = Path(qualification_receipt).resolve()
    params_path = Path(params_path).resolve()
    output = Path(output_path).resolve()
    if not executable.is_file() or not qualification_path.is_file() or not params_path.is_file():
        raise SeedGrowthRefusal("executable, qualification receipt, and params must exist")
    if output.exists() or output == output.parent:
        raise SeedGrowthRefusal("output must be a new non-root path")
    missing_binding = [key for key in ("physical_scroll", "volume_id", "acquisition_id")
                       if not source_binding.get(key)]
    if missing_binding:
        raise SeedGrowthRefusal(f"source binding is missing {missing_binding}")
    qualification = _load_qualification(qualification_path, executable)
    params = json.loads(params_path.read_text(encoding="utf-8"))
    remote = str(volume).startswith(("s3://", "http://", "https://"))
    min_area = float(params.get("min_area_cm", 0.3))
    voxel_size = float(params.get("voxelsize", 0.0))
    if remote and min_area > 0 and not (math.isfinite(voxel_size) and voxel_size > 0):
        raise SeedGrowthRefusal(
            "remote seed growth with min_area_cm > 0 requires explicit positive voxelsize"
        )
    if not remote and not Path(volume).exists():
        raise SeedGrowthRefusal(f"local volume does not exist: {volume}")
    policy = load_policy(policy_path)
    decision = select_seed(candidates, policy)
    body = {
        "schema": PLAN_SCHEMA,
        "executable": str(executable),
        "executable_sha256": qualification["executable_sha256"],
        "qualification_receipt": str(qualification_path),
        "qualification_sha256": _sha_bytes(qualification_path),
        "upstream_revision": qualification["upstream_revision"],
        "volume": str(volume),
        "params_path": str(params_path),
        "params_sha256": _sha_bytes(params_path),
        "output_path": str(output),
        "source_binding": dict(source_binding),
        "seed_decision": decision,
        "controls": [
            "PR 1737 executable hash bound", "explicit voxel size for remote physical threshold",
            "deterministic conservative seed policy", "disposable same-volume staging",
            "zero exit plus complete tifxyz required", "atomic promotion to unused output",
        ],
        "scientific_boundary": (
            "A successful grow is geometry apparatus. Sheet identity, topology, alignment, "
            "rendering, and ink remain separately gated."
        ),
    }
    body["plan_sha256"] = _sha_json(body)
    return body


def _write_receipt(path: Path, body: dict) -> None:
    if path.exists():
        raise SeedGrowthRefusal("seed-growth receipts are append-only")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _complete_tifxyz_candidates(staging: Path) -> list[Path]:
    valid = []
    for meta in staging.rglob("meta.json"):
        parent = meta.parent
        names = {child.name for child in parent.iterdir() if child.is_file()}
        if REQUIRED_TIFXYZ <= names and all((parent / name).stat().st_size > 0
                                            for name in REQUIRED_TIFXYZ):
            valid.append(parent)
    return sorted(valid)


def run(the_plan: dict, *, receipt_path=None, runner=None, timeout_s: int = 7200) -> dict:
    if the_plan.get("schema") != PLAN_SCHEMA:
        raise SeedGrowthRefusal("an ARGUS seed-growth plan is required")
    expected = _sha_json({key: value for key, value in the_plan.items()
                          if key != "plan_sha256"})
    if expected != the_plan.get("plan_sha256"):
        raise SeedGrowthRefusal("seed-growth plan hash does not re-derive")
    executable = Path(the_plan["executable"])
    params = Path(the_plan["params_path"])
    qualification = Path(the_plan["qualification_receipt"])
    output = Path(the_plan["output_path"])
    if output.exists():
        raise SeedGrowthRefusal("seed-growth output is no longer unused")
    if _sha_bytes(executable) != the_plan["executable_sha256"]:
        raise SeedGrowthRefusal("seed-growth executable changed after planning")
    if _sha_bytes(params) != the_plan["params_sha256"]:
        raise SeedGrowthRefusal("seed-growth params changed after planning")
    if _sha_bytes(qualification) != the_plan["qualification_sha256"]:
        raise SeedGrowthRefusal("seed-growth qualification changed after planning")
    receipt = Path(receipt_path).resolve() if receipt_path else output.with_name(
        output.name + ".seed-growth-receipt.json")
    if receipt.exists():
        raise SeedGrowthRefusal("seed-growth receipt already exists")

    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="argus-seed-", dir=str(output.parent)) as temp_name:
        staging = Path(temp_name) / "generated"
        staging.mkdir()
        x, y, z = the_plan["seed_decision"]["selected_xyz"]
        argv = [the_plan["executable"], "--volume", the_plan["volume"],
                "--target-dir", str(staging), "--params", the_plan["params_path"],
                "--seed", str(x), str(y), str(z)]
        invoke = runner or (lambda command: subprocess.run(
            command, capture_output=True, text=True, shell=False, timeout=timeout_s))
        try:
            result = invoke(argv)
            returncode = getattr(result, "returncode", None)
            candidates = _complete_tifxyz_candidates(staging) if returncode == 0 else []
            if returncode != 0:
                reason = f"native grower returned {returncode}"
            elif len(candidates) != 1:
                reason = f"zero exit produced {len(candidates)} complete tifxyz outputs"
            else:
                reason = None
        except Exception as exc:
            result, candidates = None, []
            returncode = None
            reason = f"{type(exc).__name__}: {exc}"

        body = {
            "schema": RECEIPT_SCHEMA,
            "plan_sha256": the_plan["plan_sha256"],
            "upstream_revision": the_plan["upstream_revision"],
            "source_binding": the_plan["source_binding"],
            "seed_decision": the_plan["seed_decision"],
            "argv": argv,
            "returncode": returncode,
            "stdout_tail": (getattr(result, "stdout", "") or "")[-2000:] if result else "",
            "stderr_tail": (getattr(result, "stderr", "") or "")[-2000:] if result else "",
            "duration_s": round(time.time() - started, 3),
            "output_path": str(output),
            "scientific_boundary": the_plan["scientific_boundary"],
            "physical_scroll": the_plan["source_binding"].get("physical_scroll"),
            "acquisition_id": the_plan["source_binding"].get("acquisition_id"),
            "provider": "vc_grow_seg_from_seed",
            "provider_revision": the_plan["upstream_revision"],
        }
        if reason:
            body.update({"status": "REFUSED", "reason": reason, "output_exists": False})
            _write_receipt(receipt, body)
            raise SeedGrowthRefusal(f"seed growth refused: {reason}; receipt: {receipt}")

        os.replace(candidates[0], output)
        body.update({"status": "OK", "output_exists": True,
                     "output_fingerprint": _sha_json([
                         {"path": str(child.relative_to(output)).replace("\\", "/"),
                          "sha256": _sha_bytes(child), "bytes": child.stat().st_size}
                         for child in sorted(output.rglob("*")) if child.is_file()
                     ])})
        _write_receipt(receipt, body)
        return {"status": "OK", "output_path": str(output),
                "receipt_path": str(receipt), "receipt": body}
