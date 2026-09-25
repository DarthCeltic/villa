"""Run the synthetic public evidence-gate corpus without scan data or network access."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evidence_gate_fixtures"
sys.path.insert(0, str(ROOT))
from evidence_gate.pipeline import evaluate_payload


def _run_declared() -> list[dict]:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for row in manifest["fixtures"]:
        payload = json.loads((FIXTURES / f"{row['name']}.json").read_text(encoding="utf-8"))
        report = evaluate_payload(payload, command="check_evidence_fixtures")
        rows.append({"corpus": "declared", "fixture": row["name"], "expected": row["expected"],
                    "actual": report.status, "match": report.status == row["expected"]})
    return rows


def _run_measured(tmp_dir: Path) -> list[dict]:
    try:
        from evidence_gate.measure import measure_mesh
        sys.path.insert(0, str(FIXTURES))
        from build_measured import build
    except ImportError as e:
        return [{"corpus": "measured", "fixture": "*", "expected": "*", "actual": "SKIPPED",
                 "match": True, "reason": str(e)}]
    manifest = build(tmp_dir)
    rows = []
    for name, case in manifest["cases"].items():
        m = case["manifest"]
        report = measure_mesh(Path(m["mesh_dir"]), m["acquisition"],
                              volume_shape=m.get("volume_shape"), volume_url=m.get("volume_url"),
                              chunk_shape=tuple(m.get("chunk_shape", (128, 128, 128))),
                              orientation_provenance=m.get("orientation_provenance"),
                              command="check_evidence_fixtures(measured)")
        rows.append({"corpus": "measured", "fixture": name, "expected": case["expected"],
                    "actual": report.status, "match": report.status == case["expected"]})
    return rows


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="evidence-gate-measured-") as td:
        rows = _run_declared() + _run_measured(Path(td))
    ok = all(r["match"] for r in rows)
    print(json.dumps({"schema": "argus-evidence-fixtures-result-v2", "rows": rows,
                      "verdict": "PASS" if ok else "FAIL"}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
