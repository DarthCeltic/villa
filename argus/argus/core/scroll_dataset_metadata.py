"""Versioned physical-fact contract for one scroll's dataset metadata."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import time

CONTRACT = "argus-scroll-dataset-metadata-v1"

AUTHORITY_STATES = (
    "PUBLISHED_AUTHORITY",
    "TEAM_ACCEPTED",
    "MEASURED_REPRODUCED",
    "COMMUNITY_ESTIMATE",
    "UNKNOWN",
)

ADMISSIBLE_AUTHORITIES = ("PUBLISHED_AUTHORITY", "TEAM_ACCEPTED", "MEASURED_REPRODUCED")


class MetadataRefusal(Exception):
    """Raised when a caller asks this module to treat a fact as more certain than its own recorded authority allows -- never raised merely because a fact is absent."""


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


@dataclasses.dataclass(frozen=True)
class EvidenceRef:
    """Where a fact came from, and how sure anyone is."""
    authority: str
    source: str
    method: str = ""
    stated_uncertainty: str | None = None
    recorded_utc: str = dataclasses.field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def __post_init__(self):
        if self.authority not in AUTHORITY_STATES:
            raise MetadataRefusal(
                "unknown authority %r; must be one of %s" % (self.authority, AUTHORITY_STATES))

    @property
    def is_admissible(self) -> bool:
        return self.authority in ADMISSIBLE_AUTHORITIES



SOURCE_AUTHORITY_STATES = (
    "PUBLISHER_DECLARED",
    "CORE_TEAM_DECLARED",
    "COMMUNITY_REPORTED",
    "ARGUS_DERIVED",
    "UNKNOWN",
)

OPERATIONAL_VERIFICATION_STATES = (
    "REPRODUCED",
    "CONTROL_PASSED",
    "CONTRADICTED",
    "UNTESTED",
    "UNRESOLVED",
)

PERMITTED_USE_STATES = (
    "DISPLAY_ONLY",
    "EXPLORATORY_FITTING",
    "SCIENTIFIC_EVIDENCE",
    "UPSTREAM_PROPOSAL",
    "CANONICAL_DATASET_DEFAULT",
)

_CONTRADICTED_PERMITS_ONLY = frozenset({"DISPLAY_ONLY"})

_STRONG_SOURCES = frozenset({"PUBLISHER_DECLARED", "CORE_TEAM_DECLARED", "ARGUS_DERIVED"})
_STRONG_VERIFICATION = frozenset({"REPRODUCED", "CONTROL_PASSED"})


@dataclasses.dataclass(frozen=True)
class EvidenceRefV2:
    """Three independent axes replacing v1's single `authority` field."""
    source_authority: str
    operational_verification: str
    source: str
    method: str = ""
    stated_uncertainty: str | None = None
    recorded_utc: str = dataclasses.field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def __post_init__(self):
        if self.source_authority not in SOURCE_AUTHORITY_STATES:
            raise MetadataRefusal("unknown source_authority %r; must be one of %s"
                                  % (self.source_authority, SOURCE_AUTHORITY_STATES))
        if self.operational_verification not in OPERATIONAL_VERIFICATION_STATES:
            raise MetadataRefusal("unknown operational_verification %r; must be one of %s"
                                  % (self.operational_verification,
                                     OPERATIONAL_VERIFICATION_STATES))

    def permits(self, use: str) -> bool:
        """Whether this evidence, as recorded, may be used for `use`."""
        if use not in PERMITTED_USE_STATES:
            raise MetadataRefusal("unknown permitted_use %r; must be one of %s"
                                  % (use, PERMITTED_USE_STATES))
        if self.operational_verification == "CONTRADICTED":
            return use in _CONTRADICTED_PERMITS_ONLY
        if use == "DISPLAY_ONLY":
            return True
        if use == "EXPLORATORY_FITTING":
            return self.operational_verification != "UNRESOLVED"
        if use in ("SCIENTIFIC_EVIDENCE", "UPSTREAM_PROPOSAL"):
            return self.operational_verification in _STRONG_VERIFICATION
        if use == "CANONICAL_DATASET_DEFAULT":
            return (self.source_authority in _STRONG_SOURCES
                    and self.operational_verification in _STRONG_VERIFICATION)
        return False


