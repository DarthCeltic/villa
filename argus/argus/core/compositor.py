"""Valid-core compositing: assemble a full-surface map from patch inference without seams."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Tile:
    """One read window and the core region that is actually kept from it."""

    y0: int
    x0: int
    h: int
    w: int
    ry0: int
    rx0: int
    rh: int
    rw: int
    vy0: int
    vx0: int
    vh: int
    vw: int

    @property
    def core_in_read(self) -> tuple[int, int, int, int]:
        """Where the core sits inside the read window: (y, x, h, w)."""
        return (self.y0 - self.ry0, self.x0 - self.rx0, self.h, self.w)

    @property
    def has_padding(self) -> bool:
        return (self.vh, self.vw) != (self.rh, self.rw)


def plan_tiles(height: int, width: int, patch: int, halo: int) -> list[Tile]:
    """Tile `height x width` so cores partition it exactly and each carries a halo."""
    if patch <= 0 or halo < 0:
        raise ValueError("patch must be positive and halo non-negative")
    core = patch - 2 * halo
    if core <= 0:
        raise ValueError("halo %d leaves no core inside patch %d" % (halo, patch))
    tiles: list[Tile] = []
    for y0 in range(0, height, core):
        for x0 in range(0, width, core):
            h = min(core, height - y0)
            w = min(core, width - x0)
            ry0, rx0 = y0 - halo, x0 - halo
            rh, rw = h + 2 * halo, w + 2 * halo
            vy0 = max(0, -ry0)
            vx0 = max(0, -rx0)
            vy1 = min(rh, height - ry0)
            vx1 = min(rw, width - rx0)
            tiles.append(Tile(y0, x0, h, w, ry0, rx0, rh, rw,
                              vy0, vx0, vy1 - vy0, vx1 - vx0))
    return tiles


def coverage(tiles: list[Tile], height: int, width: int) -> np.ndarray:
    """How many times each output pixel is written."""
    cov = np.zeros((height, width), dtype=np.int32)
    for t in tiles:
        cov[t.y0:t.y0 + t.h, t.x0:t.x0 + t.w] += 1
    return cov


def valid_mask(tile: Tile) -> np.ndarray:
    """True where the read window holds real data, False where the reader padded."""
    m = np.zeros((tile.rh, tile.rw), dtype=bool)
    m[tile.vy0:tile.vy0 + tile.vh, tile.vx0:tile.vx0 + tile.vw] = True
    return m


def normalize_valid(arr: np.ndarray, mask: np.ndarray,
                    low: float = 1.0, high: float = 99.0) -> np.ndarray:
    """Robust percentile normalization computed over valid pixels ONLY (villa PR #1646)."""
    if mask.shape != arr.shape[-2:]:
        raise ValueError("mask %s does not match the spatial shape %s"
                         % (mask.shape, arr.shape[-2:]))
    if not mask.any():
        raise ValueError("no valid pixels: refusing to normalize on padding alone")
    sel = arr[..., mask] if arr.ndim > 2 else arr[mask]
    lo = float(np.percentile(sel, low))
    hi = float(np.percentile(sel, high))
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.float32)
    return np.clip((arr.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)


def composite(height: int, width: int, patch: int, halo: int, run_patch,
              dtype=np.float32) -> np.ndarray:
    """Run `run_patch` over a valid-core tiling and assemble the result."""
    out = np.zeros((height, width), dtype=dtype)
    tiles = plan_tiles(height, width, patch, halo)
    for t in tiles:
        m = valid_mask(t)
        p = np.asarray(run_patch(t, m))
        if p.shape != (t.rh, t.rw):
            raise ValueError("run_patch returned %s, expected the read window %s"
                             % (p.shape, (t.rh, t.rw)))
        cy, cx, ch, cw = t.core_in_read
        out[t.y0:t.y0 + t.h, t.x0:t.x0 + t.w] = p[cy:cy + ch, cx:cx + cw]
    return out
