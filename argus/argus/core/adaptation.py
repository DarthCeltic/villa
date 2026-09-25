"""Adaptation contracts: physical folds, out-of-fold exclusion and the worst-scroll gate."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

QUALIFY_AUC = 0.80
QUALIFY_CI_LOWER = 0.70


class AdaptationRung:
    """The declared order."""

    PREPROCESSING = "preprocessing"
    SOURCE_TRAINING = "source_training"
    TARGET_ADAPTATION = "target_adaptation"
    ENSEMBLE = "ensemble"

    ORDER = (PREPROCESSING, SOURCE_TRAINING, TARGET_ADAPTATION, ENSEMBLE)

    @classmethod
    def index(cls, rung: str) -> int:
        if rung not in cls.ORDER:
            raise ValueError("unknown rung %r; the ladder is declared, not ad hoc" % rung)
        return cls.ORDER.index(rung)


def _sha(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   default=str).encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class Region:
    """An axis-aligned physical region."""

    frame: str
    y0_um: float
    x0_um: float
    y1_um: float
    x1_um: float

    def __post_init__(self):
        if self.y1_um <= self.y0_um or self.x1_um <= self.x0_um:
            raise ValueError("empty or inverted region: %r" % (self,))
        if self.frame not in ("raw_volume", "mesh"):
            raise ValueError("frame must be raw_volume or mesh, got %r" % self.frame)

    def overlaps(self, other: "Region", buffer_um: float = 0.0) -> bool:
        """True when the regions intersect after growing `self` by the buffer."""
        if self.frame != other.frame:
            raise ValueError("cannot compare regions in different frames (%s vs %s); an "
                             "exclusion proved in one frame is not proved in the other"
                             % (self.frame, other.frame))
        return not (other.x0_um >= self.x1_um + buffer_um
                    or other.x1_um <= self.x0_um - buffer_um
                    or other.y0_um >= self.y1_um + buffer_um
                    or other.y1_um <= self.y0_um - buffer_um)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ScrollDomainCard:
    """What a deterministic profiler measured about one scroll."""

    scroll: str
    segment: str
    acquisition: dict
    voxel_stats: dict
    depth_structure: dict
    fiber_structure: dict
    geometry: dict
    render_quality: dict
    profiler_version: str
    inputs_sha256: str

    def fingerprint(self) -> str:
        """A stable digest of the MEASUREMENTS, deliberately excluding identity."""
        return _sha({"acquisition": self.acquisition, "voxel_stats": self.voxel_stats,
                     "depth_structure": self.depth_structure,
                     "fiber_structure": self.fiber_structure,
                     "geometry": self.geometry, "render_quality": self.render_quality,
                     "profiler_version": self.profiler_version})


@dataclasses.dataclass(frozen=True)
class Recipe:
    """A configuration that was qualified once, and the evidence that it was."""

    recipe_id: str
    representation: dict
    normalization: dict
    orientation: str
    training: dict
    rungs_used: tuple
    qualified_on: tuple
    qualification_evidence: dict
    domain_fingerprints: tuple

    def __post_init__(self):
        for r in self.rungs_used:
            AdaptationRung.index(r)
        if "qualify_auc" in self.training or "qualify_ci_lower" in self.training:
            raise ValueError("a recipe may not carry a qualification threshold; the bar is "
                             "fixed at AUC >= %.2f, CI lower >= %.2f and a threshold a "
                             "scroll can choose for itself is not a threshold"
                             % (QUALIFY_AUC, QUALIFY_CI_LOWER))

    def is_qualified(self) -> bool:
        return bool(self.qualified_on) and bool(self.qualification_evidence)


@dataclasses.dataclass(frozen=True)
class FoldAssignment:
    """Physical spatial K-fold, frozen and hashed BEFORE any target prediction is opened."""

    scroll: str
    segment: str
    k: int
    buffer_um: float
    folds: dict
    frame_coverage: tuple
    frozen_utc: str

    def __post_init__(self):
        if self.k < 2:
            raise ValueError("K-fold needs at least 2 folds, got %d" % self.k)
        if self.buffer_um <= 0:
            raise ValueError("a zero buffer certifies a memorised stroke read from the "
                             "other side of a fold boundary as generalization")
        frames = {r.frame for rs in self.folds.values() for r in rs}
        if not frames <= set(self.frame_coverage):
            raise ValueError("folds contain frames not declared in frame_coverage")
        for f in ("raw_volume", "mesh"):
            if f not in self.frame_coverage:
                raise ValueError("fold assignment must be expressed in BOTH raw_volume and "
                                 "mesh coordinates; %r is missing, and an exclusion proved "
                                 "in one frame is not proved in the other" % f)

    def sha256(self) -> str:
        return _sha({"scroll": self.scroll, "segment": self.segment, "k": self.k,
                     "buffer_um": self.buffer_um,
                     "folds": {str(i): [r.as_dict() for r in sorted(
                         rs, key=lambda z: (z.frame, z.y0_um, z.x0_um))]
                         for i, rs in sorted(self.folds.items())}})

    def regions_of(self, fold: int) -> tuple:
        return tuple(self.folds[fold])

    def regions_excluding(self, fold: int) -> tuple:
        return tuple(r for i, rs in self.folds.items() if i != fold for r in rs)


@dataclasses.dataclass(frozen=True)
class AdaptationRun:
    """One fitting episode, and -- crucially -- every region that shaped it."""

    run_id: str
    scroll: str
    segment: str
    recipe_id: str
    rung: str
    predicted_fold: int
    training_regions: tuple
    adaptation_regions: tuple
    pseudo_label_source_regions: tuple
    normalization_fit_regions: tuple
    fold_assignment_sha256: str
    checkpoint_sha256: str | None = None

    def __post_init__(self):
        AdaptationRung.index(self.rung)

    def fitting_inputs(self) -> tuple:
        """Everything that could have carried information into the model."""
        return tuple(self.training_regions) + tuple(self.adaptation_regions) + \
            tuple(self.pseudo_label_source_regions) + tuple(self.normalization_fit_regions)


@dataclasses.dataclass(frozen=True)
class OutOfFoldPrediction:
    """A prediction over one fold, by a model that never saw it."""

    scroll: str
    segment: str
    fold: int
    run_id: str
    region: Region
    prediction_sha256: str
    auc: float | None = None
    ap: float | None = None
    prevalence: float | None = None


@dataclasses.dataclass(frozen=True)
class Candidate:
    """A location proposed for reading, with the provenance that makes it checkable."""

    candidate_id: str
    scroll: str
    segment: str
    region: Region
    score: float
    out_of_fold: bool
    supporting_runs: tuple
    controls_passed: dict
    orientation: str
    exposure_state: str

    def __post_init__(self):
        if not self.orientation:
            raise ValueError("orientation is a mandatory Stage-0 field")
        if not self.out_of_fold:
            raise ValueError("a candidate must be out of fold; an in-fold score is a "
                             "training artefact wearing a candidate's name")



class ExclusionViolation(Exception):
    """Raised when a model would predict a region its own fitting touched."""


def verify_out_of_fold(run: AdaptationRun, folds: FoldAssignment) -> dict:
    """The whole guarantee, computed from coordinates."""
    if run.fold_assignment_sha256 != folds.sha256():
        raise ExclusionViolation(
            "run %s cites fold assignment %s but the assignment now hashes to %s; a fold map "
            "that changed after a run is not a fold map"
            % (run.run_id, run.fold_assignment_sha256[:12], folds.sha256()[:12]))
    target = folds.regions_of(run.predicted_fold)
    inputs = run.fitting_inputs()
    violations = []
    for t in target:
        for i in inputs:
            if t.frame != i.frame:
                continue
            if t.overlaps(i, buffer_um=folds.buffer_um):
                violations.append({"predicted_region": t.as_dict(),
                                   "fitting_input": i.as_dict(),
                                   "frame": t.frame,
                                   "buffer_um": folds.buffer_um})
    checked = {f: sum(1 for t in target for i in inputs
                      if t.frame == f and i.frame == f)
               for f in folds.frame_coverage}
    unchecked = [f for f, n in checked.items() if n == 0 and any(t.frame == f
                                                                 for t in target)]
    if unchecked:
        raise ExclusionViolation(
            "fold %d has regions in frame(s) %s with no fitting input expressed in the same "
            "frame, so exclusion was never actually tested there. An untested frame is not a "
            "clean one." % (run.predicted_fold, unchecked))
    return {"run_id": run.run_id, "predicted_fold": run.predicted_fold,
            "fold_assignment_sha256": folds.sha256(),
            "buffer_um": folds.buffer_um,
            "n_predicted_regions": len(target), "n_fitting_inputs": len(inputs),
            "comparisons_by_frame": checked,
            "violations": violations,
            "verdict": "OUT_OF_FOLD" if not violations else "EXCLUSION_VIOLATION",
            "basis": "actual coordinate overlap in microns, not filenames"}


def build_physical_folds(scroll: str, segment: str, *, extent_um: tuple, k: int,
                         buffer_um: float, mesh_extent_um: tuple | None = None,
                         frozen_utc: str) -> FoldAssignment:
    """Spatial stripes SEPARATED BY THE BUFFER, not a random partition and not contiguous."""
    y0, x0, y1, x1 = extent_um
    if y1 <= y0 or x1 <= x0:
        raise ValueError("empty extent %r" % (extent_um,))

    def stripes(a0, a1, b0, b1, along_x):
        span = a1 - a0
        w = (span - (k - 1) * buffer_um) / k
        if w <= 0:
            raise ValueError(
                "%d folds separated by a %.1f um buffer need more than the %.1f um available, "
                "leaving each fold %.1f um wide; use fewer folds or a smaller buffer, and do "
                "not shrink the buffer merely to fit K"
                % (k, buffer_um, span, w))
        out = {}
        for i in range(k):
            lo = a0 + i * (w + buffer_um)
            out[i] = (lo, lo + w)
        return out

    along_x = (x1 - x0) >= (y1 - y0)
    cuts = stripes(x0, x1, y0, y1, along_x) if along_x else stripes(y0, y1, x0, x1, along_x)
    mesh = mesh_extent_um or extent_um
    my0, mx0, my1, mx1 = mesh
    mcuts = (stripes(mx0, mx1, my0, my1, True) if along_x
             else stripes(my0, my1, mx0, mx1, False))

    folds = {}
    for i in range(k):
        lo, hi = cuts[i]
        mlo, mhi = mcuts[i]
        if along_x:
            folds[i] = (Region("raw_volume", y0, lo, y1, hi),
                        Region("mesh", my0, mlo, my1, mhi))
        else:
            folds[i] = (Region("raw_volume", lo, x0, hi, x1),
                        Region("mesh", mlo, mx0, mhi, mx1))
    return FoldAssignment(scroll=scroll, segment=segment, k=k, buffer_um=buffer_um,
                          folds=folds, frame_coverage=("raw_volume", "mesh"),
                          frozen_utc=frozen_utc)


def worst_scroll_gate(per_scroll: dict) -> dict:
    """Gate on the WORST scroll's out-of-fold result."""
    if not per_scroll:
        return {"verdict": "NO_EVIDENCE",
                "why": "a gate with nothing to gate is not a pass"}
    worst = min(per_scroll.items(), key=lambda kv: kv[1]["auc"])
    name, v = worst
    ok = v["auc"] >= QUALIFY_AUC and v["ci95"][0] >= QUALIFY_CI_LOWER
    return {"worst_scroll": name, "worst_auc": v["auc"], "worst_ci95": v["ci95"],
            "n_scrolls": len(per_scroll),
            "qualify_auc": QUALIFY_AUC, "qualify_ci_lower": QUALIFY_CI_LOWER,
            "verdict": "HUNT_QUALIFIED" if ok else "NOT_QUALIFIED",
            "basis": ("worst scroll out of fold; source-scroll fit and single good windows "
                      "are excluded by construction")}
