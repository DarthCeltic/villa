"""Deterministic scroll profiler: what a scroll IS, measured, before anything is chosen."""
from __future__ import annotations

import hashlib
import json

import numpy as np

PROFILER_VERSION = "argus-profiler-v1"

QUANTILES = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)

LATTICE_N = 8


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def lattice_positions(h: int, w: int, patch: int, n: int = LATTICE_N) -> list:
    """A fixed grid of top-left origins."""
    if h < patch or w < patch:
        return []
    ys = np.linspace(0, h - patch, min(n, max(1, h // patch)), dtype=int)
    xs = np.linspace(0, w - patch, min(n, max(1, w // patch)), dtype=int)
    return [(int(y), int(x)) for y in sorted(set(ys.tolist()))
            for x in sorted(set(xs.tolist()))]


def voxel_stats(samples: np.ndarray) -> dict:
    """Intensity distribution."""
    a = np.asarray(samples, dtype=np.float64).ravel()
    if a.size == 0:
        return {"n": 0}
    q = np.quantile(a, QUANTILES)
    p1, p99 = float(q[0]), float(q[-1])
    return {"n": int(a.size),
            "quantiles": {str(k): round(float(v), 4) for k, v in zip(QUANTILES, q)},
            "mean": round(float(a.mean()), 4), "std": round(float(a.std()), 4),
            "dynamic_range_p1_p99": round(p99 - p1, 4),
            "saturated_low_fraction": round(float((a <= 0).mean()), 6),
            "saturated_high_fraction": round(float((a >= 255).mean()), 6)}


def depth_structure(vol: np.ndarray) -> dict:
    """Depth profile shape, and whether it is asymmetric."""
    v = np.asarray(vol, dtype=np.float64)
    prof = v.mean(axis=(1, 2)) if v.ndim == 3 else v.mean(axis=0)
    prof = prof - prof.mean()
    rev = prof[::-1]
    denom = float(np.linalg.norm(prof) * np.linalg.norm(rev))
    sym = float(prof @ rev / denom) if denom > 0 else 1.0
    peak = int(np.argmax(v.mean(axis=(1, 2)))) if v.ndim == 3 else int(np.argmax(prof))
    return {"planes": int(v.shape[0]),
            "profile_mean": [round(float(x), 4) for x in
                             (v.mean(axis=(1, 2)) if v.ndim == 3 else prof)],
            "symmetry_correlation": round(sym, 4),
            "asymmetry": round(1.0 - sym, 4),
            "peak_plane": peak,
            "peak_offset_from_centre": peak - (v.shape[0] // 2)}


def fiber_structure(plane: np.ndarray) -> dict:
    """Dominant orientation and anisotropy from the gradient structure tensor."""
    a = np.asarray(plane, dtype=np.float64)
    gy, gx = np.gradient(a)
    jxx, jyy, jxy = float((gx * gx).mean()), float((gy * gy).mean()), float((gx * gy).mean())
    tr = jxx + jyy
    det = jxx * jyy - jxy * jxy
    disc = max(tr * tr / 4.0 - det, 0.0) ** 0.5
    l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
    coherence = ((l1 - l2) / (l1 + l2)) if (l1 + l2) > 0 else 0.0
    theta = 0.5 * float(np.arctan2(2 * jxy, jxx - jyy))
    return {"orientation_rad": round(theta, 4),
            "orientation_deg": round(float(np.degrees(theta)), 2),
            "coherence": round(float(coherence), 4),
            "energy": round(tr, 6)}


def row_periodicity(plane: np.ndarray, pitch_um: float) -> dict:
    """Spacing of the strongest horizontal periodicity, in microns."""
    a = np.asarray(plane, dtype=np.float64)
    rows = a.mean(axis=1)
    rows = rows - rows.mean()
    if rows.size < 8 or not np.any(rows):
        return {"detected": False}
    spec = np.abs(np.fft.rfft(rows))
    spec[0] = 0.0
    k = int(np.argmax(spec))
    if k == 0:
        return {"detected": False}
    period_px = rows.size / k
    return {"detected": True,
            "period_px": round(float(period_px), 3),
            "period_um": round(float(period_px * pitch_um), 2),
            "relative_strength": round(float(spec[k] / (spec.sum() + 1e-12)), 4),
            "status": "SECONDARY_FEATURE_ONLY",
            "why": ("fiber and tiling artefacts are periodic too; this may support a "
                    "candidate and may never create one")}


def render_quality(coverage: np.ndarray, valid_core: np.ndarray | None = None) -> dict:
    c = np.asarray(coverage) > 0
    out = {"coverage_fraction": round(float(c.mean()), 6),
           "hole_fraction": round(float(1.0 - c.mean()), 6)}
    if valid_core is not None:
        v = np.asarray(valid_core) > 0
        out["valid_core_fraction"] = round(float(v.mean()), 6)
        out["valid_core_within_coverage"] = round(
            float((v & c).sum() / max(int(c.sum()), 1)), 6)
    return out


def geometry_stats(normals: np.ndarray | None, area_mm2: float | None) -> dict:
    out = {"area_mm2": None if area_mm2 is None else round(float(area_mm2), 4)}
    if normals is None:
        out["normal_consistency"] = None
        return out
    n = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.where(norm == 0, 1.0, norm)
    mean = n.mean(axis=0)
    out["normal_consistency"] = round(float(np.linalg.norm(mean)), 4)
    out["normal_consistency_note"] = ("1.0 means every normal points the same way; a low "
                                      "value on a supposedly flat patch means the surface "
                                      "is folded or the mesh is wrong")
    return out


def build_card(scroll: str, segment: str, acquisition: dict, *,
               samples: np.ndarray, vol: np.ndarray, plane: np.ndarray,
               coverage: np.ndarray, valid_core=None, normals=None,
               area_mm2=None, pitch_um: float, inputs_sha256: str):
    """Assemble a ScrollDomainCard from measurements."""
    from argus.core.adaptation import ScrollDomainCard
    fib = fiber_structure(plane)
    fib["row_periodicity"] = row_periodicity(plane, pitch_um)
    return ScrollDomainCard(
        scroll=scroll, segment=segment, acquisition=dict(acquisition),
        voxel_stats=voxel_stats(samples), depth_structure=depth_structure(vol),
        fiber_structure=fib, geometry=geometry_stats(normals, area_mm2),
        render_quality=render_quality(coverage, valid_core),
        profiler_version=PROFILER_VERSION, inputs_sha256=inputs_sha256)



class RecipeRegistry:
    """Maps a measured fingerprint to a previously QUALIFIED configuration."""

    def __init__(self, recipes=(), max_distance: float = 0.35):
        self.recipes = list(recipes)
        self.max_distance = float(max_distance)

    @staticmethod
    def distance(a, b) -> float:
        """Normalised disagreement over the comparable numeric readings."""
        def flat(card):
            out = {}
            for grp in ("acquisition", "voxel_stats", "depth_structure",
                        "fiber_structure", "geometry", "render_quality"):
                for k, v in (getattr(card, grp) or {}).items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        out["%s.%s" % (grp, k)] = float(v)
            return out
        fa, fb = flat(a), flat(b)
        keys = set(fa) | set(fb)
        if not keys:
            return 1.0
        tot = 0.0
        for k in keys:
            if k not in fa or k not in fb:
                tot += 1.0
                continue
            x, y = fa[k], fb[k]
            scale = max(abs(x), abs(y), 1e-9)
            tot += min(abs(x - y) / scale, 1.0)
        return tot / len(keys)

    def recommend(self, card, cards_by_fingerprint: dict) -> dict:
        best, bestd = None, None
        for r in self.recipes:
            if not r.is_qualified():
                continue
            for fp in r.domain_fingerprints:
                other = cards_by_fingerprint.get(fp)
                if other is None:
                    continue
                d = self.distance(card, other)
                if bestd is None or d < bestd:
                    best, bestd = r, d
        if best is None:
            return {"verdict": "NO_MATCH", "why": "no qualified recipe has a comparable card"}
        if bestd > self.max_distance:
            return {"verdict": "NO_MATCH", "nearest": best.recipe_id,
                    "distance": round(bestd, 4), "max_distance": self.max_distance,
                    "why": ("the nearest qualified recipe is beyond the declared ceiling; a "
                            "scroll unlike anything qualified needs a new qualification, "
                            "not the least-bad match")}
        return {"verdict": "RECOMMEND", "recipe_id": best.recipe_id,
                "distance": round(bestd, 4),
                "qualified_on": list(best.qualified_on),
                "note": ("a recommendation is a starting configuration, not a "
                         "qualification; this scroll must still pass its own worst-fold "
                         "gate before it may be used for hunting")}
