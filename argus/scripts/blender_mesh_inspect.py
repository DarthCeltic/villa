"""Run inside Blender for ARGUS inspection, conservative round-trip, interactive opening, or a deterministic test edit used to exercise the governed round trip in automated tests."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy

_KIND_MODES = {"test_edit": ("sculpt", "drift")}


def _args() -> tuple[str, list[Path], str | None]:
    if "--" not in sys.argv:
        raise SystemExit("expected Blender's -- argument boundary")
    tail = sys.argv[sys.argv.index("--") + 1:]
    if not tail or tail[0] not in {"inspect", "roundtrip", "interactive", "test_edit"}:
        raise SystemExit("expected a mode: inspect, roundtrip, interactive or test_edit")
    mode = tail[0]
    rest = tail[1:]
    kind = None
    if mode in _KIND_MODES:
        if not rest or rest[-1] not in _KIND_MODES[mode]:
            raise SystemExit("%s expected a trailing kind in %s" % (mode, _KIND_MODES[mode]))
        kind, rest = rest[-1], rest[:-1]
    args = [Path(x).resolve() for x in rest]
    expected = {"inspect": 2, "roundtrip": 3, "interactive": 1, "test_edit": 3}[mode]
    if len(args) != expected:
        raise SystemExit("%s expected %d path arguments" % (mode, expected))
    return mode, args, kind


def _import(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif suffix == ".ply":
        bpy.ops.wm.ply_import(filepath=str(path))
    elif suffix == ".stl":
        bpy.ops.wm.stl_import(filepath=str(path))
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    else:
        raise SystemExit("unsupported mesh format: %s" % suffix)


def _export(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".obj":
        bpy.ops.wm.obj_export(filepath=str(path), export_selected_objects=True)
    elif suffix == ".ply":
        bpy.ops.wm.ply_export(filepath=str(path), export_selected_objects=True)
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.export_scene.gltf(filepath=str(path), use_selection=True,
                                  export_format="GLB" if suffix == ".glb" else "GLTF_SEPARATE")
    else:
        raise SystemExit("unsupported export format: %s" % suffix)


_DRIFT_SCALE = 1.002

_SCULPT_HEIGHT_FRACTION = 0.12


def _apply_test_edit(objects, kind: str) -> dict:
    """Perform one deterministic, real bmesh edit."""
    touched = {"kind": kind, "objects": []}
    for obj in objects:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        boundary_idx = {v.index for v in bm.verts if any(e.is_boundary for e in v.link_edges)}
        if kind == "drift":
            for v in bm.verts:
                v.co = v.co * _DRIFT_SCALE
            touched["objects"].append({"name": obj.name, "vertices_moved": len(bm.verts),
                                       "boundary_vertices_moved": len(boundary_idx),
                                       "scale_factor": _DRIFT_SCALE})
        elif kind == "sculpt":
            interior = [v for v in bm.verts if v.index not in boundary_idx]
            if interior:
                xs = [v.co.x for v in bm.verts]
                ys = [v.co.y for v in bm.verts]
                cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
                diag = math.dist((min(xs), min(ys)), (max(xs), max(ys)))
                sigma = max(diag / 4.0, 1e-6)
                height = max(diag * _SCULPT_HEIGHT_FRACTION, 1e-6)
                for v in interior:
                    r2 = (v.co.x - cx) ** 2 + (v.co.y - cy) ** 2
                    v.co.z += height * math.exp(-r2 / (2.0 * sigma * sigma))
            touched["objects"].append({"name": obj.name, "vertices_moved": len(interior),
                                       "boundary_vertices_untouched": len(boundary_idx)})
        else:
            bm.free()
            raise SystemExit("unknown test edit kind: %s" % kind)
        bm.to_mesh(obj.data)
        obj.data.update()
        bm.free()
    return touched


def _row(obj) -> dict:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    row = {
        "name": obj.name,
        "vertices": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces),
        "non_manifold_edges": sum(not edge.is_manifold for edge in bm.edges),
        "boundary_edges": sum(edge.is_boundary for edge in bm.edges),
        "uv_layers": len(obj.data.uv_layers),
        "dimensions": [float(value) for value in obj.dimensions],
    }
    bm.free()
    return row


def main() -> None:
    mode, args, kind = _args()
    source = args[0]
    output = args[1] if mode in {"roundtrip", "test_edit"} else None
    report = args[-1] if mode in {"inspect", "roundtrip", "test_edit"} else None
    if not source.is_file():
        raise SystemExit("mesh does not exist: %s" % source)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    _import(source)
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    before = [_row(obj) for obj in objects]
    edit_detail = None
    if mode == "roundtrip":
        for obj in objects:
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-9)
            if bm.faces:
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            bm.to_mesh(obj.data)
            bm.free()
    elif mode == "test_edit":
        assert kind is not None
        edit_detail = _apply_test_edit(objects, kind)
    after = [_row(obj) for obj in objects]
    if mode == "interactive":
        print("ARGUS interactive import ready: %s (%d mesh objects)" % (source, len(objects)))
        return
    if output is not None:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        if objects:
            bpy.context.view_layer.objects.active = objects[0]
        _export(output)
    schema = "argus-blender-test-edit-v1" if mode == "test_edit" else "argus-blender-mesh-inspection-v1"
    body = {
        "schema": schema, "mode": mode,
        "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "coordinate_rule": "imported artifact frame; no axis transform applied",
        "before": before, "objects": after, "object_count": len(after),
        "output": str(output) if output else None,
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest() if output else None,
        "repair_scope": ("exact-duplicate weld and face-normal consistency only" if mode == "roundtrip"
                         else "deterministic test edit only, for exercising the governed drift refusal" if mode == "test_edit"
                         else "none"),
        "edit_kind": kind,
        "edit_detail": edit_detail,
        "claim_boundary": "geometry mechanics only; no sheet identity or scientific admissibility claim",
    }
    assert report is not None
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
