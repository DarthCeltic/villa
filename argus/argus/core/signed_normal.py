"""Signed-normal resolver: which side of a tifxyz sheet is \"interior\", with an honest confidence."""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

WINDING_FIELD = "WINDING_FIELD"
LOCAL_WINDING_CONSTRAINT = "LOCAL_WINDING_CONSTRAINT"
NEIGHBOR_AGREEMENT = "NEIGHBOR_AGREEMENT"
UMBILICUS_RADIAL = "UMBILICUS_RADIAL"
MANUAL = "MANUAL"
METHODS = (WINDING_FIELD, LOCAL_WINDING_CONSTRAINT, NEIGHBOR_AGREEMENT, UMBILICUS_RADIAL, MANUAL)

TRUSTED = "TRUSTED"
UNCERTAIN = "UNCERTAIN"
HINT_ONLY = "HINT_ONLY"
REFUSED = "REFUSED"
CONFIDENCE_LEVELS = (TRUSTED, UNCERTAIN, HINT_ONLY, REFUSED)
CONFIDENCE_RANK = {REFUSED: 0, HINT_ONLY: 1, UNCERTAIN: 2, TRUSTED: 3}

ORIENTATION_CONFIRMED = "CONFIRMED"
ORIENTATION_UNCONFIRMED = "ORIENTATION_UNCONFIRMED"
ORIENTATION_CONFLICT = "ORIENTATION_CONFLICT"
MANUAL_VERIFICATION = "MANUAL_VERIFICATION"
INDEPENDENT_CUES = ("PHOTOMETRIC", "FIBER_DIRECTION", "OFFICIAL_ORIENTATION_LABEL")
ONE_SIDED_MAX_DEPTH_LAYERS = 4

TRUSTED_MIN_AGREEMENT = 0.90
TRUSTED_MIN_MEAN_DELTA = 0.05
TRUSTED_MAX_FOLD_FRACTION = 0.10
DEFAULT_MIN_VALID = 8
DEFAULT_MIN_VALID_FRACTION = 0.5
DEFAULT_MIN_DELTA = 0.01
DEFAULT_MIN_PAIRS = 8
UMBILICUS_MIN_AGREEMENT = 0.75
UMBILICUS_MIN_COS = 0.05
INTERIOR_MAX_FOLD_FRACTION = 0.05

_COMBINE_PREFERENCE = (WINDING_FIELD, MANUAL, LOCAL_WINDING_CONSTRAINT, NEIGHBOR_AGREEMENT, UMBILICUS_RADIAL)


@dataclass
class SignDecision:
    sign: int | None
    method: str
    confidence_level: str
    agreement_fraction: float = 0.0
    n_valid: int = 0
    mean_winding_delta: float | None = None
    fold_region_fraction: float | None = None
    ambiguity: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    independent_evidence: list[str] = field(default_factory=list)
    orientation_conflict: bool = False

    @property
    def is_trusted(self) -> bool:
        return self.confidence_level == TRUSTED

    @property
    def orientation_status(self) -> str:
        if self.orientation_conflict:
            return ORIENTATION_CONFLICT
        if self.sign in (1, -1) and self.independent_evidence:
            return ORIENTATION_CONFIRMED
        return ORIENTATION_UNCONFIRMED

    def to_dict(self) -> dict[str, Any]:
        return {
            "sign": self.sign,
            "method": self.method,
            "confidence_level": self.confidence_level,
            "agreement_fraction": _json_float(self.agreement_fraction),
            "n_valid": int(self.n_valid),
            "mean_winding_delta": _json_float(self.mean_winding_delta),
            "fold_region_fraction": _json_float(self.fold_region_fraction),
            "ambiguity": bool(self.ambiguity),
            "history": copy.deepcopy(self.history),
            "reasons": list(self.reasons),
            "orientation_status": self.orientation_status,
            "independent_evidence": list(self.independent_evidence),
        }


