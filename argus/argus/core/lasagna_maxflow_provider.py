"""Typed adapter for Villa's ``vc_lasagna_maxflow_graph`` (Lasagna multi-sheet maxflow)."""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
from pathlib import Path

from argus.core import paths
from argus.core import villa_provider_adapter as PVA

CAPABILITY_ID = "vc_lasagna_maxflow_graph"
CONTRACT = "argus-lasagna-maxflow-provider-v1"
SCHEMA_PLAN = "argus-lasagna-maxflow-plan-v1"
SCHEMA_RECEIPT = "argus-lasagna-maxflow-receipt-v1"

REQUIRED_CHANNEL = "pred_dt"

SCIENTIFIC_BOUNDARY = (
    "a zero-exit graph build is an operational artifact -- CPU passability-graph construction "
    "only -- never a qualified multi-sheet separation or fitted geometry. The pinned Windows "
    "binary additionally cannot execute the maxflow/min-cut solve itself: both --run-ecl "
    "(ECL-MaxFlow/CUDA) and --run-laplace-amgx (AMGX) report themselves unavailable at this "
    "build; see graph_report.solve_backends in the receipt. No real pred_dt science field is "
    "bound here either -- see the plan's pred_dt block and argus.core.lasagna_candidate for that "
    "separate, still-REMOTE_ONLY producer."
)


class LasagnaMaxflowRefusal(RuntimeError):
    pass




def _load_manifest(manifest_path: Path) -> dict:
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LasagnaMaxflowRefusal("manifest is unreadable: %s: %s" % (type(exc).__name__, exc)) from None
    if not isinstance(doc, dict):
        raise LasagnaMaxflowRefusal("manifest root must be a JSON object")
    return doc


def _pred_dt_group(manifest: dict) -> tuple:
    groups = manifest.get("groups")
    if not isinstance(groups, dict):
        raise LasagnaMaxflowRefusal(
            "manifest has no 'groups' object; vc_lasagna_maxflow_graph's own manifest parser "
            "(vc::lasagna::LasagnaDatasetManifest::parseText) requires one")
    for name, group in groups.items():
        if isinstance(group, dict) and REQUIRED_CHANNEL in (group.get("channels") or []):
            return name, group
    raise LasagnaMaxflowRefusal(
        "manifest declares no channel group containing %r; upstream MaxflowGraph.cpp's "
        "bindPredDt() throws \"Lasagna dataset missing required channel 'pred_dt'\" at exactly "
        "this point, before any subprocess would even be spent" % REQUIRED_CHANNEL)