def upgrade_v1_to_v2(v1: EvidenceRef) -> EvidenceRefV2:
    """The explicit v1->v2 interpretation adapter."""
    source_map = {
        "PUBLISHED_AUTHORITY": "PUBLISHER_DECLARED",
        "TEAM_ACCEPTED": "CORE_TEAM_DECLARED",
        "MEASURED_REPRODUCED": "ARGUS_DERIVED",
        "COMMUNITY_ESTIMATE": "COMMUNITY_REPORTED",
        "UNKNOWN": "UNKNOWN",
    }
    verification_map = {
        "PUBLISHED_AUTHORITY": "UNTESTED",
        "TEAM_ACCEPTED": "UNTESTED",
        "MEASURED_REPRODUCED": "REPRODUCED",
        "COMMUNITY_ESTIMATE": "UNTESTED",
        "UNKNOWN": "UNRESOLVED",
    }
    return EvidenceRefV2(
        source_authority=source_map[v1.authority],
        operational_verification=verification_map[v1.authority],
        source=v1.source, method=v1.method, stated_uncertainty=v1.stated_uncertainty,
        recorded_utc=v1.recorded_utc)


def require_permitted(evidence: EvidenceRef | EvidenceRefV2 | None, *, use: str,
                      purpose: str) -> EvidenceRefV2:
    """The v2 gate."""
    if evidence is None:
        raise MetadataRefusal("no evidence at all is recorded for %r" % purpose)
    v2 = evidence if isinstance(evidence, EvidenceRefV2) else upgrade_v1_to_v2(evidence)
    if not v2.permits(use):
        raise MetadataRefusal(
            "%r may not be used as %s: source_authority=%s, operational_verification=%s "
            "(source: %s). A reproduced community estimate may support SCIENTIFIC_EVIDENCE "
            "or UPSTREAM_PROPOSAL, but not CANONICAL_DATASET_DEFAULT or a publisher fact."
            % (purpose, use, v2.source_authority, v2.operational_verification, v2.source))
    return v2


@dataclasses.dataclass(frozen=True)
class RejectedUpstreamPlacement:
    """Boundary B rule 4, made a first-class record rather than a comment."""
    upstream_reference: str
    proposed_placement: str
    rejection_reason: str
    requested_destination: str
    reviewer: str = ""


@dataclasses.dataclass(frozen=True)
class UmbilicusEstimate:
    control_points: tuple[tuple[float, float, float], ...]
    coordinate_frame: str
    evidence: EvidenceRef
    rejected_placement: RejectedUpstreamPlacement | None = None


@dataclasses.dataclass(frozen=True)
class WindingCountEstimate:
    count: float
    validated_z_band: tuple[float, float] | None
    derivation_method: str
    evidence: EvidenceRef
    rejected_placement: RejectedUpstreamPlacement | None = None


@dataclasses.dataclass(frozen=True)
class CanonicalIdentity:
    """Boundary 3: exact identity, not a name-prefix heuristic."""
    scroll_id: str
    volume_id: str
    source_url: str
    array_path: str
    catalog_identity: str
    shape: tuple[int, ...]
    chunks: tuple[int, ...]
    dtype: str
    pyramid_level: int
    voxel_pitch_um: float
    energy_kev: float
    coordinate_transform: dict
    manifest_sha256: str


@dataclasses.dataclass(frozen=True)
class ScrollDatasetMetadata:
    """One versioned record."""
    schema: str
    scroll_id: str
    volume_id: str
    acquisition: dict
    coordinate_frame: dict
    valid_spatial_bounds: dict
    z_band: tuple[float, float] | None
    spiral_outward_sense: str | None
    spiral_outward_sense_evidence: EvidenceRef | None
    winding_count: WindingCountEstimate | None
    umbilicus: UmbilicusEstimate | None
    tracks: tuple[dict, ...] = ()
    normal_field_ids: tuple[str, ...] = ()
    fiber_map_ids: tuple[str, ...] = ()
    surface_ids: tuple[str, ...] = ()
    provenance_hashes: dict = dataclasses.field(default_factory=dict)
    identity: CanonicalIdentity | None = None

    def __post_init__(self):
        if self.schema != CONTRACT:
            raise MetadataRefusal("unrecognised schema %r, expected %r" % (self.schema, CONTRACT))

    @property
    def record_sha256(self) -> str:
        return _sha(dataclasses.asdict(self))


