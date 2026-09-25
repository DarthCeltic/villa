#!/usr/bin/env python3
"""Run the public ARGUS test kit: the deterministic, offline tests that need no private data.

The kit is grouped by the question each group answers. Every group runs in its own pytest process,
so one group's state cannot make another pass, and the summary says which group failed.

    python scripts/run_portable_ci.py                     run every group
    python scripts/run_portable_ci.py --list              print the groups and the files in each
    python scripts/run_portable_ci.py --group identity    run one or more groups (repeatable)
    python scripts/run_portable_ci.py --json out.json     also write a machine-readable result
    python scripts/run_portable_ci.py -- -x -k pitch      everything after -- goes to pytest

A file named here that does not exist is an error, not a skip: a kit that quietly runs fewer tests
than it claims is worse than one that fails.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: group -> (what it answers, files). Every file is hermetic: synthetic inputs, no network, no GPU.
GROUPS = {
    "identity": (
        "Are a scroll, a volume, a block and a pixel pitch the thing they say they are?",
        ("argus/tests/test_scroll_ids.py", "argus/tests/test_public_identity.py",
         "argus/tests/test_block_identity.py", "argus/tests/test_physical_frame_contract.py",
         "argus/tests/test_pitch.py"),
    ),
    "input-representation": (
        "Is the input the detector sees the input it was declared to see?",
        ("argus/tests/test_input_boundaries.py", "argus/tests/test_depth_composite.py",
         "argus/tests/test_compositor.py", "argus/tests/test_physical_resample.py",
         "argus/tests/test_signed_normal.py"),
    ),
    "null-and-sabotage": (
        "Does the score fall to chance when one thing is taken away (zero input, shuffled labels, phase, depth)?",
        ("tests/test_public_null_controls.py", "argus/tests/test_pairwise.py",
         "argus/tests/test_pseudolabel_controls.py", "argus/tests/test_spiral_fit_controls.py",
         "argus/tests/test_copy_out_in_defect_control.py"),
    ),
    "labels": (
        "Where do labels come from, how are they scored, and what may a score be called?",
        ("argus/tests/test_label_sources.py", "argus/tests/test_scoring.py", "argus/tests/test_metrics.py",
         "argus/tests/test_result_class.py"),
    ),
    "geometry": (
        "Do boundary, topology and surface-geometry confounds get measured instead of assumed away?",
        ("argus/tests/test_surface_metric.py", "argus/tests/test_topology_metric.py",
         "argus/tests/test_surface_consistency.py", "argus/tests/test_surface_contract.py",
         "argus/tests/test_blender_roundtrip.py"),
    ),
    "lineage-and-independence": (
        "Is the evidence independent of what the model was trained on, and is that declared?",
        ("argus/tests/test_lineage.py", "argus/tests/test_stage_lineage.py", "argus/tests/test_arm_isolation.py",
         "argus/tests/test_science_arbiter.py", "argus/tests/test_exposure.py",
         "argus/tests/test_scroll_generalization.py"),
    ),
    "renderer-parity": (
        "Do two independent implementations of sampling, tiling and resampling agree on a synthetic volume?",
        ("tests/test_public_renderer_parity.py",),
    ),
    "orientation": (
        "Is the sheet-normal sign decided, gated and reported, and are both orientations shown until it is?",
        ("argus/tests/test_orientation_semantics.py", "argus/tests/test_normal_orientation.py"),
    ),
    "memory-and-limits": (
        "Does a run know its memory, VRAM, disk and process limits before it starts?",
        ("argus/tests/test_long_run_preflight.py", "argus/tests/test_availability_bounds.py",
         "argus/tests/test_owned_process_tree.py", "argus/tests/test_resource_sampler.py",
         "argus/tests/test_job_preflight.py", "argus/tests/test_install_tiers.py"),
    ),
    "deterministic-replay": (
        "Does the same seed and the same plan replay to the same bytes, and does an interrupted run resume?",
        ("argus/tests/test_tiled_run_resume.py", "tests/test_public_release.py"),
    ),
    "evidence-integrity": (
        "Can a packet be exported, reopened and proven unchanged, with its limitations still attached?",
        ("argus/tests/test_publication_packet.py", "tests/test_public_evidence_gate.py",
         "tests/test_evidence_products_schema.py", "argus/tests/test_manifest.py"),
    ),
    "service-and-install": (
        "Do the services refuse what they must, and do the Docker files and licences say what they claim?",
        ("argus/tests/test_bff_transport.py", "argus/tests/test_command_boundary.py",
         "argus/tests/test_request_guard.py", "argus/tests/test_service_receipts.py",
         "argus/tests/test_service_jobs_api.py", "argus/tests/test_rescan_boundaries.py",
         "argus/tests/test_ledger_v2.py", "argus/tests/test_licence_registry.py",
         "argus/tests/test_licence_resolver.py", "tests/test_docker_runtime_contract.py",
         "tests/test_windows_consumer_launcher.py", "argus/tests/test_ui_governed_hash.py",
         "argus/tests/test_ui_scroll_status.py", "argus/tests/test_wb_responsive_contract.py",
         "argus/tests/test_install_tiers_ui.py"),
    ),
}


def _env() -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    parts = [str(ROOT / "src"), str(ROOT)]
    have = env.get("PYTHONPATH", "").split(os.pathsep)
    env["PYTHONPATH"] = os.pathsep.join([p for p in parts if p not in have] + [p for p in have if p])
    return env


def _missing() -> list:
    return [f for _n, (_q, files) in GROUPS.items() for f in files if not (ROOT / f).is_file()]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print the groups and their files")
    ap.add_argument("--group", action="append", choices=sorted(GROUPS), help="run only this group (repeatable)")
    ap.add_argument("--json", help="write a machine-readable result to this path")
    ns = ap.parse_args(argv)

    if ns.list:
        for name, (question, files) in GROUPS.items():
            print("%s\n  %s" % (name, question))
            for f in files:
                print("    " + f)
        return 0

    missing = _missing()
    if missing:
        print("the test kit names files that are not in this checkout:", file=sys.stderr)
        print("\n".join("  " + m for m in missing), file=sys.stderr)
        return 2

    results = []
    for name in (ns.group or list(GROUPS)):
        question, files = GROUPS[name]
        started = time.time()
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--tb=short", "-ra", *files, *extra]
        print("== %s: %s" % (name, question), flush=True)
        rc = subprocess.call(cmd, cwd=str(ROOT), env=_env())
        results.append({"group": name, "files": list(files), "returncode": rc,
                        "seconds": round(time.time() - started, 1)})

    print("\n%-28s %-8s %s" % ("group", "result", "seconds"))
    for r in results:
        print("%-28s %-8s %s" % (r["group"], "PASS" if r["returncode"] == 0 else "FAIL (%d)" % r["returncode"], r["seconds"]))
    failed = [r["group"] for r in results if r["returncode"] != 0]
    print("\nkit: %d group(s), %d failed%s" % (len(results), len(failed), (": " + ", ".join(failed)) if failed else ""))
    if ns.json:
        Path(ns.json).write_text(json.dumps({"schema": "argus-public-test-kit-result-v1", "results": results,
                                             "failed": failed}, indent=1) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
