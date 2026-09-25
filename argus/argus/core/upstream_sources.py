"""Auditable upstream source records used by the convergence layer."""
from __future__ import annotations

import hashlib
import json


VERIFIED_UTC = "2026-09-19"
_HOW = "GitHub REST API read-only (repos/<owner>/<repo>[/pulls/<n>]); the pin is the SHA the API returned at verification time"

UPSTREAM_SOURCES = (
    {"id": "vc-segqa", "url": "https://github.com/Wadoekeani/vc-segqa",
     "role": "mask escape and adjacent-winding geometry QA",
     "pin": "86aa4b65f29b660351fe0131a76ed403afe6ddca", "pin_kind": "default-branch HEAD (main) when verified; not a release tag",
     "pin_status": "PINNED_LIVE_" + VERIFIED_UTC, "verified_utc": VERIFIED_UTC, "verification": _HOW, "size_kb": 7029,
     "license_status": "NO_LICENSE_DECLARED",
     "license_evidence": "API license=null and no LICENSE file in the root tree listing; all rights reserved by default",
     "use_gate": "not redistributable and not vendored; local execution only after an explicit operator acknowledgement (ARGUS_ALLOW_UNLICENSED_UPSTREAM=vc-segqa)",
     "execution_note": "a set of scripts (scan.py, l0.py, wrapgap.py ...), not a package; its scan reads the open-data bucket over HTTP, which ARGUS does not do implicitly"},
    {"id": "labelscope", "url": "https://github.com/rodriguescarson/labelscope",
     "role": "leakage, label topology, ridge/profile, sheet-switch and on-sheet diagnostics",
     "pin": "00bdae40036e0dda2c5a61e76a1d777e6e911f67", "pin_kind": "default-branch HEAD (main) when verified; not a release tag",
     "pin_status": "PINNED_LIVE_" + VERIFIED_UTC, "verified_utc": VERIFIED_UTC, "verification": _HOW, "size_kb": 127948,
     "license_status": "MIT_VERIFIED_" + VERIFIED_UTC, "license_evidence": "API license=MIT; LICENSE present in the tree",
     "interface": "installable CLI (`labelscope onsheet|sheetswitch|align|scan|leakage`), CPU only"},
    {"id": "villa-pr-1812", "url": "https://github.com/ScrollPrize/villa/pull/1812",
     "role": "Apple Silicon MPS inference support", "pin": "b8f4d3a116f8ef9b8aacac5d820bd3c133f528d2", "pin_kind": "PR head",
     "pin_status": "OPEN_VERIFIED_" + VERIFIED_UTC, "verified_utc": VERIFIED_UTC, "verification": _HOW,
     "license_status": "INHERITS_VILLA_LICENSE",
     "lane_status": "STAGED", "execution_validated": False,
     "lane_note": "The MPS lane stays STAGED: no provider has been executed on Apple Silicon by ARGUS, so selection logic is contract only.",
     "evidence": "live PR reports CUDA->MPS->CPU selection, device-aware autocast and 3-D limitations"},
    {"id": "villa-apple-silicon", "url": "https://github.com/AndreasHad04/villa-apple-silicon",
     "role": "MPS measurements and null battery", "pin": "9fd758835fce48504151dcf76d8b1e7acfe8cfbb", "pin_kind": "default-branch HEAD (main) when verified",
     "pin_status": "PINNED_LIVE_" + VERIFIED_UTC, "verified_utc": VERIFIED_UTC, "verification": _HOW, "size_kb": 1971,
     "license_status": "MIT_VERIFIED_" + VERIFIED_UTC, "license_evidence": "API license=MIT; LICENSE present in the root",
     "lane_status": "STAGED", "execution_validated": False},
    {"id": "villa-pr-1819", "url": "https://github.com/ScrollPrize/villa/pull/1819",
     "role": "distilled 3-D ink provider staging", "pin": "5ff00ca01bdaa5dec7e4d7fb4dd1171d096fdb59", "pin_kind": "merge commit into base `distilled_3d_ink`",
     "pin_status": "MERGED_INTO_NON_MAIN_BASE_" + VERIFIED_UTC, "verified_utc": VERIFIED_UTC, "verification": _HOW,
     "license_status": "INHERITS_VILLA_LICENSE", "activation_rule": "a non-main merge is staged, never activated"},
    {"id": "villa-pr-1829", "url": "https://github.com/ScrollPrize/villa/pull/1829",
     "role": "fiber adjacent-link schema", "pin": "f07d33be6a00d12ace7d6a9465efe17c78ed7b47", "pin_kind": "merge commit into main",
     "pin_status": "MERGED_MAIN_VERIFIED_" + VERIFIED_UTC, "verified_utc": VERIFIED_UTC, "verification": _HOW,
     "license_status": "INHERITS_VILLA_LICENSE"},
    {"id": "villa-pr-1813", "url": "https://github.com/ScrollPrize/villa/pull/1813",
     "role": "center the 62-layer ink inference window",
     "pin": "ebc12b1e5c6dddf5550f5ce1ce19da7ee9edba53", "pin_kind": "PR head", "pin_status": "OPEN_VERIFIED_" + VERIFIED_UTC,
     "verified_utc": VERIFIED_UTC, "verification": _HOW, "license_status": "INHERITS_VILLA_LICENSE",
     "evidence": "upstream discussion identifies centered depth as necessary for reproduction"},
    {"id": "villa-pr-1818", "url": "https://github.com/ScrollPrize/villa/pull/1818",
     "role": "bicubic interpolation for vc_render_tifxyz (a renderer-convention change)",
     "pin": "6fe8ac14a7d9088be58bd7effa5a2ddb752c2c0f", "pin_kind": "PR head", "pin_status": "OPEN_VERIFIED_" + VERIFIED_UTC,
     "verified_utc": VERIFIED_UTC, "verification": _HOW, "license_status": "INHERITS_VILLA_LICENSE"},
    {"id": "villa-pr-1821", "url": "https://github.com/ScrollPrize/villa/pull/1821",
     "role": "Tutorial 5 on a small-memory GPU: flat-path chunk selection",
     "pin": "430a050117893cba7de351f8afa9f96a1dd21d8d", "pin_kind": "PR head", "pin_status": "OPEN_VERIFIED_" + VERIFIED_UTC,
     "verified_utc": VERIFIED_UTC, "verification": _HOW, "license_status": "INHERITS_VILLA_LICENSE"},
)


def manifest() -> dict:
    rows = [dict(row) for row in UPSTREAM_SOURCES]
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return {"schema": "argus-upstream-source-manifest-v1", "sources": rows,
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            "activation": "staged-only; no source auto-activation",
            "drift_rule": "recompute source and contract hashes before promotion"}


def drift_report(previous: dict, current: dict | None = None) -> dict:
    now = current or manifest()
    previous_hash = previous.get("manifest_sha256")
    return {"changed": previous_hash != now["manifest_sha256"],
            "previous_sha256": previous_hash,
            "current_sha256": now["manifest_sha256"],
            "action": "REVIEW_REQUIRED" if previous_hash != now["manifest_sha256"] else "NO_DRIFT"}
