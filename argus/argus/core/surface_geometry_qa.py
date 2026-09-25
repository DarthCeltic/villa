"""Label-free surface QA: the result vocabulary, and ARGUS's own native checks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


PROVENANCE_NATIVE = "ARGUS_NATIVE"
PROVENANCE_UPSTREAM = "UPSTREAM_PINNED"
UPSTREAM_IDS = ("vc-segqa", "labelscope")
MEASURED = "MEASURED"
PASS = "PASS"
REFUSE = "REFUSE"
UNAVAILABLE = "UNAVAILABLE"
INSUFFICIENT_OVERLAP = "INSUFFICIENT_OVERLAP"
NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class GeometryQAResult:
    check: str
    status: str
    metrics: dict[str, Any]
    source: dict[str, Any]
    reason: str = ""
    provenance: str = PROVENANCE_NATIVE
    threshold_source: str = ""

    def as_dict(self) -> dict:
        return {
            "schema": "argus-surface-geometry-qa-v2",
            "check": self.check,
            "status": self.status,
            "provenance": self.provenance,
            "threshold_source": self.threshold_source,
            "metrics": self.metrics,
            "source": self.source,
            "reason": self.reason,
            "claim_boundary": "geometry QA does not establish ink, text or detector accuracy",
        }


def _native_source(source: dict | None, threshold_source: str) -> dict:
    src = dict(source or {})
    if str(src.get("provider", "")).lower() in UPSTREAM_IDS:
        raise ValueError("a native ARGUS check cannot be attributed to the upstream tool %r; run it through argus.core.upstream_qa_adapters" % src["provider"])
    if not str(threshold_source or "").strip():
        raise ValueError("a native check needs a cited threshold_source: there is no built-in default threshold")
    return src


def _native_result(threshold_source: str, check: str, status: str, metrics: dict, source: dict, reason: str = "") -> GeometryQAResult:
    return GeometryQAResult(check, status, metrics, source, reason, PROVENANCE_NATIVE, threshold_source)


def mask_escape(points: Iterable[tuple[int, int, int]], contains: Callable[[int, int, int], bool],
                *, max_escape_fraction: float, threshold_source: str, source: dict | None = None) -> GeometryQAResult:
    source = _native_source(source, threshold_source)
    pts = list(points)
    if not pts:
        return _native_result(threshold_source, "mask_escape", INSUFFICIENT_OVERLAP, {"n_points": 0},
                                source or {}, "no surface points")
    escaped = sum(1 for x, y, z in pts if not contains(x, y, z))
    fraction = escaped / len(pts)
    status = PASS if fraction <= max_escape_fraction else REFUSE
    return _native_result(threshold_source, "mask_escape", status,
                            {"n_points": len(pts), "escaped": escaped,
                             "escape_fraction": fraction, "threshold": max_escape_fraction},
                            source or {}, "surface leaves the volume mask" if status == REFUSE else "")


def adjacent_winding_gap(gaps_um: Iterable[float], *, min_gap_um: float,
                         max_gap_um: float, threshold_source: str, source: dict | None = None) -> GeometryQAResult:
    source = _native_source(source, threshold_source)
    gaps = list(gaps_um)
    if not gaps:
        return _native_result(threshold_source, "adjacent_winding_gap", INSUFFICIENT_OVERLAP, {"n_gaps": 0},
                                source or {}, "no adjacent winding pairs")
    bad = [g for g in gaps if g < min_gap_um or g > max_gap_um]
    status = PASS if not bad else REFUSE
    return _native_result(threshold_source, "adjacent_winding_gap", status,
                            {"n_gaps": len(gaps), "bad_gaps": len(bad),
                             "min_gap_um": min_gap_um, "max_gap_um": max_gap_um},
                            source or {}, "adjacent winding spacing is inconsistent" if bad else "")


def ridge_profile_support(profile: Iterable[float], *, support_threshold: float,
                          min_samples: int, threshold_source: str, source: dict | None = None) -> GeometryQAResult:
    source = _native_source(source, threshold_source)
    values = list(profile)
    if len(values) < min_samples:
        return _native_result(threshold_source, "ridge_profile_support", INSUFFICIENT_OVERLAP,
                                {"n_samples": len(values), "min_samples": min_samples},
                                source or {}, "profile is too short")
    support = sum(1 for value in values if value > 0) / len(values)
    status = PASS if support >= support_threshold else REFUSE
    return _native_result(threshold_source, "ridge_profile_support", status,
                            {"n_samples": len(values), "support_fraction": support,
                             "threshold": support_threshold}, source or {},
                            "ridge support is below threshold" if status == REFUSE else "")


def summarize(results: Iterable[GeometryQAResult]) -> dict:
    rows = [r.as_dict() for r in results]
    return {"schema": "argus-surface-geometry-qa-summary-v2", "checks": rows,
            "provenances": sorted({r["provenance"] for r in rows}),
            "geometry_only": True,
            "ink_established": False,
            "claim_boundary": "no combination of these checks proves ink"}