def _zarray_metadata(zarr_dir: Path) -> dict:
    zarray = zarr_dir / ".zarray"
    if not zarray.is_file():
        raise LasagnaMaxflowRefusal(
            "pred_dt zarr has no .zarray metadata at %s; the pinned reader "
            "(utils/include/utils/zarr.hpp) opens a zarr v2 array directory directly, not an "
            "OME-zarr multiscale group" % zarray)
    try:
        meta = json.loads(zarray.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LasagnaMaxflowRefusal(".zarray is unreadable: %s: %s" % (type(exc).__name__, exc)) from None
    if not isinstance(meta, dict):
        raise LasagnaMaxflowRefusal(".zarray root must be a JSON object")
    return meta


def validate_manifest(manifest_path) -> dict:
    """Reproduce, in Python, the exact preconditions the native binary checks before it will even start building a graph: a 'groups' object, one group whose 'channels' contains 'pred_dt', and that..."""
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_file():
        raise LasagnaMaxflowRefusal("manifest does not exist: %s" % manifest_path)
    manifest = _load_manifest(manifest_path)
    group_name, group = _pred_dt_group(manifest)
    zarr_key = group.get("zarr")
    if not isinstance(zarr_key, str) or not zarr_key.strip():
        raise LasagnaMaxflowRefusal("group %r has no string 'zarr' path" % group_name)
    zarr_path = Path(zarr_key)
    if not zarr_path.is_absolute():
        zarr_path = (manifest_path.parent / zarr_path).resolve()
    if not zarr_path.is_dir():
        raise LasagnaMaxflowRefusal("pred_dt zarr does not exist: %s" % zarr_path)
    meta = _zarray_metadata(zarr_path)
    dtype = str(meta.get("dtype", ""))
    if dtype not in ("|u1", "u1", "uint8"):
        raise LasagnaMaxflowRefusal(
            "pred_dt zarr dtype is %r, not uint8; upstream bindPredDt() requires "
            "\"Lasagna channel 'pred_dt' must be uint8\"" % dtype)
    shape = meta.get("shape")
    chunks = meta.get("chunks")
    if (not isinstance(shape, list) or not isinstance(chunks, list)
            or len(shape) != len(chunks) or not shape):
        raise LasagnaMaxflowRefusal("pred_dt zarr has malformed shape/chunks metadata")
    if len(shape) not in (3, 4):
        raise LasagnaMaxflowRefusal(
            "pred_dt zarr is %dD; upstream requires 3D (Z,Y,X) or channel-first 4D, "
            "\"Lasagna pred_dt zarr must be 3D or channel-first 4D\"" % len(shape))
    try:
        shape = [int(v) for v in shape]
        chunks = [int(v) for v in chunks]
    except (TypeError, ValueError):
        raise LasagnaMaxflowRefusal("pred_dt zarr shape/chunks must be integers") from None
    if any(c <= 0 for c in chunks):
        raise LasagnaMaxflowRefusal(
            "pred_dt zarr has a zero-sized chunk dimension, \"Lasagna pred_dt zarr has "
            "zero-sized chunks\"")
    channel_index = group["channels"].index(REQUIRED_CHANNEL)
    if len(shape) == 4 and channel_index >= shape[0]:
        raise LasagnaMaxflowRefusal(
            "pred_dt channel index %d is outside the zarr's leading (channel) dimension %d"
            % (channel_index, shape[0]))
    return {
        "group": group_name, "zarr_path": str(zarr_path), "dtype": dtype,
        "shape": shape, "chunks": chunks, "channel_dimension": len(shape) == 4,
        "channel_index": channel_index,
        "fingerprint": PVA._tree_fingerprint(zarr_path),
    }




def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _point(value, label: str) -> str:
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise LasagnaMaxflowRefusal(
            "%s must be an 'x,y,z' string or a three-element sequence" % label)
    if len(parts) != 3:
        raise LasagnaMaxflowRefusal("%s must have exactly three components" % label)
    try:
        numbers = [float(p) for p in parts]
    except (TypeError, ValueError):
        raise LasagnaMaxflowRefusal("%s components must be numbers" % label) from None
    if not all(math.isfinite(n) for n in numbers):
        raise LasagnaMaxflowRefusal("%s components must be finite" % label)
    return ",".join(_format_number(n) for n in numbers)


def _points(values, label: str) -> list:
    if not isinstance(values, (list, tuple)) or not values:
        raise LasagnaMaxflowRefusal("at least one %s point is required" % label)
    return [_point(v, "%s[%d]" % (label, i)) for i, v in enumerate(values)]




def plan_lasagna(*, manifest_path, sources, sinks, source_binding: dict,
                  margin_base_voxels: int = 1000, threshold: int = 110,
                  working_to_base_scale: float = 1.0, run_ecl: bool = False, runs: int = 1,
                  terminal_flood_depth: int = 10, terminal_flood_capacity: int = 1024,
                  terminal_flood_decay: int = 2, terminal_region_iterations: int = 0) -> dict:
    """Pure command plan for ``vc_lasagna_maxflow_graph``."""
    row = PVA._entry(CAPABILITY_ID)
    manifest_path = Path(manifest_path).resolve()
    pred_dt = validate_manifest(manifest_path)
    if not isinstance(source_binding, dict):
        raise LasagnaMaxflowRefusal("source_binding is required and must be an object")
    missing = [key for key in ("physical_scroll", "volume_id", "acquisition_id")
               if source_binding.get(key) in (None, "")]
    if missing:
        raise LasagnaMaxflowRefusal("source_binding is missing %s" % missing)
    src_points = _points(sources, "src")
    sink_points = _points(sinks, "sink")

    margin_base_voxels = int(margin_base_voxels)
    threshold = int(threshold)
    working_to_base_scale = float(working_to_base_scale)
    runs = int(runs)
    terminal_flood_depth = int(terminal_flood_depth)
    terminal_flood_capacity = int(terminal_flood_capacity)
    terminal_flood_decay = int(terminal_flood_decay)
    terminal_region_iterations = int(terminal_region_iterations)
    if margin_base_voxels < 0:
        raise LasagnaMaxflowRefusal("margin_base_voxels must be non-negative")
    if not 0 <= threshold <= 255:
        raise LasagnaMaxflowRefusal("threshold must be in [0,255]")
    if not working_to_base_scale > 0 or not math.isfinite(working_to_base_scale):
        raise LasagnaMaxflowRefusal("working_to_base_scale must be a positive finite number")
    if runs <= 0:
        raise LasagnaMaxflowRefusal("runs must be positive")
    if terminal_flood_depth < 0:
        raise LasagnaMaxflowRefusal("terminal_flood_depth must be non-negative")
    if terminal_flood_capacity <= 0:
        raise LasagnaMaxflowRefusal("terminal_flood_capacity must be positive")
    if terminal_flood_decay <= 1:
        raise LasagnaMaxflowRefusal("terminal_flood_decay must be greater than 1")
    if terminal_region_iterations < 0:
        raise LasagnaMaxflowRefusal("terminal_region_iterations must be non-negative")

    argv = [row["executable"], str(manifest_path)]
    for point in src_points:
        argv += ["--src", point]
    for point in sink_points:
        argv += ["--sink", point]
    argv += ["--margin-base-voxels", str(margin_base_voxels), "--threshold", str(threshold),
             "--working-to-base-scale", _format_number(working_to_base_scale)]
    if run_ecl:
        argv += ["--run-ecl", "--runs", str(runs)]
        if terminal_region_iterations > 0:
            argv += ["--terminal-region-iterations", str(terminal_region_iterations)]
        else:
            argv += ["--terminal-flood-depth", str(terminal_flood_depth),
                     "--terminal-flood-capacity", str(terminal_flood_capacity),
                     "--terminal-flood-decay", str(terminal_flood_decay)]

    body = {
        "schema": SCHEMA_PLAN, "contract": CONTRACT, "capability_id": CAPABILITY_ID,
        "upstream_revision": row.get("upstream_commit"), "executable": row["executable"],
        "manifest_path": str(manifest_path),
        "manifest_fingerprint": PVA._tree_fingerprint(manifest_path),
        "pred_dt": pred_dt,
        "sources": src_points, "sinks": sink_points,
        "options": {
            "margin_base_voxels": margin_base_voxels, "threshold": threshold,
            "working_to_base_scale": working_to_base_scale, "run_ecl": bool(run_ecl),
            "runs": runs, "terminal_flood_depth": terminal_flood_depth,
            "terminal_flood_capacity": terminal_flood_capacity,
            "terminal_flood_decay": terminal_flood_decay,
            "terminal_region_iterations": terminal_region_iterations,
        },
        "source_binding": dict(source_binding), "argv": argv,
        "controls": ["immutable upstream ledger entry", "typed argv",
                     "manifest pred_dt precondition reproduced from upstream C++",
                     "source binding", "nonzero exit refusal",
                     "no admission without a stable graph-build stats block in stdout"],
        "scientific_boundary": SCIENTIFIC_BOUNDARY,
    }
    body["plan_sha256"] = PVA._sha(body)
    return body


def run_lasagna(the_plan: dict, *, runner=None, receipt_path=None, timeout_s: int = 1800) -> dict:
    """Run one approved plan and write an append-only differential receipt."""
    if not isinstance(the_plan, dict) or the_plan.get("schema") != SCHEMA_PLAN:
        raise LasagnaMaxflowRefusal("an ARGUS lasagna-maxflow plan is required")
    expected = PVA._sha({k: v for k, v in the_plan.items() if k != "plan_sha256"})
    if expected != the_plan.get("plan_sha256"):
        raise LasagnaMaxflowRefusal("plan hash does not re-derive")
    manifest_path = Path(the_plan["manifest_path"])
    if not manifest_path.is_file():
        raise LasagnaMaxflowRefusal("manifest no longer exists: %s" % manifest_path)
    if PVA._tree_fingerprint(manifest_path) != the_plan["manifest_fingerprint"]:
        raise LasagnaMaxflowRefusal("manifest changed after planning; re-plan before invocation")
    pred_dt_now = validate_manifest(manifest_path)
    if pred_dt_now["fingerprint"] != the_plan["pred_dt"]["fingerprint"]:
        raise LasagnaMaxflowRefusal("pred_dt zarr changed after planning; re-plan before invocation")

    if receipt_path is None:
        receipt_path = paths.science_data(
            "provider_runs", "lasagna_maxflow", the_plan["plan_sha256"] + ".json")
    rp = paths.assert_writable(receipt_path)
    if rp.exists():
        raise LasagnaMaxflowRefusal("receipt already exists; receipts are append-only")

    def write_receipt(receipt: dict) -> str:
        rp.parent.mkdir(parents=True, exist_ok=True)
        if rp.exists():
            raise LasagnaMaxflowRefusal("receipt already exists; receipts are append-only")
        rp.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n",
                      encoding="utf-8")
        return str(rp)

    fn = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True,
                                                 timeout=timeout_s, shell=False))
    started = time.time()
    base_receipt = {
        "schema": SCHEMA_RECEIPT, "contract": CONTRACT, "plan_sha256": the_plan["plan_sha256"],
        "capability_id": CAPABILITY_ID, "upstream_revision": the_plan.get("upstream_revision"),
        "argv": list(the_plan["argv"]), "source_binding": the_plan["source_binding"],
        "manifest_fingerprint": the_plan["manifest_fingerprint"],
        "pred_dt_fingerprint": the_plan["pred_dt"]["fingerprint"],
        "scientific_boundary": the_plan["scientific_boundary"],
    }
    try:
        result = fn(list(the_plan["argv"]))
    except Exception as exc:
        receipt = dict(base_receipt,
                       started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                       duration_s=round(time.time() - started, 3), status="REFUSED",
                       error="%s: %s" % (type(exc).__name__, exc))
        write_receipt(receipt)
        raise LasagnaMaxflowRefusal(
            "provider process failed: %s: %s; receipt: %s" % (type(exc).__name__, exc, rp)) from None

    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    receipt = dict(base_receipt,
                   started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                   duration_s=round(time.time() - started, 3), returncode=returncode,
                   stdout_tail=stdout[-4000:], stderr_tail=stderr[-2000:])
    if returncode != 0:
        receipt["status"] = "REFUSED"
        write_receipt(receipt)
        raise LasagnaMaxflowRefusal(
            "vc_lasagna_maxflow_graph returned %s; output was not admitted; receipt: %s"
            % (returncode, rp))

    parsed = parse_graph_report(stdout)
    if parsed is None:
        receipt["status"] = "REFUSED"
        receipt["error"] = ("zero exit but stdout has no stable graph-build stats block; "
                             "nothing was admitted")
        write_receipt(receipt)
        raise LasagnaMaxflowRefusal(
            "vc_lasagna_maxflow_graph output was not admitted: no stable graph-build stats "
            "block in stdout; receipt: %s" % rp)
    receipt["graph_report"] = parsed
    receipt["status"] = "OK"
    write_receipt(receipt)
    return {"status": "OK", "receipt": receipt, "receipt_path": str(rp)}