def new_record(*, scroll_id: str, volume_id: str, acquisition: dict, coordinate_frame: dict,
               valid_spatial_bounds: dict, z_band: tuple[float, float] | None = None,
               spiral_outward_sense: str | None = None,
               spiral_outward_sense_evidence: EvidenceRef | None = None,
               winding_count: WindingCountEstimate | None = None,
               umbilicus: UmbilicusEstimate | None = None,
               tracks: tuple[dict, ...] = (), normal_field_ids: tuple[str, ...] = (),
               fiber_map_ids: tuple[str, ...] = (), surface_ids: tuple[str, ...] = (),
               provenance_hashes: dict | None = None,
               identity: CanonicalIdentity | None = None) -> ScrollDatasetMetadata:
    return ScrollDatasetMetadata(
        schema=CONTRACT, scroll_id=scroll_id, volume_id=volume_id, acquisition=acquisition,
        coordinate_frame=coordinate_frame, valid_spatial_bounds=valid_spatial_bounds,
        z_band=z_band, spiral_outward_sense=spiral_outward_sense,
        spiral_outward_sense_evidence=spiral_outward_sense_evidence,
        winding_count=winding_count, umbilicus=umbilicus, tracks=tracks,
        normal_field_ids=normal_field_ids, fiber_map_ids=fiber_map_ids, surface_ids=surface_ids,
        provenance_hashes=provenance_hashes or {}, identity=identity)


SENTINEL_COORDINATE = -1.0


def validate_record(metadata: ScrollDatasetMetadata) -> list[str]:
    """Boundary C's mechanical checks."""
    problems: list[str] = []

    if metadata.identity is not None:
        idn = metadata.identity
        if idn.scroll_id != metadata.scroll_id:
            problems.append(
                "identity.scroll_id %r does not exactly equal the record's scroll_id %r"
                % (idn.scroll_id, metadata.scroll_id))
        if idn.volume_id != metadata.volume_id:
            problems.append(
                "identity.volume_id %r does not exactly equal the record's volume_id %r"
                % (idn.volume_id, metadata.volume_id))
        if len(idn.manifest_sha256) != 64:
            problems.append(
                "identity.manifest_sha256 %r is not a 64-character hash" % idn.manifest_sha256)
    else:
        if not metadata.volume_id.startswith(metadata.scroll_id):
            problems.append(
                "volume_id %r does not start with scroll_id %r -- wrong volume for this "
                "scroll (weak check: no CanonicalIdentity recorded on this record yet)"
                % (metadata.volume_id, metadata.scroll_id))

    if not metadata.coordinate_frame or not metadata.coordinate_frame.get("name"):
        problems.append("coordinate_frame has no declared name -- wrong or missing frame")

    bounds = metadata.valid_spatial_bounds
    for axis in ("z", "y", "x"):
        if axis not in bounds:
            problems.append("valid_spatial_bounds is missing axis %r" % axis)

    def _in_bounds(pt: tuple[float, float, float]) -> bool:
        for axis, v in zip(("z", "y", "x"), pt):
            lo_hi = bounds.get(axis)
            if lo_hi is None:
                continue
            lo, hi = lo_hi
            if not (lo <= v <= hi):
                return False
        return True

    if metadata.umbilicus is not None:
        pts = metadata.umbilicus.control_points
        for pt in pts:
            if any(abs(c - SENTINEL_COORDINATE) < 1e-9 for c in pt):
                problems.append(
                    "umbilicus control point %r is the sentinel coordinate -- a placeholder, "
                    "not a real measurement" % (pt,))
            elif not _in_bounds(pt):
                problems.append(
                    "umbilicus control point %r lies outside valid_spatial_bounds" % (pt,))
        z_values = [pt[0] for pt in pts]
        if len(z_values) >= 2 and len(set(z_values)) != len(z_values):
            problems.append("umbilicus control points repeat a z value -- not monotonic")
        if len(z_values) >= 2 and z_values != sorted(z_values) and z_values != sorted(
                z_values, reverse=True):
            problems.append("umbilicus control points are not monotonic in z")

    if metadata.winding_count is not None:
        wc = metadata.winding_count
        if wc.count <= 0:
            problems.append("winding_count.count %r is not a positive winding count" % wc.count)
        if wc.validated_z_band is not None and metadata.z_band is not None:
            vlo, vhi = wc.validated_z_band
            zlo, zhi = metadata.z_band
            if vlo < zlo or vhi > zhi:
                problems.append(
                    "winding_count.validated_z_band %r extends outside the record's own "
                    "z_band %r" % (wc.validated_z_band, metadata.z_band))

    return problems


