"""Optional Blender boundary for ARGUS surface-geometry work."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from argus.core import paths, process_hardening

SUPPORTED = {".obj", ".ply", ".stl", ".glb", ".gltf", ".fbx", ".blend"}


def resolve_executable(executable: str | None = None) -> str | None:
    """Resolve an explicit override, PATH, then the verified ARGUS portable install."""
    candidates: list[Path] = []
    if executable:
        candidates.append(Path(executable))
    if os.environ.get("ARGUS_BLENDER"):
        candidates.append(Path(os.environ["ARGUS_BLENDER"]))
    on_path = process_hardening.which_on_path("blender")
    if on_path:
        candidates.append(Path(on_path))
    portable = paths.tools("blender")
    if portable.is_dir():
        candidates.extend(sorted(portable.glob("blender-*-windows-x64/blender.exe"), reverse=True))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def probe(executable: str | None = None) -> dict:
    found = resolve_executable(executable)
    return {
        "provider": "blender",
        "role": "optional_geometry_inspection_repair_and_interchange",
        "adapter_ready": True,
        "available": bool(found),
        "executable": found,
        "modes": ["headless_inspect", "headless_roundtrip", "interactive"],
        "replaces": None,
        "does_not_replace": ["VC3D tracing", "GrowPatch", "vc_flatten"],
    }


def _script() -> Path:
    script = Path(__file__).resolve().parents[2] / "scripts" / "blender_mesh_inspect.py"
    if not script.is_file():
        raise RuntimeError("ARGUS Blender script is missing: %s" % script)
    return script


def _mesh(path: str | Path) -> Path:
    value = Path(path).resolve()
    if value.suffix.lower() not in SUPPORTED:
        raise ValueError("unsupported Blender interchange format: %s" % value.suffix)
    return value


def inspection_command(mesh: str | Path, report: str | Path, *,
                       executable: str | None = None) -> list[str]:
    found = resolve_executable(executable)
    if not found:
        raise RuntimeError("Blender is not installed or is not on PATH")
    return [found, "--background", "--factory-startup", "--python", str(_script()), "--",
            "inspect", str(_mesh(mesh)), str(Path(report).resolve())]


def roundtrip_command(mesh: str | Path, output_mesh: str | Path, report: str | Path, *,
                      executable: str | None = None) -> list[str]:
    """Build a conservative cleanup/export command; the source is never overwritten."""
    source, output = _mesh(mesh), Path(output_mesh).resolve()
    if source == output:
        raise ValueError("Blender round-trip output must not overwrite the source mesh")
    if output.suffix.lower() not in {".obj", ".ply", ".glb", ".gltf"}:
        raise ValueError("unsupported Blender export format: %s" % output.suffix)
    found = resolve_executable(executable)
    if not found:
        raise RuntimeError("Blender is not installed or is not on PATH")
    return [found, "--background", "--factory-startup", "--python", str(_script()), "--",
            "roundtrip", str(source), str(output), str(Path(report).resolve())]


def interactive_command(mesh: str | Path, *, executable: str | None = None) -> list[str]:
    """Build the GUI command using the same importer as the headless paths."""
    found = resolve_executable(executable)
    if not found:
        raise RuntimeError("Blender is not installed or is not on PATH")
    return [found, "--factory-startup", "--python", str(_script()), "--",
            "interactive", str(_mesh(mesh))]