_SCALAR_PATTERNS = (
    ("used_pred_dt_voxels", r"^used pred_dt voxels: (\d+)$"),
    ("passable_voxels", r"^passable voxels: (\d+)$"),
    ("voxel_nodes", r"^voxel nodes: (\d+)$"),
    ("graph_nodes", r"^graph nodes: (\d+)$"),
    ("directed_edges", r"^directed edges: (\d+)$"),
    ("undirected_edges", r"^undirected edges: (\d+)$"),
    ("average_edges_per_node", r"^average edges/node: ([\d.eE+-]+)$"),
    ("total_face_contact_capacity", r"^total face-contact capacity: (\d+)$"),
    ("contraction_ratio", r"^contraction ratio: ([\d.eE+-]+)$"),
    ("graph_build_time_seconds", r"^graph build time seconds: ([\d.eE+-]+)$"),
    ("graph_pipeline_total_time_seconds", r"^graph pipeline total time seconds: ([\d.eE+-]+)$"),
)
_TERMINAL_PATTERN = re.compile(
    r"^(source|sink)\[(\d+)\] pred_dt voxel xyz: (-?[\d.eE+-]+),(-?[\d.eE+-]+),(-?[\d.eE+-]+) "
    r"node=(-?\d+) exact=(yes|nearest)$")


