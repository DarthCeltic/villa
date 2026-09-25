"""Where a sealed review task lives in the CT, for the Workbench's 3D viewer -- and nothing else about it."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from argus.core import paths

SCHEMA = "argus-workbench-task-binding-v1"
IMPORT_DIR_NAME = "sealed_review_tasks"
QUEUES = {
    "scroll_blocks": {"file": "REVIEW_TASKS_BLOCKS.json", "coordinates": "mesh_block_corners", "carries_mesh": True, "pitch_from": "geometry"},
}

COORDINATE_DISCLOSURE = {
    "mesh_block_corners": {
        "positioning_system": "TIFXYZ_MESH_LEVEL0_XYZ",
        "positioning_statement": "The camera and region box are positioned from this task's tifxyz/mesh level-0 XYZ coordinates (voxels of the exact volume): the mesh block's four corners and centre.",
    },
    "level_window": {
        "positioning_system": "VOLUME_LEVEL_WINDOW_ORIGIN_EDGE",
        "positioning_statement": "The region box is this task's window origin and edge in voxels of the exact volume at the task's own level.",
    },
}
FINE_GRID_STATEMENT = ("The task's fine-grid screening indices are a separate coordinate system. They are not used to position this viewer, "
                       "and they are not part of what this view is sent.")
KIND_FILES = {k: v["file"] for k, v in QUEUES.items()}
HALO_VOXELS = 24
MAX_MESH_TRIANGLES = 80_000

CLAIMS = (
    "This is a view of the CT at a bound location. It is not a detector and it does not read anything.",
    "The task is UNREVIEWED; no reading, OCR, transcription or translation may consume it before two named reviewers answer.",
    "NO_QUALIFIED_DETECTOR: nothing shown here is evidence of ink.",
)


class BindingRefused(RuntimeError):
    def __init__(self, code: str, why: str):
        super().__init__("%s: %s" % (code, why))
        self.code = code
        self.why = why


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=True).encode("utf-8")


def import_dir() -> Path:
    env = os.environ.get("ARGUS_SEALED_TASKS_DIR")
    if env:
        return Path(env)
    return paths.repo("artifacts", IMPORT_DIR_NAME)


_CACHE: dict = {}


def load_sealed(kind: str, *, directory: Path | None = None) -> dict:
    """The sealed task file, after the import receipt, the file hash, every task seal and the root are re-verified."""
    if kind not in KIND_FILES:
        raise BindingRefused("UNKNOWN_QUEUE", "the sealed queues are %s, not %r" % (sorted(KIND_FILES), kind))
    d = Path(directory) if directory else import_dir()
    receipt_p = d / "IMPORT_RECEIPT.json"
    file_p = d / KIND_FILES[kind]
    if not receipt_p.is_file() or not file_p.is_file():
        raise BindingRefused("TASKS_NOT_IMPORTED", "the sealed review tasks are not imported into this checkout (%s)" % d.name)
    receipt = json.loads(receipt_p.read_text(encoding="utf-8"))
    raw = file_p.read_bytes()
    entry = next((f for f in receipt.get("imported_files", []) if f.get("stored_as") == KIND_FILES[kind]), None)
    if entry is None or entry.get("sha256") != _sha(raw):
        raise BindingRefused("SEAL_MISMATCH", "%s does not match the hash recorded when it was imported" % KIND_FILES[kind])
    key = (str(file_p), len(raw), _sha(raw))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("kind") != kind:
        raise BindingRefused("SEAL_MISMATCH", "the file declares kind %r, not %r" % (payload.get("kind"), kind))
    for t in payload["tasks"]:
        body = {k: v for k, v in t.items() if k != "task_sha256"}
        if _sha(_canon(body)) != t["task_sha256"]:
            raise BindingRefused("SEAL_MISMATCH", "the seal of %s does not verify" % t.get("task_id"))
    root = _sha("\n".join(sorted("%s:%s" % (t["task_id"], t["task_sha256"]) for t in payload["tasks"])).encode("utf-8"))
    recorded = (receipt.get("sealed_task_roots") or {}).get(kind)
    if root != payload.get("root_sha256") or (recorded is not None and recorded != root):
        raise BindingRefused("SEAL_MISMATCH", "the sealed task root does not verify for %s" % kind)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = payload
    return payload


def find_task(kind: str, task_id: str, *, directory: Path | None = None) -> tuple[dict, dict]:
    payload = load_sealed(kind, directory=directory)
    for t in payload["tasks"]:
        if t["task_id"] == task_id:
            return t, payload
    raise BindingRefused("NO_SUCH_TASK", "no task %r in the %s queue" % (task_id, kind))


def list_tasks(scroll: str | None = None, *, directory: Path | None = None) -> list:
    """Task ids and their coordinate status only: no window names, scores or geometry beyond 'can it be opened'."""
    rows = []
    for kind in KIND_FILES:
        try:
            payload = load_sealed(kind, directory=directory)
        except BindingRefused as e:
            if e.code == "TASKS_NOT_IMPORTED":
                continue
            raise
        if scroll is not None and payload.get("scroll") != scroll:
            continue
        for t in payload["tasks"]:
            b = _box(kind, t)
            rows.append({"kind": kind, "task_id": t["task_id"], "scroll": payload.get("scroll"), "task_sha256": t["task_sha256"],
                         "coordinate_status": b["status"], "level": b.get("level")})
    return rows


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _volume_id(name: str | None) -> str | None:
    if not name:
        return None
    base = str(name).replace("\\", "/").rstrip("/").split("/")[-1]
    return base[:-5] if base.endswith(".zarr") else base


def _box(kind: str, task: dict) -> dict:
    """The ROI in the units of the level the task names."""
    c = task.get("coordinates") or {}
    shape = QUEUES[kind]["coordinates"] if kind in QUEUES else None
    if shape == "mesh_block_corners":
        pts = list(c.get("block_corners_xyz_L0_voxels") or []) + [c.get("block_centre_xyz_L0_voxels")]
        flat = [v for p in pts if p for v in p]
        if not pts or not flat or not all(_finite(v) for v in flat):
            return {"status": "UNRESOLVED_NONFINITE", "level": "0",
                    "why": "the block's mesh coordinates are not finite (the mesh has a hole here), so no exact location can be bound"}
        lo_xyz = [min(p[i] for p in pts if p) for i in range(3)]
        hi_xyz = [max(p[i] for p in pts if p) for i in range(3)]
        lo = [math.floor(lo_xyz[2]), math.floor(lo_xyz[1]), math.floor(lo_xyz[0])]
        hi = [math.ceil(hi_xyz[2]) + 1, math.ceil(hi_xyz[1]) + 1, math.ceil(hi_xyz[0]) + 1]
        ctr = c["block_centre_xyz_L0_voxels"]
        return {"status": "RESOLVED", "level": "0", "min_zyx": lo, "max_zyx": hi, "centre_zyx": [ctr[2], ctr[1], ctr[0]],
                "halo_voxels": HALO_VOXELS, "units": "level-0 voxels of the exact volume (tifxyz mesh coordinates)"}
    if shape == "level_window":
        o, e = c.get("L2_origin_zyx"), c.get("edge_L2")
        if not o or not _finite(e) or not all(_finite(v) for v in o):
            return {"status": "UNRESOLVED_NONFINITE", "level": "2", "why": "the window's origin is not declared"}
        hi = [int(o[i]) + int(e) for i in range(3)]
        return {"status": "RESOLVED", "level": str(task["source"].get("level", 2)), "min_zyx": [int(v) for v in o], "max_zyx": hi,
                "centre_zyx": [(int(o[i]) + hi[i]) / 2 for i in range(3)], "halo_voxels": 0, "units": "level-2 voxels (9.6 um) of the exact volume"}
    raise BindingRefused("UNKNOWN_QUEUE", kind)


def _mesh_files(task: dict) -> dict:
    """Identity of the task's mesh on disk: only a directory inside the declared roots, only the three named TIFFs."""
    src = task["source"]
    want = src.get("mesh_sha256") or {}
    d = src.get("mesh_dir")
    if not d or not isinstance(want, dict) or not all(k in want for k in ("x.tif", "y.tif", "z.tif")):
        return {"state": "NONE_LOCAL", "why": str(src.get("mesh") or "the task declares no mesh"), "dir": None}
    p = Path(str(d).replace("\\", "/"))
    roots = [Path(r).resolve() for r in paths.serve_roots()]
    try:
        rp = p.resolve()
    except OSError:
        return {"state": "NOT_MOUNTED", "why": "the mesh directory cannot be resolved", "dir": None}
    if not any(paths.within_root(rp, r) for r in roots):
        return {"state": "REFUSED_OUTSIDE_DECLARED_ROOTS", "why": "the mesh directory is outside every declared root", "dir": None}
    if not all((rp / f).is_file() for f in ("x.tif", "y.tif", "z.tif")):
        return {"state": "NOT_MOUNTED", "why": "the hashed mesh is not present on this machine", "dir": None}
    got = {f: _sha((rp / f).read_bytes()) for f in ("x.tif", "y.tif", "z.tif")}
    bad = [f for f in got if got[f] != want[f]]
    if bad:
        return {"state": "HASH_MISMATCH", "why": "%s differ from the sealed hashes, so this mesh is not the task's mesh" % ", ".join(bad), "dir": None}
    combined = _sha("\n".join("%s:%s" % (f, got[f]) for f in sorted(got)).encode("utf-8"))
    return {"state": "AVAILABLE_LOCALLY", "dir": rp, "component_sha256": got, "sha256": combined}


