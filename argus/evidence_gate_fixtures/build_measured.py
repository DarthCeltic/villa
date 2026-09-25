"""Deterministic builder for the MEASURED defect corpus."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def _write_tifxyz(mesh_dir: Path, X: np.ndarray, Y: np.ndarray, Z: np.ndarray) -> None:
    import tifffile
    mesh_dir.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(mesh_dir / "x.tif", X.astype(np.float32))
    tifffile.imwrite(mesh_dir / "y.tif", Y.astype(np.float32))
    tifffile.imwrite(mesh_dir / "z.tif", Z.astype(np.float32))


def _flat_sheet(rows=8, cols=8, spacing=2.0, z0=5.0):
    u, v = np.meshgrid(np.arange(cols, dtype=np.float64), np.arange(rows, dtype=np.float64))
    X = u * spacing
    Y = v * spacing
    Z = np.full_like(X, z0)
    return X, Y, Z


def _write_zarr_store(root: Path, shape, chunks=(8, 8, 8), *, write_chunks=True):
    """A real zarr v2 layout on disk."""
    meta = {"shape": list(shape), "chunks": list(chunks), "dtype": "|u1", "fill_value": 0,
            "order": "C", "filters": None, "compressor": None, "dimension_separator": "/",
            "zarr_format": 2}
    level_dir = root / "0"
    level_dir.mkdir(parents=True, exist_ok=True)
    (level_dir / ".zarray").write_text(json.dumps(meta))
    if not write_chunks:
        return root.resolve().as_uri()
    rng = np.random.default_rng(20260913)
    arr = rng.integers(1, 255, size=shape, dtype=np.uint8)
    grid = [-(-s // c) for s, c in zip(shape, chunks)]
    for i in range(grid[0]):
        for j in range(grid[1]):
            for k in range(grid[2]):
                cdir = level_dir / str(i) / str(j)
                cdir.mkdir(parents=True, exist_ok=True)
                pad = np.zeros(chunks, np.uint8)
                sub = arr[i * chunks[0]:(i + 1) * chunks[0], j * chunks[1]:(j + 1) * chunks[1],
                          k * chunks[2]:(k + 1) * chunks[2]]
                pad[:sub.shape[0], :sub.shape[1], :sub.shape[2]] = sub
                (cdir / str(k)).write_bytes(pad.tobytes(order="C"))
    return root.resolve().as_uri()


ACQUISITION = {"volume_id": "synthetic-fixture-v1", "voxel_um": 9.362, "energy_kev": 113,
              "pitch_um_per_px": 9.362, "pyramid_level": 0}


def build(out_dir: Path) -> dict:
    """Build all four measured cases under `out_dir`; return a manifest dict."""
    out_dir = Path(out_dir)
    manifest: dict = {"schema": "argus-evidence-fixtures-measured-v1", "cases": {}}

    X, Y, Z = _flat_sheet()

    clean_dir = out_dir / "clean"
    _write_tifxyz(clean_dir / "mesh", X, Y, Z)
    clean_volume = _write_zarr_store(clean_dir / "store", shape=(20, 20, 20))
    manifest["cases"]["clean"] = {
        "expected": "PASS",
        "manifest": {"mesh_dir": str((clean_dir / "mesh").resolve()), "acquisition": ACQUISITION,
                    "volume_shape": [20, 20, 20], "volume_url": clean_volume,
                    "chunk_shape": [8, 8, 8],
                    "orientation_provenance": "AS_WRITTEN"}}

    outside_dir = out_dir / "mesh_outside_volume"
    _write_tifxyz(outside_dir / "mesh", X, Y, Z)
    manifest["cases"]["mesh_outside_volume"] = {
        "expected": "FAIL",
        "manifest": {"mesh_dir": str((outside_dir / "mesh").resolve()), "acquisition": ACQUISITION,
                    "volume_shape": [5, 5, 5], "orientation_provenance": "AS_WRITTEN"}}

    Zs = Z.copy()
    Zs[4, :] = 500.0
    seam_dir = out_dir / "seam_across_sheet"
    _write_tifxyz(seam_dir / "mesh", X, Y, Zs)
    manifest["cases"]["seam_across_sheet"] = {
        "expected": "FAIL",
        "manifest": {"mesh_dir": str((seam_dir / "mesh").resolve()), "acquisition": ACQUISITION,
                    "volume_shape": [600, 20, 20], "orientation_provenance": "AS_WRITTEN"}}

    fill_dir = out_dir / "all_fill"
    _write_tifxyz(fill_dir / "mesh", X, Y, Z)
    fill_volume = _write_zarr_store(fill_dir / "store", shape=(20, 20, 20), write_chunks=False)
    manifest["cases"]["all_fill"] = {
        "expected": "FAIL",
        "manifest": {"mesh_dir": str((fill_dir / "mesh").resolve()), "acquisition": ACQUISITION,
                    "volume_shape": [20, 20, 20], "volume_url": fill_volume,
                    "chunk_shape": [8, 8, 8], "orientation_provenance": "AS_WRITTEN"}}

    (out_dir / "MEASURED_MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    return manifest