def _scalar(value: str):
    return int(value) if re.fullmatch(r"-?\d+", value) else float(value)


def parse_graph_report(stdout: str):
    """Structured graph-build stats from ``vc_lasagna_maxflow_graph``'s stdout, or ``None``."""
    if "Lasagna maxflow graph build" not in stdout:
        return None
    lines = [line.strip() for line in stdout.replace("\r", "\n").splitlines()]
    report: dict = {}
    for key, pattern in _SCALAR_PATTERNS:
        rx = re.compile(pattern)
        found = next((m.group(1) for line in lines if (m := rx.match(line))), None)
        if found is None:
            return None
        report[key] = _scalar(found)

    sources, sinks = [], []
    for line in lines:
        m = _TERMINAL_PATTERN.match(line)
        if not m:
            continue
        kind, index, x, y, z, node, exact = m.groups()
        entry = {"index": int(index), "voxel_xyz": [_scalar(x), _scalar(y), _scalar(z)],
                 "node": int(node), "exact": exact == "yes"}
        (sources if kind == "source" else sinks).append(entry)
    report["sources"] = sources
    report["sinks"] = sinks

    ecl_available = None
    m = re.search(r"^ECL-MaxFlow results:\n\s*available: (yes|no)", stdout, re.MULTILINE)
    if m:
        ecl_available = m.group(1) == "yes"
    laplace_available = None
    m = re.search(r"^AMGX screened-Laplace results:\n\s*available: (yes|no)", stdout, re.MULTILINE)
    if m:
        laplace_available = m.group(1) == "yes"
    report["solve_backends"] = {
        "ecl_maxflow_available": ecl_available,
        "laplace_amgx_available": laplace_available,
    }
    return report
