"""Measure whether the running interpreter actually matches ARGUS's runtime lock."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def _normal(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def audit(repo_root: Path) -> dict:
    lock_path = repo_root / "runtime" / "requirements.lock.txt"
    freeze_path = repo_root / "runtime" / "RUNTIME_FREEZE.json"
    lock_bytes = lock_path.read_bytes()
    declared: dict[str, tuple[str, str]] = {}
    for raw in lock_bytes.decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        declared[_normal(name)] = (name, version)

    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    missing: list[str] = []
    mismatched: list[dict[str, str]] = []
    for key, (name, expected) in sorted(declared.items()):
        try:
            running = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
            continue
        if running != expected:
            mismatched.append({"package": name, "declared": expected, "running": running})

    lock_sha = hashlib.sha256(lock_bytes).hexdigest()
    freeze_lock = freeze.get("lockfile") or {}
    return {
        "lock_path": str(lock_path),
        "lock_sha256": lock_sha,
        "declared_sha256": freeze_lock.get("sha256"),
        "package_count": len(declared),
        "declared_package_count": freeze_lock.get("n_packages"),
        "missing": missing,
        "mismatched": mismatched,
        "matches": (
            lock_sha == freeze_lock.get("sha256")
            and len(declared) == freeze_lock.get("n_packages")
            and not missing
            and not mismatched
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--require-match", action="store_true")
    args = parser.parse_args()
    result = audit(args.repo_root.resolve())
    print(json.dumps(result, indent=2))
    return 1 if args.require_match and not result["matches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
