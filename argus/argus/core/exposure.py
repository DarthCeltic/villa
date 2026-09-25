"""Three DIFFERENT things that all get loosely called \"exposure\"."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from argus.core.scroll_ids import resolve


class Channel(str, Enum):
    RAW_VOLUME_PRETRAINING = "raw_volume_pretraining"
    SUPERVISED_LABEL = "supervised_label"
    PSEUDO_LABEL = "pseudo_label"
    TEACHER_PREDICTION = "teacher_prediction"
    FINE_TUNING = "fine_tuning"


class State(str, Enum):
    EXPOSED = "EXPOSED"
    CLEAN = "CLEAN"
    UNKNOWN = "UNKNOWN"


class Eligibility(str, Enum):
    MODEL_HELDOUT_ELIGIBLE = "MODEL_HELDOUT_ELIGIBLE"
    MODEL_UNSEEN_OPERATOR_SEEN = "MODEL_UNSEEN_OPERATOR_SEEN"
    DEVELOPMENT_ONLY = "DEVELOPMENT_ONLY"
    INDETERMINATE = "INDETERMINATE"


@dataclass
class ChannelFinding:
    state: State
    evidence: str
    source: str = ""

    def __post_init__(self):
        if self.state is not State.UNKNOWN and not self.evidence.strip():
            raise ValueError("a CLEAN or EXPOSED channel must carry its evidence; an "
                             "unevidenced verdict is an UNKNOWN wearing a costume")


@dataclass
class ModelExposure:
    """Per-channel exposure of ONE scroll to ONE checkpoint's weights."""

    scroll: str
    model: str
    channels: dict = field(default_factory=dict)

    def __post_init__(self):
        self.scroll = resolve(self.scroll)
        for c in Channel:
            self.channels.setdefault(c, ChannelFinding(State.UNKNOWN, "", ""))

    @property
    def unknown_channels(self) -> list:
        return sorted(c.value for c, f in self.channels.items()
                      if f.state is State.UNKNOWN)

    @property
    def exposed_channels(self) -> list:
        return sorted(c.value for c, f in self.channels.items()
                      if f.state is State.EXPOSED)

    def eligibility(self, *, operator_seen: bool = False) -> Eligibility:
        if self.exposed_channels:
            return Eligibility.DEVELOPMENT_ONLY
        if self.unknown_channels:
            return Eligibility.INDETERMINATE
        return (Eligibility.MODEL_UNSEEN_OPERATOR_SEEN if operator_seen
                else Eligibility.MODEL_HELDOUT_ELIGIBLE)

    def may_be_called_fully_unseen(self) -> bool:
        """Only when every channel is checked AND clean."""
        return not self.exposed_channels and not self.unknown_channels


@dataclass
class OperatorExposure:
    """Results we have already inspected."""

    scroll: str
    seen: bool
    where: list = field(default_factory=list)
    note: str = ""

    def __post_init__(self):
        self.scroll = resolve(self.scroll)
        if self.seen and not self.where:
            raise ValueError("operator exposure must name where it happened, or it cannot "
                             "be audited later")


@dataclass
class AssetAvailability:
    """What exists locally."""

    scroll: str
    labels: bool = False
    label_format: str = ""
    raw_ct: bool = False
    raw_scale0_acquisition: str = ""
    mesh_or_tifxyz: bool = False
    coordinate_map: bool = False
    axis_order: str = ""
    physical_pitch_um: tuple | None = None
    coverage_mask: bool = False
    supervision_mask: bool = False
    notes: str = ""

    def __post_init__(self):
        self.scroll = resolve(self.scroll)

    def projection_state(self) -> str:
        """Can a labelled surface be mapped back into raw CT voxels?"""
        if not self.labels:
            return "NO_LABELS"
        if not self.raw_ct:
            return "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE"
        if not (self.mesh_or_tifxyz or self.coordinate_map):
            return "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE"
        if not self.physical_pitch_um or not self.axis_order:
            return "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE"
        return "RAW_CT_PAIR_CONSTRUCTIBLE"



import hashlib as _hashlib
import json as _json
import pathlib as _pathlib
import time as _time

TARGET_REGISTRY_CONTRACT = "argus-target-exposure-registry-v1"
TARGET_REGISTRY_FILE = ("target_exposure", "TARGET_EXPOSURE_REGISTRY.jsonl")


class TargetExposureKind(str, Enum):
    PRIOR_VOXEL_STATISTICS = "PRIOR_VOXEL_STATISTICS"
    PRIOR_VISUAL_INSPECTION = "PRIOR_VISUAL_INSPECTION"
    PRIOR_DETECTOR_OUTPUT = "PRIOR_DETECTOR_OUTPUT"
    OPERATOR_FENCE = "OPERATOR_FENCE"
    CLASSIFICATION = "CLASSIFICATION"


class TargetClassification(str, Enum):
    DEVELOPMENT_TARGET_ONLY = "DEVELOPMENT_TARGET_ONLY"


DEVELOPMENT_TARGET_ONLY_MAY = (
  "bounded mechanical integration experiments", "segmentation experiments",
  "rendering experiments", "with the contamination displayed on every surface")
