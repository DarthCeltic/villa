"""Optional surface-consistency postprocessing, and the control that makes it meaningful."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FROZEN_RADIUS = 2

ADAPTER = {
    "repo": "danilolapegna/vesuvius-geometry-surface-consistency",
    "commit": "854cf91db064f67570b579ad611d1c4b651c18d7",
    "licence": "MIT",
    "optional": True,
    "note": ("ARGUS must run identically with the adapter absent. The reference "
             "implementation below is used for controlled comparison; if the external "
             "adapter is vendored it must agree with this on the fixture, and any "
             "disagreement is a finding, not something to paper over."),
}


def _plane_means(vol: np.ndarray, radius: int) -> list:
    """Local mean over each of the three axis-aligned (2r+1)x(2r+1) planes."""
    k = 2 * radius + 1
    out = []
    for drop in (0, 1, 2):
        acc = np.zeros_like(vol, dtype=np.float64)
        axes = [a for a in (0, 1, 2) if a != drop]
        for da in range(-radius, radius + 1):
            for db in range(-radius, radius + 1):
                shifted = np.roll(vol, shift=(da, db), axis=tuple(axes))
                acc += shifted
        out.append(acc / (k * k))
    return out


def enhance(vol: np.ndarray, radius: int = FROZEN_RADIUS) -> np.ndarray:
    """Surface-consistency: the strongest of three axis-aligned plane means."""
    if vol.ndim != 3:
        raise ValueError("surface consistency operates on a 3D probability volume")
    v = vol.astype(np.float64)
    return np.maximum.reduce(_plane_means(v, radius)).astype(vol.dtype, copy=False)


def matched_smoothing(vol: np.ndarray, radius: int = FROZEN_RADIUS) -> np.ndarray:
    """The control arm: an isotropic box mean over the SAME support."""
    if vol.ndim != 3:
        raise ValueError("matched smoothing operates on a 3D probability volume")
    v = vol.astype(np.float64)
    acc = np.zeros_like(v)
    n = 0
    for dz in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                acc += np.roll(v, (dz, dy, dx), axis=(0, 1, 2))
                n += 1
    return (acc / n).astype(vol.dtype, copy=False)


@dataclass(frozen=True)
class ArmResult:
    arm: str
    auc: float | None
    n: int


def _auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    s = np.asarray(scores, dtype=np.float64).ravel()
    y = np.asarray(labels).astype(bool).ravel()
    n1 = int(y.sum())
    n0 = int(y.size - n1)
    if n1 == 0 or n0 == 0:
        return None
    order = np.argsort(s, kind="mergesort")
    r = np.empty(s.size, dtype=np.float64)
    r[order] = np.arange(1, s.size + 1, dtype=np.float64)
    srt = s[order]
    i = 0
    while i < srt.size:
        j = i + 1
        while j < srt.size and srt[j] == srt[i]:
            j += 1
        if j - i > 1:
            r[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    return float((r[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def three_arm_compare(vol: np.ndarray, labels: np.ndarray, sample=None,
                      radius: int = FROZEN_RADIUS) -> dict:
    """Raw vs surface-consistency vs matched smoothing, on identical sample points."""
    raw = np.asarray(vol)
    arms = {"raw": raw, "surface_consistency": enhance(raw, radius),
            "matched_smoothing": matched_smoothing(raw, radius)}
    sel = (slice(None) if sample is None else sample)
    y = np.asarray(labels)[sel]
    out = {k: ArmResult(k, _auc(v[sel], y), int(np.asarray(y).size))
           for k, v in arms.items()}
    sc = out["surface_consistency"].auc
    sm = out["matched_smoothing"].auc
    rw = out["raw"].auc
    verdict = "INDETERMINATE"
    if None not in (sc, sm, rw):
        if sc > sm and sc > rw:
            verdict = "GEOMETRIC_GAIN"
        elif sc > rw and sc <= sm:
            verdict = "GAIN_EXPLAINED_BY_SMOOTHING"
        else:
            verdict = "NO_GAIN"
    return {"arms": {k: {"auc": v.auc, "n": v.n} for k, v in out.items()},
            "radius": radius, "verdict": verdict,
            "raw_preserved": True,
            "interpretation": {
                "GEOMETRIC_GAIN": ("surface-consistency beats an isotropic blur of the "
                                   "same support, so the gain is not merely blur"),
                "GAIN_EXPLAINED_BY_SMOOTHING": ("it improves on raw but does not beat "
                                                "matched smoothing -- the advantage is "
                                                "blur, and the geometric claim is not "
                                                "earned"),
                "NO_GAIN": "no improvement over raw",
                "INDETERMINATE": "a degenerate label set; no arm is scoreable",
            }[verdict],
            "not_evidence_of": ("ink. This is rank quality on an already-qualified "
                                "detector and cannot confer qualification.")}