def validate_identity_against_store(identity: CanonicalIdentity, *, actual_shape: tuple,
                                    actual_chunks: tuple, actual_dtype: str) -> list[str]:
    """Boundary 3: check the catalog's OWN say-so (shape/chunks/dtype) against a REAL probed store -- reuses whatever the caller already probed (e.g."""
    problems: list[str] = []
    if tuple(identity.shape) != tuple(actual_shape):
        problems.append(
            "catalog shape %r does not match the actual store shape %r"
            % (identity.shape, actual_shape))
    if tuple(identity.chunks) != tuple(actual_chunks):
        problems.append(
            "catalog chunks %r does not match the actual store chunks %r"
            % (identity.chunks, actual_chunks))
    if identity.dtype != actual_dtype:
        problems.append(
            "catalog dtype %r does not match the actual store dtype %r"
            % (identity.dtype, actual_dtype))
    return problems


def identity_preflight_against_probe(scroll_id: str, probe: dict) -> dict:
    """Boundary 2, renderer coordinate preflight -- ADVISORY, NOT A GATE, for the same reason launch authorization's scroll_dataset_metadata check is advisory: almost no scroll has a saved record yet, so..."""
    if not scroll_id:
        return {"present": False, "level_matched": None, "problems": []}
    try:
        rec = load_scroll_metadata(scroll_id)
    except MetadataRefusal as exc:
        return {"present": False, "level_matched": None, "problems": ["refused: %s" % exc]}
    if rec is None or rec.identity is None:
        return {"present": False, "level_matched": None, "problems": []}
    ident = rec.identity
    level = str(ident.pyramid_level)
    entry = next((lv for lv in probe.get("levels", []) if lv.get("level") == level), None)
    if entry is None or not entry.get("openable"):
        return {"present": True, "level_matched": None,
                "problems": ["declared pyramid_level %r is not an openable level in this "
                            "probe (openable levels: %s)"
                            % (level, [lv["level"] for lv in probe.get("levels", [])
                                       if lv.get("openable")])]}
    problems = validate_identity_against_store(
        ident, actual_shape=tuple(entry["shape"]), actual_chunks=tuple(entry["chunks"]),
        actual_dtype=entry["dtype"])
    return {"present": True, "level_matched": level, "problems": problems}


def require_admissible(evidence: EvidenceRef | None, *, purpose: str) -> EvidenceRef:
    """THE one gate."""
    if evidence is None:
        raise MetadataRefusal("no evidence at all is recorded for %r" % purpose)
    if not evidence.is_admissible:
        raise MetadataRefusal(
            "%r is backed by %s evidence (%s), which may support exploratory fitting but may "
            "not become an authoritative default, an admissible scientific claim, or a "
            "certification badge. Source: %s"
            % (purpose, evidence.authority, evidence.method or "no method recorded",
               evidence.source))
    return evidence


