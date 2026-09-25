"""Real measurement mode: the actual ARGUS geometry gates run against real files."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pipeline import EvidenceReport, GateResult, REFUSAL, SCHEMA, _safe_argv

_MEASURED_DEFECT_REASONS = ("fall inside the volume", "not one continuous sheet",
                            "sampled voxels are non-fill")


def _classify_refusal(exc) -> str:
    return "FAIL" if any(s in str(exc) for s in _MEASURED_DEFECT_REASONS) else REFUSAL


def _open_reader(volume_url, allow_network, level=0):
    """(reader, network_used, note, error)."""
    is_local = volume_url.startswith("file://")
    if not (is_local or allow_network):
        return None, False, ("chunk-identity gate (#1674) was not measured: %s is remote and "
                             "--allow-network was not given" % volume_url), None
    try:
        from argus.adapters.zarr_range_reader import ZarrRangeReader
        return ZarrRangeReader(volume_url, level=level), (not is_local), None, None
    except Exception as e:
        return None, (not is_local), None, e


def _chunk_identity_gate(reader, refusal=None) -> GateResult:
    """The chunk-identity verdict from what the reader actually touched."""
    report = reader.report()
    touched = report["chunks_present"] + report["chunks_missing_count"]
    if touched == 0:
        return GateResult("chunk_identity", "NOT_MEASURED",
                          "the run stopped before any chunk was read")
    if refusal is not None and "sampled voxels are non-fill" in str(refusal):
        return GateResult("chunk_identity", "FAIL", str(refusal))
    return GateResult(
        "chunk_identity", "PASS",
        "%d chunk(s) touched, %d present, %d never written (fill); %d range request(s) sent"
        % (touched, report["chunks_present"], report["chunks_missing_count"],
           report["range_requests"]))


def parse_probe_box(text: str):
    """`z0:z1:y0:y1:x0:x1` -> six ints, half-open."""
    parts = str(text).split(":")
    if len(parts) != 6:
        raise ValueError("probe box must be z0:z1:y0:y1:x0:x1, got %r" % text)
    v = [int(p) for p in parts]
    if not (v[0] < v[1] and v[2] < v[3] and v[4] < v[5]) or min(v) < 0:
        raise ValueError("probe box must be non-negative and non-empty on every axis: %r" % text)
    return tuple(v)


def probe_volume(volume_url: str, probe_box, *, allow_network: bool = False, level: int = 0,
                 chunk_samples: int = 256, min_nonfill: float | None = None,
                 command: str = "evidence-gate measure --volume-only",
                 argv: list[str] | None = None) -> EvidenceReport:
    """Chunk-identity gate on a CT volume alone: no mesh, no orientation."""
    import numpy as np
    inputs = {"volume_url": volume_url, "probe_box_zyx": list(probe_box), "level": level,
              "network_permitted": bool(allow_network)}
    argv = _safe_argv(argv or sys.argv)
    now = lambda: datetime.now(timezone.utc).isoformat()
    try:
        from argus.adapters.published_mesh import MIN_NONFILL
    except ImportError as e:
        return EvidenceReport(SCHEMA, now(), REFUSAL, True, False, False, command, argv, inputs,
                              [GateResult("dependency", REFUSAL,
                                          "the argus package is required and is not importable: %s" % e)])
    reader, network_used, note, err = _open_reader(volume_url, allow_network, level)
    if err is not None:
        return EvidenceReport(SCHEMA, now(), REFUSAL, True, network_used, False, command, argv,
                              inputs, [GateResult("volume_access", REFUSAL,
                                                  "volume store could not be opened: %s" % err)])
    if reader is None:
        return EvidenceReport(SCHEMA, now(), "NOT_MEASURED", True, False, False, command, argv,
                              inputs, [GateResult("chunk_identity", "NOT_MEASURED", note)])
    z0, z1, y0, y1, x0, x1 = probe_box
    if z1 > reader.shape[0] or y1 > reader.shape[1] or x1 > reader.shape[2]:
        return EvidenceReport(SCHEMA, now(), REFUSAL, True, network_used, False, command, argv,
                              inputs, [GateResult("chunk_identity", REFUSAL,
                                                  "probe box %s reaches outside the level's shape %s"
                                                  % (list(probe_box), list(reader.shape)))])
    cz, cy, cx = reader.chunks
    grid = [(zi, yi, xi) for zi in range(z0 // cz, (z1 - 1) // cz + 1)
            for yi in range(y0 // cy, (y1 - 1) // cy + 1)
            for xi in range(x0 // cx, (x1 - 1) // cx + 1)]
    exhaustive = None
    if volume_url.startswith("file://"):
        import urllib.request
        from argus.core import zarr_volume as ZV
        root = Path(urllib.request.url2pathname(volume_url[len("file://"):]))
        try:
            exhaustive = ZV.missing_chunks_in_region(root, str(level), z0, z1, y0, y1, x0, x1)
        except Exception:
            exhaustive = None
    for zi, yi, xi in grid:
        reader(max(z0, zi * cz), max(y0, yi * cy), max(x0, xi * cx))
    rng = np.random.default_rng(0)
    n = min(int(chunk_samples), (z1 - z0) * (y1 - y0) * (x1 - x0))
    pts = np.stack([rng.integers(z0, z1, n), rng.integers(y0, y1, n), rng.integers(x0, x1, n)], 1)
    vals = np.array([reader(int(a), int(b), int(c)) for a, b, c in pts], float)
    nonfill = float((vals != reader.fill).mean())
    rep = reader.report()
    missing = rep["chunks_missing_count"] if exhaustive is None else len(exhaustive)
    floor = MIN_NONFILL if min_nonfill is None else min_nonfill
    detail = ("%d chunk(s) touched by the box, %d never written (fill); %.1f%% of %d sampled "
              "voxels are non-fill (floor %.1f%%); %d range request(s) sent"
              % (len(grid), missing, 100 * nonfill, n, 100 * floor, rep["range_requests"]))
    if missing == len(grid) or nonfill < floor:
        status, why = "FAIL", "the box does not hold signal: " + detail
    elif missing:
        status, why = REFUSAL, "the box is only partly held: " + detail
    else:
        status, why = "PASS", detail
    return EvidenceReport(SCHEMA, now(), status, True, network_used, False, command, argv, inputs,
                          [GateResult("chunk_identity", status, why)])


def measure_mesh(mesh_dir: Path, acquisition: dict[str, Any], *, volume_shape=None,
                 volume_origin_zyx=(0.0, 0.0, 0.0), coord_scale: float = 1.0,
                 volume_url: str | None = None, allow_network: bool = False,
                 chunk_shape=(128, 128, 128), fill_value: float = 0.0, chunk_samples: int = 256,
                 orientation_provenance: str | None = None, orientation_control: str | None = None,
                 command: str = "evidence-gate measure", argv: list[str] | None = None,
                 ) -> EvidenceReport:
    inputs = {"mesh_dir": str(mesh_dir), "volume_url": volume_url,
             "network_permitted": bool(allow_network)}
    argv = _safe_argv(argv or sys.argv)
    now = lambda: datetime.now(timezone.utc).isoformat()

    try:
        from argus.core.contracts import Refusal
        from argus.adapters.published_mesh import PublishedMeshAdapter
    except ImportError as e:
        return EvidenceReport(SCHEMA, now(), REFUSAL, True, False, False, command, argv, inputs,
                              [GateResult("dependency", REFUSAL,
                                          "the argus package is required for real measurement "
                                          "and is not importable: %s" % e)])

    voxel_reader = None
    network_used = False
    reader_note = "chunk-identity gate (#1674) was not measured: no volume access permitted"
    if volume_url:
        voxel_reader, network_used, note, err = _open_reader(volume_url, allow_network)
        if err is not None:
            return EvidenceReport(SCHEMA, now(), REFUSAL, True, network_used, False, command,
                                  argv, inputs,
                                  [GateResult("volume_access", REFUSAL,
                                              "volume store could not be opened: %s" % err)])
        if voxel_reader is not None:
            reader_note = ("chunk-identity gate measured against %s"
                           % ("a remote store, network explicitly permitted" if network_used
                              else "a local store"))
        else:
            reader_note = note

    if not mesh_dir.is_dir():
        return EvidenceReport(SCHEMA, now(), REFUSAL, True, network_used, False, command, argv,
                              inputs, [GateResult("local_evidence", REFUSAL,
                                                  "mesh directory is absent: %s" % mesh_dir)])

    ctx = {"mesh_dir": mesh_dir, "acquisition": acquisition,
           "volume_shape": volume_shape or [10**9, 10**9, 10**9],
           "volume_origin_zyx": volume_origin_zyx, "coord_scale": coord_scale,
           "chunk_shape": chunk_shape, "fill_value": fill_value, "chunk_samples": chunk_samples,
           "orientation_provenance": orientation_provenance, "orientation_control": orientation_control}
    if voxel_reader is not None:
        ctx["voxel_reader"] = voxel_reader

    adapter = PublishedMeshAdapter()
    insp = adapter.inspect(ctx)
    if not insp["can_handle"]:
        return EvidenceReport(SCHEMA, now(), REFUSAL, True, network_used, False, command, argv,
                              inputs, [GateResult("preflight", REFUSAL, insp["why_not"])])

    with tempfile.TemporaryDirectory(prefix="evidence-gate-") as td:
        try:
            result = adapter.run(ctx, Path(td))
        except Refusal as e:
            status = _classify_refusal(e)
            gates = [GateResult("measured_geometry", status, str(e))]
            if voxel_reader is None:
                gates.append(GateResult("chunk_identity", "NOT_MEASURED", reader_note))
            else:
                gates.append(_chunk_identity_gate(voxel_reader, e))
            return EvidenceReport(SCHEMA, now(), status, True, network_used, False, command, argv,
                                  inputs, gates)

    bundle = result["bundle"]
    gates = [
        GateResult("frame_handshake", "PASS",
                  "%.1f%% of mesh vertices fall inside the declared volume"
                  % (100 * bundle.frame_handshake["vertices_inside_volume"])),
        GateResult("topology", "PASS",
                  "sheet-following: max edge %.2fx median (limit 8x), %.4f%% of edges jump "
                  "(limit 0.10%%)" % (bundle.topology["step_max_ratio"],
                                       100 * bundle.topology["jump_fraction"])),
        GateResult("orientation", "PASS", "%s (%s)" % (bundle.orientation, bundle.orientation_source)),
    ]
    if voxel_reader is not None:
        gates.append(_chunk_identity_gate(voxel_reader))
    else:
        gates.append(GateResult("chunk_identity", "NOT_MEASURED", reader_note))

    return EvidenceReport(SCHEMA, now(), "PASS", True, network_used, False, command, argv,
                          inputs, gates)