def _layers(kind: str, task: dict, box: dict, mesh: dict) -> list:
    src = task["source"]
    out = [{"id": "raw_ct", "kind": "raw_ct", "label": "Raw CT (fixed contrast)", "permitted": True, "status": "DISPLAY_ONLY",
            "why": "the exact volume, drawn with the same fixed contrast as the plane viewer; display settings never change the data"}]
    if QUEUES[kind]["carries_mesh"]:
        mesh_ok = mesh["state"] == "AVAILABLE_LOCALLY"
        out.append({"id": "mesh", "kind": "mesh", "label": "Surface mesh (identity-matched)", "permitted": mesh_ok and box["status"] == "RESOLVED",
                    "status": "GEOMETRY_ONLY" if mesh_ok else mesh["state"], "producer": "tifxyz mesh, hashed by the science lane",
                    "source_volume": src.get("exact_eligible_volume"), "pitch_um": (task.get("geometry") or {}).get("native_voxel_um"),
                    "transform": {"kind": "identity_voxel_zyx"}, "sha256": mesh.get("sha256"), "component_sha256": mesh.get("component_sha256"),
                    "why": "drawn only because its three TIFFs match the sealed hashes and it is in the exact volume's voxel units"
                    if mesh_ok else mesh["why"]})
    else:
        out.append({"id": "mesh", "kind": "mesh", "label": "Surface mesh", "permitted": False, "status": "NONE_LOCAL", "why": mesh["why"]})
    for cid, label in (("cavity_mask", "Cavity inspection"), ("sdf_mask", "Signed-distance mask inspection"),
                       ("paired_surface", "Paired-surface inspection"), ("between_wrap_mask", "Between-wrap inspection")):
        out.append({"id": cid, "kind": cid, "label": label, "permitted": False, "status": "UNAVAILABLE",
                    "why": "no registered, hash-bound %s artifact exists for this volume and region; a registered artifact of another type does not stand in for it" % cid})
    out.append({"id": "prediction", "kind": "prediction", "label": "Model outputs", "permitted": False, "status": "WITHHELD_FROM_BLINDED_TASK",
                "why": "a blinded, unreviewed task shows no model output. Any model output must carry the MODEL OUTPUT contract (provider, exposure, sabotage/control verdict, qualification) before it can be offered anywhere, and none is bound to this task"})
    return out


