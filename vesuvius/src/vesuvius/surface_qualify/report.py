"""Deterministic JSON and the PNG overlay.  Pillow only (already a base dependency of vesuvius)."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .core import CLASS_NAMES

_COLORS = {
    0: (0, 0, 0), 1: (120, 90, 160), 2: (150, 180, 150), 3: (230, 150, 60), 4: (40, 160, 90),
    5: (210, 40, 40), 6: (255, 120, 0), 7: (240, 220, 60), 8: (255, 0, 255),
}


def _clean(o: Any) -> Any:
    """Plain, JSON-safe structure: numpy scalars to Python, floats rounded to 9 significant digits, NaN/inf refused."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float):
        if not math.isfinite(o):
            raise ValueError("non-finite float in report")
        return float(f"{o:.9g}")
    return o


def canonical_json(report: dict[str, Any]) -> str:
    return json.dumps(_clean(report), sort_keys=True, indent=1, ensure_ascii=True) + "\n"


def with_replay_hash(report: dict[str, Any]) -> dict[str, Any]:
    """Add replay_sha256: the hash of the canonical JSON without that field.  Two runs on the same input and parameters must agree."""
    body = {k: v for k, v in report.items() if k != "replay_sha256"}
    out = dict(body)
    out["replay_sha256"] = hashlib.sha256(canonical_json(body).encode("ascii")).hexdigest()
    return out


def _block_max(a: np.ndarray, f: int) -> np.ndarray:
    if f <= 1:
        return a
    h, w = (a.shape[0] + f - 1) // f * f, (a.shape[1] + f - 1) // f * f
    pad = np.zeros((h, w), a.dtype)
    pad[: a.shape[0], : a.shape[1]] = a
    return pad.reshape(h // f, f, w // f, f).max(axis=(1, 3))


def render_overlay(arrays: dict[str, np.ndarray], path: str | Path, *, max_px: int = 1200, defect_deg: float = 5.0) -> None:
    """Left: |angle defect| heat map (per vertex, mapped to cells).  Right: class overlay.  Down-sampling takes the block maximum so thin creases stay visible."""
    from PIL import Image, ImageDraw

    cls = arrays["cell_class"]
    h, w = cls.shape
    f = max(1, int(math.ceil(max(h, w) / max_px)))
    dv = np.nan_to_num(np.abs(arrays["angle_defect_deg"]), nan=0.0)
    dc = np.maximum.reduce([dv[:-1, :-1], dv[1:, :-1], dv[:-1, 1:], dv[1:, 1:]])
    heat = _block_max((np.clip(dc / (2.0 * defect_deg), 0.0, 1.0) * 255).astype(np.uint8), f)
    left = np.stack([heat, (heat * 0.35).astype(np.uint8), 255 - heat], -1)
    lut = np.array([_COLORS[k] for k in sorted(_COLORS)], np.uint8)
    right = lut[_block_max(cls, f)]
    gap = np.full((right.shape[0], 4, 3), 255, np.uint8)
    body = np.concatenate([left, gap, right], axis=1)
    img = Image.new("RGB", (max(body.shape[1], 620), body.shape[0] + 14 * 3 + 6), (255, 255, 255))
    img.paste(Image.fromarray(body, "RGB"), (0, 0))
    dr = ImageDraw.Draw(img)
    for i, k in enumerate(sorted(_COLORS)):
        x, y = 4 + (i % 3) * 205, body.shape[0] + 3 + (i // 3) * 14
        dr.rectangle([x, y, x + 9, y + 9], fill=_COLORS[k])
        dr.text((x + 13, y - 1), CLASS_NAMES[k], fill=(0, 0, 0))
    img.save(path, format="PNG", optimize=False, compress_level=6)
