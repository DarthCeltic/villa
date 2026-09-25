from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def run_experiment(
    command: Sequence[str],
    *,
    metrics_path: Path,
    evidence_path: Path,
    cwd: Path | None = None,
    timeout_seconds: int = 86_400,
) -> dict[str, Any]:
    """Run one bounded experiment."""
    if not command:
        raise ValueError("experiment command cannot be empty")
    if metrics_path.exists():
        raise ValueError(f"refusing to reuse or overwrite existing metrics: {metrics_path}")
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["ARGUS_VESUVIUS_METRICS"] = str(metrics_path.resolve())
    started = time.monotonic()
    started_at = dt.datetime.now(dt.UTC).isoformat()
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd else None,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        timed_out = False
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        return_code = None
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""

    metrics_sha256 = sha256_file(metrics_path) if metrics_path.is_file() else None
    evidence = {
        "schema_version": "argus_vesuvius.run.v1",
        "started_at": started_at,
        "duration_seconds": time.monotonic() - started,
        "command": list(command),
        "cwd": str(cwd.resolve()) if cwd else None,
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "return_code": return_code,
        "metrics_path": str(metrics_path.resolve()),
        "metrics_sha256": metrics_sha256,
        "stdout_tail": stdout[-8000:],
        "stderr_tail": stderr[-8000:],
        "status": "complete" if return_code == 0 and metrics_sha256 else "failed",
    }
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence
