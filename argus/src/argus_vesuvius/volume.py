from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import hashlib
from pathlib import Path
from typing import Any

from .geometry import Region3D


class VolumeDependencyUnavailable(RuntimeError):
    pass


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise VolumeDependencyUnavailable(
            "volume access requires the 'volume' extra: pip install -e .[volume]"
        ) from exc
    return np


def _open_zarr(uri: str, dataset: str | None):
    try:
        import zarr
    except ImportError as exc:
        raise VolumeDependencyUnavailable(
            "OME-Zarr access requires zarr: pip install -e .[volume]"
        ) from exc

    if "://" in uri:
        try:
            import fsspec
        except ImportError as exc:
            raise VolumeDependencyUnavailable(
                "remote OME-Zarr access requires fsspec and the matching protocol backend"
            ) from exc
        store: Any = fsspec.get_mapper(uri)
    else:
        store = uri
    root = zarr.open(store, mode="r")
    return root[dataset] if dataset else root


def read_roi(uri: str, region: Region3D, dataset: str | None = None):
    """Read one bounded ROI from NumPy or OME-Zarr without loading the full volume."""
    np = _numpy()
    if "://" not in uri and Path(uri).suffix.lower() == ".npy":
        array = np.load(uri, mmap_mode="r", allow_pickle=False)
    else:
        array = _open_zarr(uri, dataset)
    if getattr(array, "ndim", None) != 3:
        raise ValueError(f"expected a 3D array, got shape={getattr(array, 'shape', None)}")
    if region.z1 > array.shape[0] or region.y1 > array.shape[1] or region.x1 > array.shape[2]:
        raise ValueError(f"ROI {region.bbox_zyx} exceeds volume shape {tuple(array.shape)}")
    return np.asarray(array[region.z0 : region.z1, region.y0 : region.y1, region.x0 : region.x1])


def summarize_roi(uri: str, region: Region3D, dataset: str | None = None) -> dict[str, Any]:
    np = _numpy()
    roi = read_roi(uri, region, dataset)
    contiguous = np.ascontiguousarray(roi)
    finite = np.isfinite(contiguous)
    return {
        "schema_version": "argus_vesuvius.volume_roi.v1",
        "uri": uri,
        "dataset": dataset,
        "region": region.to_mapping(),
        "shape": list(contiguous.shape),
        "dtype": str(contiguous.dtype),
        "voxel_count": int(contiguous.size),
        "byte_count": int(contiguous.nbytes),
        "finite_voxel_count": int(finite.sum()),
        "minimum": float(contiguous[finite].min()) if finite.any() else None,
        "maximum": float(contiguous[finite].max()) if finite.any() else None,
        "mean": float(contiguous[finite].mean()) if finite.any() else None,
        "standard_deviation": float(contiguous[finite].std()) if finite.any() else None,
        "content_sha256": hashlib.sha256(contiguous.tobytes(order="C")).hexdigest(),
    }


def open_pyramid_level(uri: str, level: int):
    """Open one pyramid level of an OME-Zarr volume, with its scale factor."""
    import fsspec
    import zarr

    import os

    timeouts = {"connect_timeout": 10, "read_timeout": 30}
    if uri.startswith("s3://") and not os.environ.get("VESUVIUS_NO_CACHE"):
        cache_dir = os.environ.get("VESUVIUS_CACHE", _argus_public_path('cache', 'chunks'))
        store = fsspec.get_mapper(
            f"simplecache::{uri}", s3={"anon": True, "config_kwargs": timeouts},
            simplecache={"cache_storage": cache_dir, "same_names": False})
    else:
        store = (fsspec.get_mapper(uri, anon=True, config_kwargs=timeouts)
                 if uri.startswith("s3://") else uri)
    root = zarr.open(store, mode="r")
    attrs = dict(root.attrs)
    scale = 1.0
    if "multiscales" in attrs:
        for d in attrs["multiscales"][0]["datasets"]:
            if str(d["path"]) == str(level):
                sc = d.get("coordinateTransformations", [{}])[0].get("scale")
                if sc:
                    scale = float(sc[0])
    return root[str(level)], scale