@dataclass(frozen=True)
class FoldReport:
    fold_mask: np.ndarray
    fold_fraction: float
    n_regions: int
    region_sizes: list[int]
    n_valid_vertices: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_fraction": _json_float(self.fold_fraction),
            "n_regions": int(self.n_regions),
            "region_sizes": list(self.region_sizes),
            "n_valid_vertices": int(self.n_valid_vertices),
        }


def _json_float(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _entry(who: str, what: str, when: str | None, **extra: Any) -> dict[str, Any]:
    return {"who": who, "when": when, "what": what, **extra}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sign_from_winding(
    w_plus: Any,
    w_minus: Any,
    *,
    min_valid: int = DEFAULT_MIN_VALID,
    min_delta: float = DEFAULT_MIN_DELTA,
    min_valid_fraction: float = DEFAULT_MIN_VALID_FRACTION,
    fold_region_fraction: float = 0.0,
) -> SignDecision:
    """Vote per sample toward the side with the SMALLER winding number."""
    wp = np.asarray(w_plus, dtype=np.float64).ravel()
    wm = np.asarray(w_minus, dtype=np.float64).ravel()
    if wp.shape != wm.shape:
        raise ValueError(f"w_plus/w_minus size mismatch: {wp.size} vs {wm.size}")
    n_total = int(wp.size)
    valid = np.isfinite(wp) & np.isfinite(wm)
    n_valid = int(valid.sum())
    fold = float(fold_region_fraction)
    if not 0.0 <= fold <= 1.0:
        raise ValueError("fold_region_fraction must be in [0, 1]")

    if n_valid < min_valid or (n_total and n_valid / n_total < min_valid_fraction):
        return SignDecision(
            sign=None, method=WINDING_FIELD, confidence_level=REFUSED,
            agreement_fraction=0.0, n_valid=n_valid, mean_winding_delta=None,
            fold_region_fraction=fold, ambiguity=True,
            reasons=[f"only {n_valid}/{n_total} valid winding samples "
                     f"(need >= {min_valid} and fraction >= {min_valid_fraction})"],
        )

    dw = wp[valid] - wm[valid]
    n_pos = int((dw < -min_delta).sum())
    n_neg = int((dw > min_delta).sum())
    agreement = max(n_pos, n_neg) / n_valid
    mean_delta = float(np.mean(np.abs(dw)))
    sign: int | None = None if n_pos == n_neg else (1 if n_pos > n_neg else -1)

    blockers: list[str] = []
    if sign is None:
        blockers.append("votes tied")
    if agreement < TRUSTED_MIN_AGREEMENT:
        blockers.append(f"agreement {agreement:.3f} < {TRUSTED_MIN_AGREEMENT}")
    if mean_delta < TRUSTED_MIN_MEAN_DELTA:
        blockers.append(f"mean |dw| {mean_delta:.4f} < {TRUSTED_MIN_MEAN_DELTA}")
    if fold > TRUSTED_MAX_FOLD_FRACTION:
        blockers.append(f"fold fraction {fold:.3f} > {TRUSTED_MAX_FOLD_FRACTION}")

    level = UNCERTAIN if blockers else TRUSTED
    return SignDecision(
        sign=sign, method=WINDING_FIELD, confidence_level=level,
        agreement_fraction=float(agreement), n_valid=n_valid, mean_winding_delta=mean_delta,
        fold_region_fraction=fold, ambiguity=bool(blockers), reasons=blockers,
    )


def sign_from_neighbors(
    sample_signs: Any,
    neighbor_index_pairs: Any,
    *,
    min_pairs: int = DEFAULT_MIN_PAIRS,
) -> tuple[float, SignDecision]:
    """Consistency of a per-vertex sign field over neighbour pairs (indices into the raveled field)."""
    signs = np.asarray(sample_signs, dtype=np.float64).ravel()
    pairs = np.asarray(neighbor_index_pairs, dtype=np.int64).reshape(-1, 2)
    if pairs.size and (pairs.min() < 0 or pairs.max() >= signs.size):
        raise ValueError("neighbor_index_pairs out of range for sample_signs")
    finite = np.isfinite(signs) & (signs != 0)
    s = np.where(finite, np.sign(signs), 0.0)
    n_valid = int(finite.sum())

    both = finite[pairs[:, 0]] & finite[pairs[:, 1]] if len(pairs) else np.zeros(0, dtype=bool)
    n_pairs = int(both.sum())
    if n_pairs < min_pairs:
        return 0.0, SignDecision(
            sign=None, method=NEIGHBOR_AGREEMENT, confidence_level=REFUSED,
            agreement_fraction=0.0, n_valid=n_valid, ambiguity=True,
            reasons=[f"only {n_pairs} valid neighbour pairs (need >= {min_pairs})"],
        )

    agree = s[pairs[both, 0]] == s[pairs[both, 1]]
    consistency = float(agree.mean())
    total = float(s.sum())
    sign: int | None = None if total == 0 else (1 if total > 0 else -1)
    consistent = consistency >= TRUSTED_MIN_AGREEMENT and sign is not None
    reasons = ["neighbour agreement is internal consistency only; it cannot fix the global sign"]
    if not consistent:
        reasons.append(f"consistency {consistency:.3f} < {TRUSTED_MIN_AGREEMENT}")
    return consistency, SignDecision(
        sign=sign, method=NEIGHBOR_AGREEMENT,
        confidence_level=UNCERTAIN if consistent else HINT_ONLY,
        agreement_fraction=consistency, n_valid=n_valid,
        ambiguity=not consistent, reasons=reasons,
    )


def sign_from_umbilicus(
    points: Any,
    normals: Any,
    umbilicus_xyz: Any,
    *,
    min_valid: int = DEFAULT_MIN_VALID,
) -> SignDecision:
    """Radial hint: sign = +1 when the +normal points TOWARD the umbilicus axis."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    nrm = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
    if pts.shape != nrm.shape:
        raise ValueError("points and normals must have the same shape")
    umb = np.atleast_2d(np.asarray(umbilicus_xyz, dtype=np.float64))
    if umb.shape[-1] != 3 or not np.isfinite(umb).all():
        raise ValueError("umbilicus_xyz must be finite with shape (K, 3) or (3,)")

    finite = np.isfinite(pts).all(axis=1) & np.isfinite(nrm).all(axis=1)
    safe_pts = np.where(finite[:, None], pts, 0.0)
    if len(umb) == 1:
        offset = safe_pts - umb[0]
    else:
        tangent = np.gradient(umb, axis=0)
        tnorm = np.linalg.norm(tangent, axis=1, keepdims=True)
        tangent = np.divide(tangent, tnorm, out=np.zeros_like(tangent), where=tnorm > 0)
        _, idx = cKDTree(umb).query(safe_pts)
        offset = safe_pts - umb[idx]
        offset = offset - np.sum(offset * tangent[idx], axis=1, keepdims=True) * tangent[idx]
    rnorm = np.linalg.norm(offset, axis=1)
    nnorm = np.linalg.norm(nrm, axis=1)
    valid = finite & (rnorm > 1e-9) & (nnorm > 1e-9)
    n_valid = int(valid.sum())

    why = ("umbilicus does not solve sign: it fixes the scroll centre, not which face of a sheet "
           "is interior (capped at HINT_ONLY)")
    if n_valid < min_valid:
        return SignDecision(
            sign=None, method=UMBILICUS_RADIAL, confidence_level=REFUSED,
            n_valid=n_valid, ambiguity=True,
            reasons=[f"only {n_valid} usable points (need >= {min_valid})", why],
        )

    cos_to_axis = np.zeros(len(pts))
    cos_to_axis[valid] = -np.sum(nrm[valid] * offset[valid], axis=1) / (nnorm[valid] * rnorm[valid])
    n_pos = int((cos_to_axis > UMBILICUS_MIN_COS).sum())
    n_neg = int((cos_to_axis < -UMBILICUS_MIN_COS).sum())
    agreement = max(n_pos, n_neg) / n_valid
    sign: int | None = None if n_pos == n_neg else (1 if n_pos > n_neg else -1)
    ambiguous = sign is None or agreement < UMBILICUS_MIN_AGREEMENT
    return SignDecision(
        sign=sign, method=UMBILICUS_RADIAL, confidence_level=HINT_ONLY,
        agreement_fraction=float(agreement), n_valid=n_valid, ambiguity=bool(ambiguous),
        reasons=[why],
    )


def _pick_signed(cands: Sequence[SignDecision]) -> SignDecision:
    def key(d: SignDecision) -> tuple[int, int, float, int]:
        pref = _COMBINE_PREFERENCE.index(d.method) if d.method in _COMBINE_PREFERENCE else len(_COMBINE_PREFERENCE)
        return (-CONFIDENCE_RANK[d.confidence_level], pref, -d.agreement_fraction, -d.n_valid)

    return min(cands, key=key)


def _agreeing_evidence(decs: Sequence[SignDecision], sign: int | None) -> list[str]:
    """Independent evidence carried only by decisions that AGREE with the chosen sign."""
    out: list[str] = []
    for d in decs:
        if d.sign is not None and d.sign == sign:
            for e in d.independent_evidence:
                if e not in out:
                    out.append(e)
    return out


def combine(decisions: Iterable[SignDecision]) -> SignDecision:
    """Merge estimator decisions without ever manufacturing trust."""
    decs = list(decisions)
    if not decs:
        return SignDecision(sign=None, method=MANUAL, confidence_level=REFUSED, ambiguity=True,
                            reasons=["no decisions to combine"])
    signed = [d for d in decs if d.sign is not None and d.confidence_level != REFUSED]
    folds = [d.fold_region_fraction for d in decs if d.fold_region_fraction is not None]
    fold = max(folds) if folds else None
    sources = [f"{d.method}:{d.confidence_level}:{d.sign}" for d in decs]

    trusted = [d for d in signed if d.confidence_level == TRUSTED]
    if trusted:
        if len({d.sign for d in trusted}) > 1:
            return SignDecision(
                sign=None, method=trusted[0].method, confidence_level=UNCERTAIN,
                agreement_fraction=0.0, n_valid=max(d.n_valid for d in trusted),
                fold_region_fraction=fold, ambiguity=True,
                history=[_entry("combine", "trusted decisions disagree; no winner", None, sources=sources)],
                reasons=["TRUSTED decisions disagree on sign"],
            )
        chosen = _pick_signed(trusted)
        history = copy.deepcopy(chosen.history)
        for d in decs:
            if d is not chosen and d.sign is not None and d.sign != chosen.sign:
                history.append(_entry(
                    "combine", f"{d.method} ({d.confidence_level}) disagrees with {chosen.method}; "
                    f"{chosen.method} kept", None, dissent_sign=d.sign, kept_sign=chosen.sign))
        history.append(_entry("combine", f"selected {chosen.method}", None, sources=sources))
        return replace(chosen, fold_region_fraction=fold, history=history, reasons=list(chosen.reasons),
                       independent_evidence=_agreeing_evidence(decs, chosen.sign),
                       orientation_conflict=any(d.orientation_conflict for d in decs))

    if not signed:
        return SignDecision(
            sign=None, method=decs[0].method, confidence_level=REFUSED, ambiguity=True,
            fold_region_fraction=fold,
            history=[_entry("combine", "no decision carried a usable sign", None, sources=sources)],
            reasons=["no usable sign from any estimator"],
        )

    chosen = _pick_signed(signed)
    disagree = len({d.sign for d in signed}) > 1
    history = copy.deepcopy(chosen.history)
    history.append(_entry("combine", "no TRUSTED decision; confidence not raised", None, sources=sources))
    reasons = list(chosen.reasons) + ["no TRUSTED evidence; agreement among weak hints does not promote"]
    if disagree:
        reasons.append("signed decisions disagree")
    return replace(
        chosen, fold_region_fraction=fold, history=history, reasons=reasons,
        ambiguity=bool(chosen.ambiguity or disagree),
        independent_evidence=_agreeing_evidence(decs, chosen.sign),
        orientation_conflict=any(d.orientation_conflict for d in decs),
    )


def apply_manual_verification(
    decision: SignDecision,
    *,
    by: str,
    verified: bool,
    flip: bool = False,
    note: str = "",
    when: str | None = None,
    fold_region_fraction: float | None = None,
) -> SignDecision:
    """Return a NEW decision with a human verification (and optional flip) appended to history."""
    if not by or not by.strip():
        raise ValueError("`by` is required for a manual verification")
    if decision.sign is None and (flip or verified):
        raise ValueError("cannot flip or verify a decision that has no sign")
    new_sign = -decision.sign if (flip and decision.sign is not None) else decision.sign
    if verified:
        level, ambiguity = TRUSTED, False
    else:
        level = decision.confidence_level if CONFIDENCE_RANK[decision.confidence_level] < CONFIDENCE_RANK[TRUSTED] else UNCERTAIN
        ambiguity = True
    history = copy.deepcopy(decision.history)
    history.append(_entry(
        by, "manual verification" + (" with flip" if flip else ""), when or _now(),
        verified=bool(verified), flipped=bool(flip), prior_sign=decision.sign,
        prior_method=decision.method, prior_confidence=decision.confidence_level, note=note,
    ))
    fold = decision.fold_region_fraction if fold_region_fraction is None else float(fold_region_fraction)
    evidence = [] if flip else list(decision.independent_evidence)
    if verified and MANUAL_VERIFICATION not in evidence:
        evidence.append(MANUAL_VERIFICATION)
    return replace(
        decision, sign=new_sign, method=MANUAL, confidence_level=level, ambiguity=ambiguity,
        fold_region_fraction=fold, history=history, reasons=list(decision.reasons),
        independent_evidence=evidence, orientation_conflict=False if verified else decision.orientation_conflict,
    )


def apply_independent_cue(
    decision: SignDecision,
    *,
    cue: str,
    agrees: bool,
    by: str,
    note: str = "",
    when: str | None = None,
) -> SignDecision:
    """Record the outcome of an INDEPENDENT orientation cue; returns a NEW decision."""
    if cue not in INDEPENDENT_CUES:
        raise ValueError(f"cue must be one of {INDEPENDENT_CUES}, got {cue!r}")
    if not by or not by.strip():
        raise ValueError("`by` is required to record an independent cue")
    if decision.sign is None:
        raise ValueError("cannot record an orientation cue on a decision that has no sign")
    history = copy.deepcopy(decision.history)
    history.append(_entry(by, f"independent cue {cue}: {'agrees' if agrees else 'CONFLICTS'}", when or _now(),
                          cue=cue, agrees=bool(agrees), prior_sign=decision.sign, note=note))
    if agrees:
        evidence = list(decision.independent_evidence)
        if cue not in evidence:
            evidence.append(cue)
        return replace(decision, independent_evidence=evidence, history=history)
    level = decision.confidence_level if CONFIDENCE_RANK[decision.confidence_level] <= CONFIDENCE_RANK[HINT_ONLY] else HINT_ONLY
    return replace(decision, confidence_level=level, ambiguity=True, orientation_conflict=True,
                   independent_evidence=[], history=history,
                   reasons=list(decision.reasons) + [f"independent cue {cue} conflicts with the estimated sign"])


def _as_grid(field_: Any, grid_shape: tuple[int, int] | None) -> np.ndarray:
    arr = np.asarray(field_, dtype=np.float64)
    if grid_shape is not None:
        if arr.size != grid_shape[0] * grid_shape[1]:
            raise ValueError(f"field of size {arr.size} does not fit grid_shape {grid_shape}")
        return arr.reshape(grid_shape)
    if arr.ndim != 2:
        raise ValueError("expected a 2-D field (or pass grid_shape)")
    return arr


def fold_region_report(
    sign_field: Any,
    fold_mask: Any = None,
    *,
    grid_shape: tuple[int, int] | None = None,
) -> FoldReport:
    """Find fold regions: 4-neighbour vertices whose sign flips."""
    grid = _as_grid(sign_field, grid_shape)
    valid = np.isfinite(grid) & (grid != 0)
    s = np.where(valid, np.sign(grid), 0.0)

    marks = np.zeros(grid.shape, dtype=bool)
    flip_v = valid[1:, :] & valid[:-1, :] & (s[1:, :] != s[:-1, :])
    marks[1:, :] |= flip_v
    marks[:-1, :] |= flip_v
    flip_h = valid[:, 1:] & valid[:, :-1] & (s[:, 1:] != s[:, :-1])
    marks[:, 1:] |= flip_h
    marks[:, :-1] |= flip_h
    if fold_mask is not None:
        extra = np.asarray(fold_mask, dtype=bool)
        if extra.shape != grid.shape:
            raise ValueError("fold_mask shape does not match the sign field")
        marks |= extra

    filled = ndimage.binary_fill_holes(marks)
    labels, n_regions = ndimage.label(filled, structure=np.ones((3, 3), dtype=int))
    sizes = sorted((int(x) for x in np.bincount(labels.ravel())[1:]), reverse=True)
    n_valid = int(valid.sum())
    fraction = float((filled & valid).sum() / n_valid) if n_valid else float("nan")
    return FoldReport(fold_mask=filled, fold_fraction=fraction, n_regions=int(n_regions),
                      region_sizes=sizes, n_valid_vertices=n_valid)


def performance_by_region(score_field: Any, fold_mask: Any) -> dict[str, Any]:
    """Mean score inside vs outside folds, so fold performance is always reported separately."""
    score = np.asarray(score_field, dtype=np.float64)
    mask = np.asarray(fold_mask, dtype=bool)
    if score.shape != mask.shape:
        raise ValueError("score_field and fold_mask shapes differ")
    finite = np.isfinite(score)
    inside = score[finite & mask]
    outside = score[finite & ~mask]
    return {
        "fold_mean": float(inside.mean()) if inside.size else None,
        "nonfold_mean": float(outside.mean()) if outside.size else None,
        "n_fold": int(inside.size),
        "n_nonfold": int(outside.size),
        "reported_separately": True,
    }


def interior_only_allowed(decision: Any) -> tuple[bool, str]:
    """Gate for an authoritative interior-only composite."""
    level = getattr(decision, "confidence_level", None)
    sign = getattr(decision, "sign", None)
    if level != TRUSTED:
        return False, f"confidence is {level}, not TRUSTED"
    if sign not in (1, -1):
        return False, "TRUSTED decision carries no usable sign"
    if getattr(decision, "ambiguity", False):
        return False, "decision is flagged ambiguous"
    fold = getattr(decision, "fold_region_fraction", None)
    if fold is None or not math.isfinite(fold):
        return False, "fold region fraction not measured"
    if fold >= INTERIOR_MAX_FOLD_FRACTION:
        return False, f"fold region fraction {fold:.3f} >= {INTERIOR_MAX_FOLD_FRACTION}"
    if getattr(decision, "orientation_conflict", False):
        return False, "ORIENTATION_CONFLICT: an independent cue disagrees with the estimated sign"
    if not getattr(decision, "independent_evidence", None):
        return False, ("ORIENTATION_UNCONFIRMED: the sign comes from centre-direction geometry only, which does "
                       "not say which side is recto; a one-sided composite needs a human verification or an "
                       "independent cue")
    return True, "TRUSTED sign, orientation confirmed by independent evidence, fold fraction below limit"


def recommended_composite(decision: Any) -> dict[str, Any]:
    """Which composite to show by default."""
    allowed, reason = interior_only_allowed(decision)
    return {
        "composite": "ONE_SIDED_INTERIOR" if allowed else "SYMMETRIC",
        "max_one_sided_depth_layers": ONE_SIDED_MAX_DEPTH_LAYERS,
        "orientation_status": getattr(decision, "orientation_status", ORIENTATION_UNCONFIRMED),
        "reason": reason,
    }
