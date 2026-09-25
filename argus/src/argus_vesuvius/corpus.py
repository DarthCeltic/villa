from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from .oracle import GateDecision, canonical_sha256


def append_gate_event(
    path: Path,
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    decision: GateDecision,
) -> dict[str, Any]:
    """Append a gate event without silently promoting raw experiment output to training truth."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "argus_vesuvius.corpus_event.v1",
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "kind": "experiment_gate",
        "decision": decision.decision,
        "accepted": decision.accepted,
        "promotion_eligible": decision.accepted,
        "training_eligible": False,
        "promotion_requirement": (
            "independent verifier promotion into a proof-bearing ARGUS corpus surface"
        ),
        "baseline_experiment_id": baseline.get("experiment_id"),
        "candidate_experiment_id": candidate.get("experiment_id"),
        "baseline_record_sha256": canonical_sha256(baseline),
        "candidate_record_sha256": canonical_sha256(candidate),
        "gate": decision.to_mapping(),
    }
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    descriptor = os.open(path, flags, 0o644)
    try:
        os.write(descriptor, line.encode("utf-8"))
    finally:
        os.close(descriptor)
    return record
