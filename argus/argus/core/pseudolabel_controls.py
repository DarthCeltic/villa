"""Controls for experimental pseudo-labeling."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from argus.core.scroll_ids import normalize as _normalize_scroll

PROVIDER_ID = "EXPERIMENTAL_PSEUDOLABEL_PROVIDER"
LIFECYCLE = "EXPERIMENTAL_RESEARCH_ONLY"
STATUS_ELIGIBLE = "ELIGIBLE_FOR_OPERATOR_REVIEW"

PROMOTION_REQUIREMENTS = (
    "held_out_real_verso_labels",
    "collapse_controls",
    "teacher_student_provenance",
    "cross_scroll_evaluation",
)

SIDES = ("recto", "verso")
OFFICIAL_GROUND_TRUTH = "OFFICIAL_GROUND_TRUTH"
HUMAN = "HUMAN"
PSEUDO_LABEL = "PSEUDO_LABEL"
LABEL_SOURCES = (OFFICIAL_GROUND_TRUTH, HUMAN, PSEUDO_LABEL)
REAL_LABEL_SOURCES = (OFFICIAL_GROUND_TRUTH, HUMAN)

RECTO_SAMPLE_PREFIX = "recto:"

STD_RATIO_MIN = 0.1
NEAR_CONSTANT_STD = 1e-3
NARROW_BAND_WIDTH = 0.02
NARROW_BAND_FRACTION = 0.98

DEFAULT_SCORE_FLOOR = 0.5


class LineageError(ValueError):
    """A lineage record that is internally inconsistent about where its labels came from."""


class PseudoLabelPromotionRefused(RuntimeError):
    """A pseudo-label was presented as real ground truth, or the provider as production."""


@dataclass(frozen=True)
class Lineage:
    """Provenance of one label set."""

    side: str
    scroll_id: str
    volume_id: str
    segment_id: str
    label_source: str
    teacher_id: str | None = None
    student_id: str | None = None
    training_sample_ids: tuple[str, ...] = ()
    recto_training_declared: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "training_sample_ids", tuple(str(s) for s in self.training_sample_ids))
        if self.side not in SIDES:
            raise LineageError(f"side must be one of {SIDES}, got {self.side!r}")
        if self.label_source not in LABEL_SOURCES:
            raise LineageError(f"label_source must be one of {LABEL_SOURCES}, got {self.label_source!r}")
        for name in ("scroll_id", "volume_id", "segment_id"):
            if not str(getattr(self, name)).strip():
                raise LineageError(f"{name} must be a non-empty string")
        if self.label_source == PSEUDO_LABEL and not self.teacher_id:
            raise LineageError("a PSEUDO_LABEL lineage must name its teacher_id")
        if self.side == "verso" and not self.recto_training_declared:
            recto = [s for s in self.training_sample_ids if s.startswith(RECTO_SAMPLE_PREFIX)]
            if recto:
                raise LineageError(
                    "verso lineage names recto training samples without recto_training_declared=True: "
                    f"{recto[:3]}")

    def key(self) -> str:
        samples = hashlib.sha256("\n".join(sorted(self.training_sample_ids)).encode()).hexdigest()[:12]
        return "|".join((self.side, self.scroll_id, self.volume_id, self.segment_id, self.label_source,
                         self.teacher_id or "-", self.student_id or "-", samples))


def assert_not_ground_truth(label_source: str, claimed_as: str | None = None) -> None:
    """Refuse a pseudo-label presented as real ground truth."""
    if label_source not in LABEL_SOURCES:
        raise LineageError(f"unknown label_source {label_source!r}")
    if label_source == PSEUDO_LABEL and claimed_as in REAL_LABEL_SOURCES:
        raise PseudoLabelPromotionRefused(
            f"PSEUDO_LABEL data claimed as {claimed_as}; pseudo-labels are never ground truth")


def _missing_requirements(provider_evidence: Mapping[str, Any] | None) -> list[str]:
    ev = provider_evidence if isinstance(provider_evidence, Mapping) else {}
    return [r for r in PROMOTION_REQUIREMENTS if not ev.get(r)]


def may_be_production(provider_evidence: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    """(eligible_for_operator_review, missing_requirements)."""
    missing = _missing_requirements(provider_evidence)
    return (not missing, missing)


def status_for(provider_evidence: Mapping[str, Any] | None) -> str:
    return STATUS_ELIGIBLE if not _missing_requirements(provider_evidence) else LIFECYCLE



def _binary_entropy(p: np.ndarray, eps: float) -> float:
    q = np.clip(p, eps, 1.0 - eps)
    return float(np.mean(-(q * np.log2(q) + (1.0 - q) * np.log2(1.0 - q))))


def _band_fraction(v: np.ndarray) -> float:
    return float(np.mean(np.abs(v - np.median(v)) <= NARROW_BAND_WIDTH / 2.0))


def _constant_agreement(v: np.ndarray) -> float:
    pos = float(np.mean(v >= 0.5))
    return max(pos, 1.0 - pos)


def collapse_report(teacher_out: Any, student_out: Any, *, eps: float = 1e-9) -> dict[str, Any]:
    """Teacher-student collapse tripwire."""
    reasons: list[str] = []
    out: dict[str, Any] = {
        "n_pairs": 0, "nonfinite_fraction": None, "teacher_std": None, "student_std": None,
        "std_ratio": None, "entropy_ratio": None, "near_constant_fraction": None,
        "teacher_near_constant_fraction": None, "student_constant_agreement": None,
        "teacher_constant_agreement": None, "student_teacher_agreement": None, "correlation": None,
        "degenerate_input": False, "reasons": reasons, "collapsed": True,
    }
    try:
        t = np.asarray(teacher_out, dtype=np.float64)
        s = np.asarray(student_out, dtype=np.float64)
    except (TypeError, ValueError):
        reasons.append("non_numeric_input")
        out["degenerate_input"] = True
        return out
    if t.shape != s.shape:
        reasons.append("shape_mismatch")
        out["degenerate_input"] = True
        return out
    if t.size == 0:
        reasons.append("empty_input")
        out["degenerate_input"] = True
        return out

    finite = np.isfinite(t) & np.isfinite(s)
    out["nonfinite_fraction"] = float(1.0 - finite.mean())
    t, s = t[finite], s[finite]
    out["n_pairs"] = int(t.size)
    if t.size == 0:
        reasons.append("no_finite_values")
        out["degenerate_input"] = True
        return out

    t_std, s_std = float(t.std()), float(s.std())
    out["teacher_std"], out["student_std"] = t_std, s_std
    out["near_constant_fraction"] = _band_fraction(s)
    out["teacher_near_constant_fraction"] = _band_fraction(t)
    out["student_constant_agreement"] = _constant_agreement(s)
    out["teacher_constant_agreement"] = _constant_agreement(t)
    out["student_teacher_agreement"] = float(np.mean((s >= 0.5) == (t >= 0.5)))
    if t_std > eps and s_std > eps:
        out["correlation"] = float(np.corrcoef(t, s)[0, 1])

    in_unit = bool(t.min() >= 0.0 and t.max() <= 1.0 and s.min() >= 0.0 and s.max() <= 1.0)
    if in_unit:
        t_ent = _binary_entropy(t, eps)
        if t_ent > eps:
            out["entropy_ratio"] = _binary_entropy(s, eps) / t_ent

    if t_std <= eps:
        reasons.append("teacher_constant")
        out["degenerate_input"] = True
    else:
        out["std_ratio"] = s_std / t_std
        if out["std_ratio"] < STD_RATIO_MIN:
            reasons.append("student_std_below_tenth_of_teacher")
    if s_std < NEAR_CONSTANT_STD:
        reasons.append("student_near_constant")
    if (out["near_constant_fraction"] > NARROW_BAND_FRACTION
            and out["near_constant_fraction"] > out["teacher_near_constant_fraction"]):
        reasons.append("student_concentrated_in_narrow_band")

    out["collapsed"] = bool(reasons)
    return out



def _overlap(a: Iterable[str], b: Iterable[str]) -> list[str]:
    return sorted(set(a) & set(b))


def leakage_report(train_lineages: Sequence[Lineage], eval_lineages: Sequence[Lineage]) -> dict[str, Any]:
    """Overlap between what the teacher/student trained on and what is evaluated."""
    train_scroll = {_normalize_scroll(l.scroll_id): l.scroll_id for l in train_lineages}
    eval_scroll_keys = {_normalize_scroll(l.scroll_id) for l in eval_lineages}
    scroll_overlap = sorted(train_scroll[k] for k in set(train_scroll) & eval_scroll_keys)

    segment_overlap = _overlap((l.segment_id for l in train_lineages), (l.segment_id for l in eval_lineages))
    volume_overlap = _overlap((l.volume_id for l in train_lineages), (l.volume_id for l in eval_lineages))
    sample_overlap = _overlap((s for l in train_lineages for s in l.training_sample_ids),
                              (s for l in eval_lineages for s in l.training_sample_ids))

    sample_level = bool(segment_overlap or volume_overlap or sample_overlap)
    scroll_level = bool(scroll_overlap)
    eval_all_real = bool(eval_lineages) and all(l.label_source in REAL_LABEL_SOURCES for l in eval_lineages)
    return {
        "segment_overlap": segment_overlap,
        "volume_overlap": volume_overlap,
        "sample_overlap": sample_overlap,
        "scroll_overlap": scroll_overlap,
        "sample_level_leak": sample_level,
        "scroll_level_leak": scroll_level,
        "leaks": sample_level or scroll_level,
        "eval_all_real": eval_all_real,
        "cross_scroll_ok": bool(train_lineages) and eval_all_real and not scroll_level,
    }


Box = tuple[float, float, float, float, float, float]
_MAX_GRID_CELLS = 20_000_000


def _check_boxes(boxes: Sequence[Sequence[float]]) -> list[Box]:
    checked: list[Box] = []
    for b in boxes:
        if len(b) != 6:
            raise ValueError(f"box must be (zmin,ymin,xmin,zmax,ymax,xmax), got {tuple(b)!r}")
        z0, y0, x0, z1, y1, x1 = (float(v) for v in b)
        if z1 < z0 or y1 < y0 or x1 < x0:
            raise ValueError(f"box has max < min: {tuple(b)!r}")
        checked.append((z0, y0, x0, z1, y1, x1))
    return checked


def _union_volume(boxes: Sequence[Box]) -> float:
    if not boxes:
        return 0.0
    edges = [np.unique([b[i] for b in boxes] + [b[i + 3] for b in boxes]) for i in range(3)]
    shape = tuple(len(e) - 1 for e in edges)
    if int(np.prod(shape, dtype=np.int64)) > _MAX_GRID_CELLS:
        raise ValueError("too many distinct box coordinates to compute an exact overlap")
    if min(shape) == 0:
        return 0.0
    grid = np.zeros(shape, dtype=bool)
    for b in boxes:
        lo = [int(np.searchsorted(edges[i], b[i])) for i in range(3)]
        hi = [int(np.searchsorted(edges[i], b[i + 3])) for i in range(3)]
        grid[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]] = True
    d = [np.diff(e) for e in edges]
    return float(np.einsum("i,j,k,ijk->", d[0], d[1], d[2], grid))


def coordinate_overlap_detail(bboxes_a: Sequence[Sequence[float]],
                              bboxes_b: Sequence[Sequence[float]]) -> dict[str, float]:
    """Exact union-based overlap of two box sets (boxes inside one set are not double counted)."""
    a, b = _check_boxes(bboxes_a), _check_boxes(bboxes_b)
    inter = []
    for za0, ya0, xa0, za1, ya1, xa1 in a:
        for zb0, yb0, xb0, zb1, yb1, xb1 in b:
            lo = (max(za0, zb0), max(ya0, yb0), max(xa0, xb0))
            hi = (min(za1, zb1), min(ya1, yb1), min(xa1, xb1))
            if all(h > l for l, h in zip(lo, hi)):
                inter.append((*lo, *hi))
    va, vb, vi = _union_volume(a), _union_volume(b), _union_volume(inter)
    return {
        "volume_a": va, "volume_b": vb, "intersection_volume": vi,
        "fraction_of_a": vi / va if va > 0 else 0.0,
        "fraction_of_b": vi / vb if vb > 0 else 0.0,
    }


def coordinate_overlap(bboxes_a: Sequence[Sequence[float]], bboxes_b: Sequence[Sequence[float]]) -> float:
    """Fraction of B's volume already covered by A."""
    return coordinate_overlap_detail(bboxes_a, bboxes_b)["fraction_of_b"]



