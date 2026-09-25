"""InkSurf as an ATTRIBUTED EXTERNAL PROVIDER for Stage 4 (VIGILES)."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

SCHEMA = "argus-inksurf-provider-receipt-v1"
UPSTREAM_URL = "https://github.com/BioMarco/Inksurf"
UPSTREAM_STATUS_EXPECTED = "NO_GO_SUBMISSION_CLAIM"
STAGE4_LICENCES = ("detector_domain", "physical_window", "calibration_control", "specificity", "blinding")
ARGUS_CLAIM_CAP = "NO_INK_CLAIM"


class ProviderRefusal(Exception):
    pass


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parse_unittest_output(text: str) -> dict:
    m = re.search(r"Ran (\d+) tests? in ([0-9.]+)s", text)
    ok = bool(re.search(r"^OK\s*$", text, re.M))
    return {"tests_run": int(m.group(1)) if m else None, "seconds": float(m.group(2)) if m else None,
            "ok": ok and m is not None}


def build_receipt(*, inksurf_root: Path, commit: str, author_handle: str, report_rel: str,
                  unit_test_output: str, demo_identical_to_upstream: bool | None,
                  utc: str | None = None) -> dict:
    report_path = inksurf_root / report_rel
    if not report_path.is_file():
        raise ProviderRefusal(f"upstream report not found: {report_rel}")
    lic = inksurf_root / "LICENSE"
    if not lic.is_file() or "MIT License" not in lic.read_text(encoding="utf-8")[:200]:
        raise ProviderRefusal("upstream LICENSE is missing or is not the MIT licence this adapter was written for")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for key in ("status", "claim_ceiling", "schema_version"):
        if key not in report:
            raise ProviderRefusal(f"upstream report lacks '{key}'; refusing to summarise it")
    status = report["status"]
    tests = parse_unittest_output(unit_test_output)
    return {
        "schema": SCHEMA,
        "utc": utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": {
            "name": "InkSurf", "upstream_url": UPSTREAM_URL, "commit": commit,
            "author_handle": author_handle, "licence": "MIT (Copyright (c) 2026 InkSurf contributors)",
            "attribution": "InkSurf is the work of its contributors; this receipt summarises one of its "
                           "reports and does not modify, vendor or re-license any of its code.",
            "role": "EXTERNAL_PROVIDER_NOT_ARGUS_DETECTOR",
        },
        "upstream_report": {
            "path_in_upstream": report_rel, "sha256": _sha256(report_path),
            "schema_version": report["schema_version"], "experiment_id": report.get("experiment_id"),
            "regime": report.get("regime"),
            "status_verbatim": status, "claim_ceiling_verbatim": report["claim_ceiling"],
            "confirmation_eligible_receipts": report.get("confirmation_eligible_receipts"),
            "next_requirement_verbatim": report.get("next_requirement"),
            "status_is_the_expected_no_go": status == UPSTREAM_STATUS_EXPECTED,
        },
        "isolated_evaluation": {
            "method": "upstream unit suite via `python -m unittest discover -s tests` in a separate venv; "
                      "synthetic demo run into a scratch directory and compared with upstream's committed "
                      "report; no ARGUS code or data was involved",
            "unit_tests": tests,
            "synthetic_demo_report_identical_to_upstream_commit": demo_identical_to_upstream,
            "what_this_shows": "the upstream software runs and its own tests pass; nothing about ink",
        },
        "argus_position": {
            "is_argus_detector": False,
            "is_independent_proof_of_ink": False,
            "argus_qualification": "NOT_QUALIFIED",
            "argus_claim_cap": ARGUS_CLAIM_CAP,
            "presented_as_ink": False,
            "stage4_licences_supplied_by_this_provider": {k: "NOT_SUPPLIED" for k in STAGE4_LICENCES},
            "can_certify_a_candidate": False,
            "needs_human_review": status != UPSTREAM_STATUS_EXPECTED,
            "note": "an upstream status other than NO_GO_SUBMISSION_CLAIM is recorded as the upstream's "
                    "statement only; it does not raise the ARGUS cap",
        },
    }


def write_receipt(receipt: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "PROVIDER_RECEIPT.json"
    p.write_text(json.dumps(receipt, indent=1) + "\n", encoding="utf-8")
    return p
