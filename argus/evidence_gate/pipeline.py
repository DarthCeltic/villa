"""Dependency-light, offline evidence checks."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "argus-evidence-gate-v1"
REFUSAL = "REFUSE_TO_INTERPRET"


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    reason: str


@dataclass(frozen=True)
class EvidenceReport:
    schema: str
    generated_utc: str
    status: str
    offline: bool
    network_used: bool
    cuda_used: bool
    command: str
    argv: list[str]
    inputs: dict[str, Any]
    gates: list[GateResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_argv(argv: list[str]) -> list[str]:
    """Retain reproducibility without copying credentials into a receipt."""
    sensitive = ("token", "secret", "password", "credential", "api-key", "apikey")
    out: list[str] = []
    redact = False
    for item in argv:
        lower = item.lower()
        if redact:
            out.append("<redacted>")
            redact = False
        elif any(word in lower for word in sensitive) and "=" not in item:
            out.append(item)
            redact = True
        elif any(word in lower for word in sensitive) and "=" in item:
            out.append(item.split("=", 1)[0] + "=<redacted>")
        else:
            out.append(item)
    return out


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def evaluate_payload(payload: dict[str, Any], *, command: str = "evidence-gate check",
                     argv: list[str] | None = None, inputs: dict[str, Any] | None = None) -> EvidenceReport:
    """Evaluate a saved evidence payload; missing evidence refuses, contradictions fail."""
    gates: list[GateResult] = []

    bbox = payload.get("mesh_bbox")
    volume = payload.get("volume_bounds")
    contained = bool(payload.get("mesh_contained"))
    if bbox is None or volume is None:
        gates.append(GateResult("geometry_containment", REFUSAL,
                                "mesh and volume bounds were not both recorded"))
    else:
        gates.append(GateResult("geometry_containment", "FAIL" if not contained else "PASS",
                                "mesh lies outside the declared volume" if not contained
                                else "mesh bounds are contained in the declared volume"))

    support = _number(payload.get("support_nonfill_fraction"))
    if support is None:
        gates.append(GateResult("ct_support", REFUSAL, "support measurement is absent"))
    else:
        gates.append(GateResult("ct_support", "FAIL" if support <= 0 else "PASS",
                                "sampled support is entirely fill value" if support <= 0
                                else f"non-fill support fraction {support:g}"))

    seam = _number(payload.get("seam_max_jump_ratio"))
    seam_limit = _number(payload.get("seam_limit"))
    if seam is None or seam_limit is None:
        gates.append(GateResult("surface_continuity", REFUSAL, "seam measurement or limit is absent"))
    else:
        gates.append(GateResult("surface_continuity", "FAIL" if seam > seam_limit else "PASS",
                                f"maximum seam jump {seam:g} exceeds limit {seam_limit:g}"
                                if seam > seam_limit else f"maximum seam jump {seam:g} is within limit"))

    overlap = _number(payload.get("overlap_consistency"))
    overlap_limit = _number(payload.get("overlap_limit"))
    if overlap is None or overlap_limit is None:
        gates.append(GateResult("overlap_consistency", REFUSAL, "overlap measurement or limit is absent"))
    else:
        gates.append(GateResult("overlap_consistency", "FAIL" if overlap < overlap_limit else "PASS",
                                f"overlap score {overlap:g} is below limit {overlap_limit:g}"
                                if overlap < overlap_limit else f"overlap score {overlap:g} meets limit"))

    statuses = {g.status for g in gates}
    status = "FAIL" if "FAIL" in statuses else REFUSAL if REFUSAL in statuses else "PASS"
    return EvidenceReport(
        schema=SCHEMA,
        generated_utc=datetime.now(timezone.utc).isoformat(),
        status=status,
        offline=True,
        network_used=False,
        cuda_used=False,
        command=command,
        argv=_safe_argv(argv or sys.argv),
        inputs=inputs or {},
        gates=gates,
    )


def check_saved_mesh(mesh: Path, volume_url: str, *, command: str = "evidence-gate check",
                     argv: list[str] | None = None) -> EvidenceReport:
    """Check a local saved payload; remote URLs are recorded and refused, never fetched."""
    inputs = {"mesh": str(mesh), "volume_url": volume_url, "remote_access": False}
    if volume_url.startswith(("http://", "https://", "s3://")):
        return EvidenceReport(SCHEMA, datetime.now(timezone.utc).isoformat(), REFUSAL, True, False, False,
                              command, _safe_argv(argv or sys.argv), inputs,
                              [GateResult("local_evidence", REFUSAL,
                                          "remote volume access is disabled; provide saved local evidence")])
    if not mesh.is_file():
        return EvidenceReport(SCHEMA, datetime.now(timezone.utc).isoformat(), REFUSAL, True, False, False,
                              command, _safe_argv(argv or sys.argv), inputs,
                              [GateResult("local_evidence", REFUSAL, "mesh evidence file is absent")])
    try:
        payload = json.loads(mesh.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return evaluate_payload(payload, command=command, argv=argv, inputs=inputs)


def write_receipt(report: EvidenceReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