DEVELOPMENT_TARGET_ONLY_NEVER = (
  "qualification", "unbiased discovery performance", "a prize claim", "fresh-target selection")

DISQUALIFYING_FOR_FRESH = tuple(k.value for k in TargetExposureKind)


class TargetRegistryRefusal(RuntimeError):
    pass


def _target_key(scroll) -> str:
    """Canonical scroll id where scroll_ids knows it; the official catalogue sample name otherwise."""
    try:
        return resolve(scroll)
    except KeyError:
        s = str(scroll or "").strip()
        if not s:
            raise TargetRegistryRefusal("a target exposure record needs a scroll") from None
        return s


def _same_target(a, b) -> bool:
    import re as _re
    k = lambda s: _re.sub(r"[\s_\-.:/]+", "", str(s or "")).upper()
    return k(a) == k(b)


def target_registry_path() -> _pathlib.Path:
    from argus.core import paths
    return paths.artifact_write_root().joinpath(*TARGET_REGISTRY_FILE)


def _line_sha(body: dict) -> str:
    return _hashlib.sha256(_json.dumps(body, sort_keys=True, separators=(",", ":"))
                           .encode("utf-8")).hexdigest()


def load_target_records(path=None) -> list:
    """Every record, chain-verified."""
    p = _pathlib.Path(path) if path is not None else target_registry_path()
    if not p.is_file():
        return []
    out, prev = [], None
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rec = _json.loads(line)
        body = {k: v for k, v in rec.items() if k != "record_sha256"}
        if rec.get("prev_sha256") != prev or _line_sha(body) != rec.get("record_sha256"):
            raise TargetRegistryRefusal("target exposure registry %s chain breaks at line %d" % (p, i))
        prev = rec["record_sha256"]
        out.append(rec)
    return out


def append_target_record(*, scroll: str, volume_id: str | None, kind, evidence: list,
                         note: str, recorded_by: str, classification=None, path=None,
                         supersedes: str | None = None) -> dict:
    """Append one record."""
    kind = TargetExposureKind(kind)
    if not evidence or not all(isinstance(e, dict) and e.get("path") for e in evidence):
        raise TargetRegistryRefusal("a target exposure record must cite evidence paths")
    if kind is TargetExposureKind.CLASSIFICATION and classification is None:
        raise TargetRegistryRefusal("a CLASSIFICATION record must name its classification")
    p = _pathlib.Path(path) if path is not None else target_registry_path()
    prior = load_target_records(p)
    body = {"contract": TARGET_REGISTRY_CONTRACT, "seq": len(prior) + 1,
            "scroll": _target_key(scroll), "volume_id": volume_id, "kind": kind.value,
            "classification": TargetClassification(classification).value if classification
            else None, "evidence": evidence, "note": note, "recorded_by": recorded_by,
            "supersedes": supersedes,
            "recorded_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "prev_sha256": prior[-1]["record_sha256"] if prior else None}
    if body["classification"] == TargetClassification.DEVELOPMENT_TARGET_ONLY.value:
        body["may"] = list(DEVELOPMENT_TARGET_ONLY_MAY)
        body["never"] = list(DEVELOPMENT_TARGET_ONLY_NEVER)
    body["record_sha256"] = _line_sha(body)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(_json.dumps(body, sort_keys=True) + "\n")
        fh.flush()
        import os as _os
        _os.fsync(fh.fileno())
    return body


def target_exposure(scroll: str, records=None, *, path=None) -> dict:
    """What every surface shows for one target: its classification and every exposure kind."""
    recs = records if records is not None else load_target_records(path)
    key = _target_key(scroll)
    mine = [r for r in recs if _same_target(r.get("scroll"), key)]
    classes = sorted({r["classification"] for r in mine if r.get("classification")})
    kinds = sorted({r["kind"] for r in mine})
    return {"scroll": key, "classifications": classes, "kinds": kinds,
            "fresh": not any(k in DISQUALIFYING_FOR_FRESH for k in kinds),
            "contamination_banner": ("%s: %s" % (key, ", ".join(classes + [k for k in kinds
                                                                       if k != "CLASSIFICATION"]))
                                     if mine else None),
            "records": [{k: r.get(k) for k in ("seq", "kind", "classification", "volume_id",
                                               "note", "record_sha256")} for r in mine]}


def usable_as_model_heldout(m: ModelExposure, a: AssetAvailability,
                            o: OperatorExposure | None = None) -> tuple:
    """All three concepts, combined ONCE, at the point of decision -- never before."""
    elig = m.eligibility(operator_seen=bool(o and o.seen))
    proj = a.projection_state()
    if elig is Eligibility.DEVELOPMENT_ONLY:
        return False, "model-exposed on %s" % ", ".join(m.exposed_channels)
    if elig is Eligibility.INDETERMINATE:
        return False, "unverified channels: %s" % ", ".join(m.unknown_channels)
    if proj != "RAW_CT_PAIR_CONSTRUCTIBLE":
        return False, proj
    return True, elig.value