def derive_exploratory_preset(metadata: ScrollDatasetMetadata) -> dict:
    """Boundary B/C: an ARGUS exploratory runtime preset, generated FROM validated metadata, never itself canonical."""
    def _evi(e: EvidenceRef | None) -> dict | None:
        return dataclasses.asdict(e) if e is not None else None

    return {
        "contract": "argus-exploratory-runtime-preset-v1",
        "derived_from": metadata.record_sha256,
        "scroll_id": metadata.scroll_id,
        "volume_id": metadata.volume_id,
        "for_exploratory_fitting_only": True,
        "never_a_certified_result": True,
        "winding_count": (
            {"count": metadata.winding_count.count,
             "validated_z_band": metadata.winding_count.validated_z_band,
             "authority": metadata.winding_count.evidence.authority,
             "evidence": _evi(metadata.winding_count.evidence)}
            if metadata.winding_count else None),
        "umbilicus": (
            {"control_points": metadata.umbilicus.control_points,
             "coordinate_frame": metadata.umbilicus.coordinate_frame,
             "authority": metadata.umbilicus.evidence.authority,
             "evidence": _evi(metadata.umbilicus.evidence)}
            if metadata.umbilicus else None),
        "spiral_outward_sense": metadata.spiral_outward_sense,
        "spiral_outward_sense_authority": (
            metadata.spiral_outward_sense_evidence.authority
            if metadata.spiral_outward_sense_evidence else "UNKNOWN"),
    }


def example_phe1745_evidence() -> EvidenceRefV2:
    """The v2 evidence half of the worked example below, exposed separately so a caller can check `.permits(...)` directly without reconstructing the whole record."""
    return EvidenceRefV2(
        source_authority="COMMUNITY_REPORTED",
        operational_verification="CONTRADICTED",
        source="ScrollPrize/villa#1745 (estimate), #1736 (method contradicted same-day)",
        method="radial sheet counts confirmed by 30k-step tracks-only fits rendering "
               "continuous sheets",
        stated_uncertainty=(
            "validated only for the fitted z-band listed, not the whole scroll; on #1736, "
            "Henderson separately noted umbilicus centers can sit far from the whole-"
            "papyrus centroid, and that sheets are often not cleanly separated in "
            "compressed regions"))


def example_phe1745_record() -> ScrollDatasetMetadata:
    """A worked example built from a public upstream issue (ScrollPrize/villa#1745)."""
    rejection = RejectedUpstreamPlacement(
        upstream_reference="ScrollPrize/villa#1745",
        proposed_placement="spiral-fitting/configs/scrolls/ (villa's algorithm config tree)",
        rejection_reason=(
            "Paul Henderson, closing #1745 (2026-09-14): the estimates may be useful if "
            "correct, but committing them into villa's algorithm config is the wrong "
            "location -- the same category problem villa's own existing Paris 4 defaults "
            "already have"),
        requested_destination=(
            "Paul Henderson, #1745: the dataset-side spiral-scroll.json, alongside the data "
            "itself rather than in algorithm config"),
        reviewer="Paul Henderson (pmh47)")

    v2_evidence = example_phe1745_evidence()

    winding_count = WindingCountEstimate(
        count=90, validated_z_band=None,
        derivation_method=v2_evidence.method,
        evidence=EvidenceRef(authority="COMMUNITY_ESTIMATE",
                             source="ScrollPrize/villa#1745",
                             stated_uncertainty=v2_evidence.stated_uncertainty),
        rejected_placement=rejection)

    rec = new_record(
        scroll_id="PHerc0125",
        volume_id="PHerc0125/spiral_m7",
        acquisition={"source": "dl.ash2txt.org (via ScrollPrize/villa#1745)"},
        coordinate_frame={"name": "villa-tracks-only-fit"},
        valid_spatial_bounds={"z": (0, 1), "y": (0, 1), "x": (0, 1)},
        winding_count=winding_count)
    return rec



def _record_path(scroll_id: str, *, root: pathlib.Path | None = None) -> pathlib.Path:
    if root is None:
        from argus.core import paths as _paths
        root = _paths.artifact_write_root() / "scroll_dataset_metadata"
    from argus.core.safe_names import safe_name
    return pathlib.Path(root) / ("%s.json" % safe_name(scroll_id, "scroll id"))