def bind(kind: str, task_id: str, *, directory: Path | None = None) -> dict:
    task, payload = find_task(kind, task_id, directory=directory)
    box = _box(kind, task)
    src = task["source"]
    mesh = _mesh_files(task) if box["status"] == "RESOLVED" else {"state": "NOT_APPLICABLE", "why": "no resolved location"}
    geom = task.get("geometry") or {}
    volume_name = src.get("exact_eligible_volume") or src.get("exact_volume")
    pitch = geom.get("native_voxel_um") if QUEUES[kind]["pitch_from"] == "geometry" else src.get("voxel_pitch_um")
    from argus.core import fiber_capability_matrix as FM

    disclosure = COORDINATE_DISCLOSURE[QUEUES[kind]["coordinates"]]
    out = {
        "schema": SCHEMA,
        "task": {"kind": kind, "task_id": task["task_id"], "task_sha256": task["task_sha256"], "queue_root_sha256": payload["root_sha256"],
                 "review_state": (task.get("review") or {}).get("review_state", "UNREVIEWED")},
        "scroll": src.get("physical_scroll"),
        "volume": _volume_id(volume_name),
        "level": box["level"],
        "pitch_um": pitch,
        "coordinate_status": box["status"],
        "geometry": {"state": geom.get("state"), "watermarked": geom.get("watermarked"),
                     "orientation": geom.get("orientation", "ORIENTATION_UNCONFIRMED"),
                     "target_gate": {k: (src.get("eligible_target_gate") or {}).get(k) for k in ("verdict", "operation_class", "registry_sha256")}
                     if src.get("eligible_target_gate") else None},
        "coordinate_system": {**disclosure, "fine_grid_screening_indices": "SEPARATE_COORDINATE_SYSTEM_NOT_USED_TO_POSITION_THE_VIEWER",
                              "fine_grid_statement": FINE_GRID_STATEMENT},
        "layers": _layers(kind, task, box, mesh),
        "fiber_capabilities": FM.matrix(directory),
        "claims": list(CLAIMS),
    }
    if box["status"] == "RESOLVED":
        out["roi"] = {"min_zyx": box["min_zyx"], "max_zyx": box["max_zyx"], "centre_zyx": box["centre_zyx"], "halo_voxels": box["halo_voxels"], "units": box["units"]}
    else:
        out["refusal"] = {"code": "COORDINATE_UNRESOLVED", "why": box["why"]}
    return out


