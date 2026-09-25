"""The governed Blender round trip: launch a mesh, verify an edit, refuse coordinate/scale drift."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np

from argus.core import blender_adapter as BA
from argus.core import paths

SCHEMA = "argus-blender-roundtrip-v1"

TEST_EDIT_KINDS = ("sculpt", "drift")

DEFAULT_BOUNDARY_REL_TOLERANCE = 1e-4

BOUNDARY_ABS_FLOOR = 1e-5


class RoundtripRefusal(ValueError):
    """Raised instead of silently accepting a mesh whose coordinate frame drifted."""



_VERTEX_RE = re.compile(r"^v\s+(\S+)\s+(\S+)\s+(\S+)")
_FACE_TOKEN_RE = re.compile(r"^-?\d+")


def _face_vertex_index(token: str, vertex_count: int) -> int:
    """The vertex index a single `f` token refers to, 0-based."""
    head = token.split("/", 1)[0]
    idx = int(head)
    return idx - 1 if idx > 0 else vertex_count + idx


def parse_obj(path: str | Path) -> dict:
    """Parse the subset of OBJ this module needs: vertex positions and face topology."""
    p = Path(path)
    if not p.is_file():
        raise RoundtripRefusal("mesh does not exist: %s" % p)
    if p.suffix.lower() != ".obj":
        raise RoundtripRefusal(
            "blender_roundtrip only parses .obj (the interchange format); got %s. "
            "Convert through the existing tifxyz/OBJ converters first." % p.suffix)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    for line in p.read_text(encoding="utf-8", errors="strict").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line[:2] in ("v ", "v\t"):
            m = _VERTEX_RE.match(line)
            if not m:
                raise RoundtripRefusal("malformed vertex line in %s: %r" % (p, line))
            vertices.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
        elif line[:2] in ("f ", "f\t"):
            tokens = line.split()[1:]
            if len(tokens) < 3:
                raise RoundtripRefusal("face with fewer than 3 vertices in %s: %r" % (p, line))
            face = tuple(_face_vertex_index(t, len(vertices)) for t in tokens)
            faces.append(face)
    if not vertices:
        raise RoundtripRefusal("no vertices found in %s" % p)
    verts = np.asarray(vertices, dtype=np.float64)
    for face in faces:
        for idx in face:
            if not (0 <= idx < len(verts)):
                raise RoundtripRefusal("face references vertex %d outside 0..%d in %s"
                                       % (idx, len(verts) - 1, p))
    boundary = _boundary_vertices(faces)
    return {"path": str(p), "vertices": verts, "faces": faces, "boundary": boundary}


def _boundary_vertices(faces: list[tuple[int, ...]]) -> set[int]:
    """Any vertex touching an edge shared by exactly one face (a triangle or an n-gon)."""
    edge_faces: dict[tuple[int, int], int] = {}
    for face in faces:
        n = len(face)
        for i in range(n):
            a, b = face[i], face[(i + 1) % n]
            key = (a, b) if a < b else (b, a)
            edge_faces[key] = edge_faces.get(key, 0) + 1
    boundary: set[int] = set()
    for (a, b), count in edge_faces.items():
        if count == 1:
            boundary.add(a)
            boundary.add(b)
    return boundary


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()



def verify_roundtrip(source_path: str | Path, edited_path: str | Path, *,
                     boundary_rel_tolerance: float = DEFAULT_BOUNDARY_REL_TOLERANCE,
                     boundary_abs_floor: float = BOUNDARY_ABS_FLOOR) -> dict:
    """Compare a mesh against a Blender-edited copy of itself and decide PASS or REFUSE."""
    src = parse_obj(source_path)
    out = parse_obj(edited_path)
    reasons: list[str] = []
    problems: list[str] = []

    n_src_v, n_out_v = len(src["vertices"]), len(out["vertices"])
    faces_src = sorted(tuple(sorted(f)) for f in src["faces"]) if src["faces"] else []
    faces_out = sorted(tuple(sorted(f)) for f in out["faces"]) if out["faces"] else []
    topology_changed = n_src_v != n_out_v or faces_src != faces_out
    if topology_changed:
        problems.append(
            "TOPOLOGY_CHANGED: source has %d vertices / %d faces, edited mesh has %d / %d. "
            "Vertex count or face connectivity changed, so there is no per-vertex correspondence "
            "to measure coordinate drift against; this is refused rather than guessed at "
            "(e.g. from a remesh, dyntopo pass, or a merge/weld operation)."
            % (n_src_v, len(faces_src), n_out_v, len(faces_out)))

    body = {
        "schema": SCHEMA,
        "source": str(source_path),
        "edited": str(edited_path),
        "source_vertex_count": n_src_v,
        "edited_vertex_count": n_out_v,
        "source_face_count": len(src["faces"]),
        "edited_face_count": len(out["faces"]),
        "boundary_vertex_count": len(src["boundary"]),
        "interior_vertex_count": n_src_v - len(src["boundary"]),
        "tolerance": {
            "rule": "max boundary-vertex displacement <= max(boundary_abs_floor, "
                    "boundary_rel_tolerance * source_bbox_diagonal)",
            "boundary_rel_tolerance": boundary_rel_tolerance,
            "boundary_abs_floor": boundary_abs_floor,
        },
    }

    if topology_changed:
        body.update(verdict="REFUSE", problems=problems,
                    max_boundary_displacement=None, mean_boundary_displacement=None,
                    max_interior_displacement=None, scale_drift_ratio=None,
                    tolerance_used=None)
        return body

    v_src, v_out = src["vertices"], out["vertices"]
    displacement = np.linalg.norm(v_out - v_src, axis=1)
    boundary_idx = sorted(src["boundary"])
    interior_idx = sorted(set(range(n_src_v)) - src["boundary"])

    bbox_min, bbox_max = v_src.min(axis=0), v_src.max(axis=0)
    bbox_diag = float(np.linalg.norm(bbox_max - bbox_min))
    tolerance_used = max(boundary_abs_floor, boundary_rel_tolerance * bbox_diag)

    max_boundary = float(displacement[boundary_idx].max()) if boundary_idx else 0.0
    mean_boundary = float(displacement[boundary_idx].mean()) if boundary_idx else 0.0
    max_interior = float(displacement[interior_idx].max()) if interior_idx else 0.0
    mean_interior = float(displacement[interior_idx].mean()) if interior_idx else 0.0

    out_bbox_diag = float(np.linalg.norm(v_out.max(axis=0) - v_out.min(axis=0)))
    scale_drift_ratio = (out_bbox_diag / bbox_diag) if bbox_diag > 0 else None

    if boundary_idx and max_boundary > tolerance_used:
        problems.append(
            "COORDINATE_SCALE_DRIFT: the mesh boundary (its stitch loop to neighbouring "
            "patches) moved by up to %.8g, which exceeds the tolerance %.8g derived from the "
            "source mesh's own bounding-box diagonal (%.8g) at a relative tolerance of %.2g. "
            "A boundary is not supposed to move during interior sculpting; this looks like a "
            "coordinate or scale change introduced by the round trip itself, not an edit, and "
            "is refused rather than silently accepted."
            % (max_boundary, tolerance_used, bbox_diag, boundary_rel_tolerance))

    body.update(
        verdict="REFUSE" if problems else "PASS",
        problems=problems,
        max_boundary_displacement=max_boundary,
        mean_boundary_displacement=mean_boundary,
        max_interior_displacement=max_interior,
        mean_interior_displacement=mean_interior,
        scale_drift_ratio=scale_drift_ratio,
        tolerance_used=tolerance_used,
        source_bbox_diagonal=bbox_diag,
        edited_bbox_diagonal=out_bbox_diag,
    )
    return body


def build_receipt(source_path: str | Path, edited_path: str | Path, verify_result: dict, *,
                  edit_kind: str | None = None, blender_report: dict | None = None) -> dict:
    """The parity receipt: input hash, output hash, coordinate diff, pass/refuse verdict."""
    return {
        "schema": "argus-blender-roundtrip-receipt-v1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": str(source_path),
        "source_sha256": _sha256(source_path),
        "edited": str(edited_path),
        "edited_sha256": _sha256(edited_path),
        "edit_kind": edit_kind,
        "verdict": verify_result["verdict"],
        "problems": verify_result["problems"],
        "coordinate_diff": {
            "source_vertex_count": verify_result["source_vertex_count"],
            "edited_vertex_count": verify_result["edited_vertex_count"],
            "boundary_vertex_count": verify_result["boundary_vertex_count"],
            "interior_vertex_count": verify_result["interior_vertex_count"],
            "max_boundary_displacement": verify_result["max_boundary_displacement"],
            "mean_boundary_displacement": verify_result["mean_boundary_displacement"],
            "max_interior_displacement": verify_result["max_interior_displacement"],
            "mean_interior_displacement": verify_result["mean_interior_displacement"],
            "scale_drift_ratio": verify_result["scale_drift_ratio"],
            "tolerance_used": verify_result["tolerance_used"],
        },
        "tolerance_rule": verify_result["tolerance"],
        "blender_report": blender_report,
        "claim_boundary": "geometry mechanics only; a PASS is not a scientific admissibility claim",
    }


def write_receipt(receipt: dict, path: str | Path) -> Path:
    target = paths.assert_writable(Path(path).resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return target



def _test_script() -> Path:
    script = Path(__file__).resolve().parents[2] / "scripts" / "blender_mesh_inspect.py"
    if not script.is_file():
        raise RoundtripRefusal("ARGUS Blender script is missing: %s" % script)
    return script


def test_edit_command(mesh: str | Path, output_mesh: str | Path, report: str | Path, kind: str, *,
                      executable: str | None = None) -> list[str]:
    """Build the headless command that makes Blender itself perform a deterministic test edit."""
    if kind not in TEST_EDIT_KINDS:
        raise RoundtripRefusal("unknown test edit kind %r; expected one of %s" % (kind, TEST_EDIT_KINDS))
    source = Path(mesh).resolve()
    if source.suffix.lower() != ".obj":
        raise RoundtripRefusal("test_edit_command only supports .obj source meshes")
    output = Path(output_mesh).resolve()
    if output == source:
        raise RoundtripRefusal("Blender test-edit output must not overwrite the source mesh")
    found = BA.resolve_executable(executable)
    if not found:
        raise RoundtripRefusal("Blender is not installed or is not on PATH")
    return [found, "--background", "--factory-startup", "--python", str(_test_script()), "--",
            "test_edit", str(source), str(output), str(Path(report).resolve()), kind]


def plan_launch(mesh_path: str | Path, mode: str, *, output_path: str | Path | None = None,
                report_path: str | Path | None = None, executable: str | None = None) -> dict:
    """Read-only: the exact command a launch would run, and why it might refuse."""
    problems: list[str] = []
    source = Path(mesh_path)
    if not source.is_file():
        problems.append("mesh does not exist: %s" % source)
    elif source.suffix.lower() != ".obj":
        problems.append("only .obj meshes are supported for the governed round trip (got %s)" % source.suffix)
    valid_modes = ("interactive", "headless_roundtrip", "headless_test_sculpt",
                   "headless_test_drift")
    if mode not in valid_modes:
        problems.append("unknown mode %r; expected interactive, headless_roundtrip, "
                        "headless_test_sculpt or headless_test_drift" % mode)
    found = BA.resolve_executable(executable)
    if not found and mode in valid_modes:
        problems.append("Blender is not installed or is not on PATH")
    argv: list[str] | None = None
    if not problems:
        if mode == "interactive":
            argv = BA.interactive_command(source, executable=executable)
        elif mode == "headless_roundtrip":
            if output_path is None or report_path is None:
                problems.append("headless_roundtrip requires output_path and report_path")
            else:
                argv = BA.roundtrip_command(source, output_path, report_path, executable=executable)
        elif mode in ("headless_test_sculpt", "headless_test_drift"):
            if output_path is None or report_path is None:
                problems.append("%s requires output_path and report_path" % mode)
            else:
                kind = "sculpt" if mode == "headless_test_sculpt" else "drift"
                argv = test_edit_command(source, output_path, report_path, kind, executable=executable)
    return {
        "schema": "argus-blender-launch-plan-v1",
        "read_only": True,
        "ready": not problems,
        "mesh_path": str(source),
        "mode": mode,
        "blender_executable": found,
        "argv": argv,
        "problems": problems,
        "note": "this route only builds and returns the command; it never starts a process",
    }


_COMMIT_EXHAUSTED_EXIT_CODES = frozenset({0xC000012D, 0xC0000017})
_COMMIT_RETRY_ATTEMPTS = 3
_COMMIT_RETRY_BACKOFF_S = 5


def launch(mesh_path: str | Path, mode: str, *, output_path: str | Path | None = None,
          report_path: str | Path | None = None, executable: str | None = None,
          timeout_s: int = 600) -> dict:
    """Actually run Blender."""
    plan = plan_launch(mesh_path, mode, output_path=output_path, report_path=report_path,
                       executable=executable)
    if not plan["ready"]:
        raise RoundtripRefusal("; ".join(plan["problems"]))
    argv = plan["argv"]
    if mode == "interactive":
        proc = subprocess.Popen(argv)
        return {"status": "LAUNCHED", "mode": mode, "pid": proc.pid, "argv": argv,
                "note": "interactive Blender window launched; it is not waited on, so this call "
                        "returns before the human finishes editing"}
    result = None
    for attempt in range(1, _COMMIT_RETRY_ATTEMPTS + 1):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
        if (result.returncode & 0xFFFFFFFF) not in _COMMIT_EXHAUSTED_EXIT_CODES:
            break
        if attempt < _COMMIT_RETRY_ATTEMPTS:
            time.sleep(_COMMIT_RETRY_BACKOFF_S * attempt)
    report_doc = None
    if report_path is not None and Path(report_path).is_file():
        try:
            report_doc = json.loads(Path(report_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report_doc = None
    if (result.returncode & 0xFFFFFFFF) in _COMMIT_EXHAUSTED_EXIT_CODES:
        raise RoundtripRefusal(
            "Blender could not start for mode %s: the operating system refused it memory "
            "(exit 0x%08X, commit limit) on all %d attempts. This is a machine-wide memory "
            "shortage, not a fault in the mesh or the Blender script; free memory or wait for "
            "other jobs to finish, then run it again." % (
                mode, result.returncode & 0xFFFFFFFF, _COMMIT_RETRY_ATTEMPTS))
    if result.returncode != 0:
        raise RoundtripRefusal(
            "Blender exited %d for mode %s: %s" % (
                result.returncode, mode, (result.stderr or result.stdout or "")[-2000:]))
    return {"status": "RAN", "mode": mode, "argv": argv, "returncode": result.returncode,
            "output_path": str(output_path) if output_path else None,
            "report_path": str(report_path) if report_path else None,
            "report": report_doc, "stdout_tail": (result.stdout or "")[-2000:],
            "stderr_tail": (result.stderr or "")[-2000:]}