def _max_abs_diff(a: Any, b: Any) -> float:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        return float("inf")
    both_nan = np.isnan(a) & np.isnan(b)
    d = np.where(both_nan, 0.0, np.abs(a - b))
    return float(np.max(d)) if d.size else 0.0


def _swapped(x: np.ndarray, swap: tuple[int, int]) -> np.ndarray:
    y = x.copy()
    y[[swap[0], swap[1]]] = y[[swap[1], swap[0]]]
    return y


def channel_swap_test(model_fn: Callable[[np.ndarray], Any], x: Any, *, swap: tuple[int, int] = (0, 1),
                      atol: float = 1e-6) -> dict[str, Any]:
    """Does the model actually use the channel that should distinguish recto from verso?"""
    arr = np.asarray(x)
    a, b = swap
    if arr.ndim < 1 or a == b or not (0 <= a < arr.shape[0] and 0 <= b < arr.shape[0]):
        raise ValueError(f"swap {swap} is not two distinct channels of an array shaped {arr.shape}")
    if np.array_equal(arr[a], arr[b], equal_nan=True):
        raise ValueError("input channels are identical; a channel swap cannot be tested on this input")

    base = model_fn(arr.copy())
    swapped = model_fn(_swapped(arr, swap))
    restored = model_fn(_swapped(_swapped(arr, swap), swap))
    diff = _max_abs_diff(base, swapped)
    changed = diff > atol
    return {
        "swap": (a, b),
        "max_abs_diff": diff,
        "output_changed": bool(changed),
        "roundtrip_ok": _max_abs_diff(base, restored) == 0.0,
        "symmetric_collapse": bool(not changed),
    }


