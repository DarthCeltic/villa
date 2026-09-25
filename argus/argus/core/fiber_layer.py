"""A bounded, identity-aware preview adapter for the existing fibre output."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from . import paths

CONTRACT = "argus-fibre-layer-preview-v1"
SCROLL = os.environ.get("ARGUS_FIBRE_LAYER_SCROLL") or None
MANIFEST_REL = "artifacts/fiber_layer/SEAL_fiber_hz_vt.json"
CLASS_LABELS = {0: "background", 1: "vt-fiber", 2: "hz-fiber", 3: "intersection"}
PREVIEW_PATCH = 12


class FiberLayerRefusal(ValueError):
    pass


def _manifest_path() -> Path:
    return paths.resolve_repo_relative(MANIFEST_REL, require=False)


def _load() -> tuple[dict[str, Any], Path]:
    manifest_path = _manifest_path()
    if not manifest_path.is_file():
        raise FiberLayerRefusal("FIBRE_MANIFEST_MISSING: %s" % MANIFEST_REL)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FiberLayerRefusal("FIBRE_MANIFEST_UNREADABLE: %s" % type(exc).__name__) from exc
    argv = list(((manifest.get("command") or {}).get("argv") or []))
    try:
        output = Path(argv[argv.index("--output_dir") + 1])
    except (ValueError, IndexError):
        raise FiberLayerRefusal("FIBRE_OUTPUT_UNDECLARED: command has no output directory")
    return manifest, output


def inventory(scroll: str | None) -> dict[str, Any]:
    """Return the only fibre asset this adapter is allowed to expose for ``scroll``."""
    base = {"schema": CONTRACT, "selected_scroll": scroll, "assets": [], "available": False}
    if not SCROLL or scroll != SCROLL:
        base["why"] = ("the retained fibre output is bound to its declared scroll, not %s; no global "
                        "fibre output is substituted" % (scroll or "an unselected target"))
        return base
    try:
        manifest, output = _load()
    except FiberLayerRefusal as exc:
        base["why"] = str(exc)
        return base
    zarr_path = output / "logits_part_0.zarr"
    coords_path = output / "coordinates_part_0.zarr"
    present = zarr_path.is_dir() and coords_path.is_dir()
    base.update({
        "available": present,
        "physical_scroll": SCROLL,
        "model": (manifest.get("model") or {}).get("repo_id"),
        "revision": (manifest.get("model") or {}).get("revision"),
        "licence": (manifest.get("model") or {}).get("licence"),
        "semantic_state": (manifest.get("model") or {}).get("semantic_state"),
        "declared_labels": CLASS_LABELS,
        "output": str(zarr_path),
        "supported_patch": PREVIEW_PATCH,
        "support": (manifest.get("patch_support") or {}).get("supported"),
        "fill_only": (manifest.get("patch_support") or {}).get("fill_only"),
        "not_a_scientific_result": manifest.get("not_a_scientific_result"),
    })
    if present:
        base["assets"] = [{"kind": "model_output", "path": str(zarr_path),
                           "patch": PREVIEW_PATCH, "status": "available"}]
    if not present:
        base["why"] = "the retained output or its coordinate store is not present"
    return base


def preview(scroll: str | None, patch: int = PREVIEW_PATCH) -> tuple[bytes, dict[str, Any]]:
    """Render one supported patch as a class-colour PNG, with provenance headers available."""
    if not SCROLL or scroll != SCROLL:
        raise FiberLayerRefusal("FIBRE_IDENTITY: output is bound to its declared scroll")
    manifest, output = _load()
    supported = set((manifest.get("patch_support") or {}).get("supported_indices") or [])
    if patch not in supported:
        raise FiberLayerRefusal("FIBRE_PATCH: patch %s is not listed as supported" % patch)
    zarr_path = output / "logits_part_0.zarr"
    if not zarr_path.is_dir():
        raise FiberLayerRefusal("FIBRE_OUTPUT_MISSING: logits_part_0.zarr")
    try:
        import zarr
        arr = zarr.open(str(zarr_path), mode="r")
        logits = np.asarray(arr[patch])
    except Exception as exc:
        raise FiberLayerRefusal("FIBRE_OUTPUT_UNREADABLE: %s" % type(exc).__name__) from exc
    if logits.ndim != 4 or logits.shape[0] != len(CLASS_LABELS):
        raise FiberLayerRefusal("FIBRE_OUTPUT_INVALID: expected [class,z,y,x] patch")
    if not np.isfinite(logits).all():
        raise FiberLayerRefusal("FIBRE_OUTPUT_INVALID: non-finite logits")
    scores = logits.max(axis=3)
    scores -= scores.max(axis=0, keepdims=True)
    probs = np.exp(scores)
    probs /= np.maximum(probs.sum(axis=0, keepdims=True), 1e-12)
    classes = np.argmax(probs, axis=0)
    confidence = np.max(probs, axis=0)
    colours = np.asarray([[20, 20, 20], [66, 180, 150], [238, 164, 72], [198, 104, 220]], dtype=np.uint8)
    rgb = colours[classes]
    uncertain = confidence < 0.5
    rgb = rgb.copy()
    rgb[uncertain] = np.asarray([80, 80, 80], dtype=np.uint8)
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    meta = {
        "schema": CONTRACT, "physical_scroll": SCROLL, "patch": patch,
        "projection": "argmax class after max over the output depth axis",
        "shape": list(logits.shape), "classes": CLASS_LABELS,
        "uncertain_fraction": float(uncertain.mean()),
        "semantic_state": (manifest.get("model") or {}).get("semantic_state"),
        "not_a_scientific_result": manifest.get("not_a_scientific_result"),
        "not_ink": "This is an operational model-output preview, not an ink map or fibre finding.",
    }
    return buf.getvalue(), meta
