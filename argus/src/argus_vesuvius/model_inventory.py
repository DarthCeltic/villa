from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

EXPECTED_MODEL_REPOSITORIES = (
    "ink_canonical_2um",
    "surface_m7_nnunet",
    "dinovol_v2_ps8",
    "dinovol_v2_ps6",
    "fiber_hz_vt",
)

MODEL_REPO_IDS = {
    "ink_canonical_2um": "scrollprize/ink_canonical_2um",
    "surface_m7_nnunet": "scrollprize/surface_m7_nnunet",
    "dinovol_v2_ps8": "scrollprize/dinovol_v2_ps8_with_paris4_352500",
    "dinovol_v2_ps6": "scrollprize/dinovol_v2_ps6_step032350",
    "fiber_hz_vt": "scrollprize/fiber_hz_vt",
}

STANDARD_RUNTIME_FILES = {
    "surface_m7_nnunet": (
        "dataset.json",
        "plans.json",
        "fold_0/checkpoint_best.pth",
    ),
}


def inventory_models(
    root: Path, expected_names: tuple[str, ...] = EXPECTED_MODEL_REPOSITORIES
) -> dict[str, Any]:
    repositories: list[dict[str, Any]] = []
    present: set[str] = set()
    if root.is_dir():
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            present.add(directory.name)
            files = [path for path in directory.rglob("*") if path.is_file()]
            artifact_files = [
                path for path in files if ".cache" not in path.relative_to(directory).parts
            ]
            metadata_files = [path for path in files if path.suffix == ".metadata"]
            revisions: set[str] = set()
            xet_hashes: dict[str, str] = {}
            for metadata_path in metadata_files:
                lines = metadata_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if lines and len(lines[0]) == 40:
                    revisions.add(lines[0])
                if len(lines) >= 2 and len(lines[1]) == 64:
                    metadata_root = directory / ".cache" / "huggingface" / "download"
                    relative_name = metadata_path.relative_to(metadata_root).as_posix()
                    xet_hashes[relative_name.removesuffix(".metadata")] = lines[1]
            incomplete = [
                path for path in files if path.suffix == ".incomplete" or ".incomplete" in path.name
            ]
            required_runtime_files = STANDARD_RUNTIME_FILES.get(directory.name, ())
            missing_runtime_files = [
                relative for relative in required_runtime_files if not (directory / relative).is_file()
            ]
            repositories.append(
                {
                    "name": directory.name,
                    "path": str(directory.resolve()),
                    "repo_id": MODEL_REPO_IDS.get(directory.name),
                    "file_count": len(files),
                    "byte_count": sum(path.stat().st_size for path in files),
                    "artifact_file_count": len(artifact_files),
                    "artifact_byte_count": sum(path.stat().st_size for path in artifact_files),
                    "incomplete_file_count": len(incomplete),
                    "revision": next(iter(revisions)) if len(revisions) == 1 else None,
                    "revision_candidates": sorted(revisions),
                    "xet_hashes": dict(sorted(xet_hashes.items())),
                    "standard_runtime_required_files": list(required_runtime_files),
                    "standard_runtime_missing_files": missing_runtime_files,
                    "standard_runtime_ready": (
                        not missing_runtime_files if required_runtime_files else None
                    ),
                    "complete": (
                        bool(artifact_files)
                        and not incomplete
                        and len(revisions) == 1
                    ),
                    "present": True,
                }
            )
    for missing_name in sorted(set(expected_names) - present):
        repositories.append(
            {
                "name": missing_name,
                "path": str((root / missing_name).resolve()),
                "repo_id": MODEL_REPO_IDS.get(missing_name),
                "file_count": 0,
                "byte_count": 0,
                "artifact_file_count": 0,
                "artifact_byte_count": 0,
                "incomplete_file_count": 0,
                "revision": None,
                "revision_candidates": [],
                "xet_hashes": {},
                "standard_runtime_required_files": list(
                    STANDARD_RUNTIME_FILES.get(missing_name, ())
                ),
                "standard_runtime_missing_files": list(
                    STANDARD_RUNTIME_FILES.get(missing_name, ())
                ),
                "standard_runtime_ready": (
                    False if STANDARD_RUNTIME_FILES.get(missing_name) else None
                ),
                "complete": False,
                "present": False,
            }
        )
    repositories.sort(key=lambda item: item["name"])
    return {
        "schema_version": "argus_vesuvius.model_inventory.v1",
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "root": str(root.resolve()),
        "repository_count": len(repositories),
        "expected_repositories": list(expected_names),
        "missing_repositories": sorted(set(expected_names) - present),
        "total_bytes": sum(item["byte_count"] for item in repositories),
        "all_complete": bool(repositories) and all(item["complete"] for item in repositories),
        "all_standard_runtime_ready": bool(repositories)
        and all(item["standard_runtime_ready"] for item in repositories),
        "repositories": repositories,
    }


def write_inventory(
    root: Path,
    output: Path,
    expected_names: tuple[str, ...] = EXPECTED_MODEL_REPOSITORIES,
) -> dict[str, Any]:
    inventory = inventory_models(root, expected_names)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return inventory