def mirror_orientation_equivariance(model_fn: Callable[[np.ndarray], Any], x: Any, axis: int,
                                    *, atol: float = 1e-6) -> dict[str, Any]:
    """Check f(flip(x)) == flip(f(x)) along a spatial axis."""
    arr = np.asarray(x)
    ax = axis + arr.ndim if axis < 0 else axis
    if not 1 <= ax < arr.ndim:
        raise ValueError(f"axis {axis} is not a spatial axis of an array shaped {arr.shape}")
    out = np.asarray(model_fn(arr.copy()))
    out_ax = ax - (arr.ndim - out.ndim)
    if not 0 <= out_ax < out.ndim:
        raise ValueError("model output has no axis matching the flipped input axis")
    flipped_out = np.asarray(model_fn(np.flip(arr, axis=ax).copy()))
    diff = _max_abs_diff(flipped_out, np.flip(out, axis=out_ax))
    return {"axis": ax, "max_abs_diff": diff, "equivariant": bool(diff <= atol), "atol": atol}



def cross_scroll_gate(eval_scores_by_scroll: Mapping[str, float], *, min_scrolls: int = 3,
                      floor: float = DEFAULT_SCORE_FLOOR) -> tuple[bool, str]:
    """Pass only if at least `min_scrolls` DISTINCT scrolls each score strictly above `floor`."""
    if min_scrolls < 2:
        raise ValueError("min_scrolls must be >= 2; a single-scroll result is never cross-scroll")
    grouped: dict[str, list[float]] = {}
    for sid, score in eval_scores_by_scroll.items():
        grouped.setdefault(_normalize_scroll(sid), []).append(float(score))
    best = {k: (float("nan") if any(v != v for v in vs) else min(vs)) for k, vs in grouped.items()}
    n_distinct = len(best)
    if n_distinct < min_scrolls:
        return False, f"only {n_distinct} distinct scroll(s) scored; need at least {min_scrolls}"
    passing = sorted(k for k, v in best.items() if v > floor)
    if len(passing) < min_scrolls:
        failing = sorted(k for k in best if k not in passing)
        return False, (f"only {len(passing)} of {n_distinct} scrolls above floor {floor}; "
                       f"need {min_scrolls}; at or below floor or NaN: {failing}")
    return True, f"{len(passing)} distinct scrolls above floor {floor}"



def provider_record() -> dict[str, Any]:
    return {
        "id": PROVIDER_ID,
        "lifecycle": LIFECYCLE,
        "status": LIFECYCLE,
        "is_ground_truth": False,
        "production_ready": False,
        "promotion_requirements": list(PROMOTION_REQUIREMENTS),
        "runnable_requirements": [
            "real held-out verso labels (human or official), never pseudo-labels",
            "teacher checkpoint with recorded provenance (teacher_id, training samples)",
            "GPU only for the teacher/student model; this control module is CPU-only",
        ],
        "notes": "pseudo-labels are never ground truth; recto and verso lineages are tracked separately",
    }