def _dataclass_to_dict(obj):
    if obj is None:
        return None
    if dataclasses.is_dataclass(obj):
        return {k: _dataclass_to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, tuple):
        return [_dataclass_to_dict(v) for v in obj]
    return obj


def save_record(metadata: ScrollDatasetMetadata, *, root: pathlib.Path | None = None) -> pathlib.Path:
    """Write a record to the one place `load_scroll_metadata` will look."""
    problems = validate_record(metadata)
    if problems:
        raise MetadataRefusal(
            "refusing to save %s: it does not pass its own validate_record(): %s"
            % (metadata.scroll_id, problems))
    p = _record_path(metadata.scroll_id, root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_dataclass_to_dict(metadata), indent=1, default=str) + "\n",
                encoding="utf-8")
    return p


def load_scroll_metadata(scroll_id: str, *, root=None) -> ScrollDatasetMetadata | None:
    """THE canonical loader."""
    p = _record_path(scroll_id, root=root)
    if not p.is_file():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    if raw.get("schema") != CONTRACT:
        raise MetadataRefusal(
            "%s carries schema %r, not %r -- refusing to load a record this loader cannot "
            "interpret rather than guessing its shape" % (p, raw.get("schema"), CONTRACT))

    def _evi(d):
        return EvidenceRef(**d) if d else None

    def _rejection(d):
        return RejectedUpstreamPlacement(**d) if d else None

    wc = raw.get("winding_count")
    winding_count = (WindingCountEstimate(
        count=wc["count"], validated_z_band=tuple(wc["validated_z_band"]) if wc.get(
            "validated_z_band") else None, derivation_method=wc["derivation_method"],
        evidence=_evi(wc["evidence"]), rejected_placement=_rejection(wc.get("rejected_placement")))
        if wc else None)

    umb = raw.get("umbilicus")
    umbilicus = (UmbilicusEstimate(
        control_points=tuple(tuple(p) for p in umb["control_points"]),
        coordinate_frame=umb["coordinate_frame"], evidence=_evi(umb["evidence"]),
        rejected_placement=_rejection(umb.get("rejected_placement")))
        if umb else None)

    idn = raw.get("identity")
    identity = (CanonicalIdentity(**{**idn, "shape": tuple(idn["shape"]),
                                     "chunks": tuple(idn["chunks"])}) if idn else None)

    rec = ScrollDatasetMetadata(
        schema=raw["schema"], scroll_id=raw["scroll_id"], volume_id=raw["volume_id"],
        acquisition=raw["acquisition"], coordinate_frame=raw["coordinate_frame"],
        valid_spatial_bounds=raw["valid_spatial_bounds"],
        z_band=tuple(raw["z_band"]) if raw.get("z_band") else None,
        spiral_outward_sense=raw.get("spiral_outward_sense"),
        spiral_outward_sense_evidence=_evi(raw.get("spiral_outward_sense_evidence")),
        winding_count=winding_count, umbilicus=umbilicus,
        tracks=tuple(raw.get("tracks", ())), normal_field_ids=tuple(raw.get("normal_field_ids", ())),
        fiber_map_ids=tuple(raw.get("fiber_map_ids", ())), surface_ids=tuple(raw.get("surface_ids", ())),
        provenance_hashes=raw.get("provenance_hashes", {}), identity=identity)

    problems = validate_record(rec)
    if problems:
        raise MetadataRefusal(
            "refusing to load %s: it no longer passes validate_record() -- %s"
            % (scroll_id, problems))
    return rec


def require_loaded_metadata(scroll_id: str, *, root=None) -> ScrollDatasetMetadata:
    """The bypass-refusing gate: a consumer that calls THIS, not `load_scroll_metadata` directly, cannot proceed on a missing record by accident -- there is no silent None to forget to check."""
    rec = load_scroll_metadata(scroll_id, root=root)
    if rec is None:
        raise MetadataRefusal(
            "no ScrollDatasetMetadata record exists for scroll_id=%r -- a consumer may not "
            "substitute a plain dict, a hard-coded legacy preset, or an invented default here"
            % scroll_id)
    return rec