def mesh_in_roi(kind: str, task_id: str, *, directory: Path | None = None, max_triangles: int = MAX_MESH_TRIANGLES) -> dict:
    """The task's own mesh, clipped to its ROI plus halo, as triangles in (z, y, x) level-0 voxel units."""
    import numpy as np

    task, _ = find_task(kind, task_id, directory=directory)
    box = _box(kind, task)
    if box["status"] != "RESOLVED":
        raise BindingRefused("COORDINATE_UNRESOLVED", box["why"])
    mesh = _mesh_files(task)
    if mesh["state"] != "AVAILABLE_LOCALLY":
        raise BindingRefused("MESH_" + mesh["state"], mesh["why"])
    import tifffile
    from argus.core.meshview import _max_pair_ratio, _median_step
    from argus.adapters.published_mesh import MAX_STEP_RATIO

    d = mesh["dir"]
    X, Y, Z = (tifffile.imread(d / f).astype(np.float64) for f in ("x.tif", "y.tif", "z.tif"))
    ok = np.isfinite(X) & np.isfinite(Y) & np.isfinite(Z) & (X > 0)
    P = np.stack([Z, Y, X], -1)
    halo = box["halo_voxels"]
    lo = np.array(box["min_zyx"], float) - halo
    hi = np.array(box["max_zyx"], float) + halo
    inside = ok & np.all((P >= lo) & (P <= hi), axis=-1)
    med = _median_step(P, ok)
    h, w = ok.shape
    quads = []
    for i in range(h - 1):
        for j in range(w - 1):
            if not (ok[i, j] and ok[i, j + 1] and ok[i + 1, j] and ok[i + 1, j + 1]):
                continue
            if not (inside[i, j] or inside[i, j + 1] or inside[i + 1, j] or inside[i + 1, j + 1]):
                continue
            if med > 0 and _max_pair_ratio((P[i, j], P[i, j + 1], P[i + 1, j], P[i + 1, j + 1]), med) > MAX_STEP_RATIO:
                continue
            quads.append((i, j))
    tris = 2 * len(quads)
    skipped = 0
    if tris > max_triangles:
        keep = max(1, max_triangles // 2)
        step = math.ceil(len(quads) / keep)
        skipped = len(quads) - len(quads[::step])
        quads = quads[::step]
    index: dict = {}
    verts: list = []
    faces: list = []

    def vid(a: int, b: int) -> int:
        k = (a, b)
        if k not in index:
            index[k] = len(verts) // 3
            verts.extend((float(P[a, b, 0]), float(P[a, b, 1]), float(P[a, b, 2])))
        return index[k]

    for i, j in quads:
        a, b, c, e = vid(i, j), vid(i, j + 1), vid(i + 1, j), vid(i + 1, j + 1)
        faces.extend((a, b, c, b, e, c))
    return {"schema": "argus-workbench-task-mesh-v1", "task_id": task_id, "task_sha256": task["task_sha256"], "scroll": task["source"]["physical_scroll"],
            "volume": _volume_id(task["source"].get("exact_eligible_volume")), "axis_order": "zyx", "units": box["units"],
            "pitch_um": (task.get("geometry") or {}).get("native_voxel_um"), "transform": {"kind": "identity_voxel_zyx"},
            "sha256": mesh["sha256"], "component_sha256": mesh["component_sha256"], "status": "GEOMETRY_ONLY",
            "vertices": verts, "faces": faces, "n_vertices": len(verts) // 3, "n_triangles": len(faces) // 3,
            "quads_dropped_by_triangle_budget": skipped, "jumping_quads_not_bridged": True}
