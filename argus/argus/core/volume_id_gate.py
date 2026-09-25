"""The EXACT official volume ID, verified at three points: acquisition, inference, package export."""
from __future__ import annotations

import json
import pathlib

from argus.core import official_identity as OI
from argus.core import paths

CONTRACT = "argus-volume-id-gate-v1"

CHECKPOINTS = ("ACQUISITION", "INFERENCE", "PACKAGE_EXPORT")

VERIFIED = "VERIFIED"
MISMATCH = "MISMATCH"
UNKNOWN = "UNKNOWN"
SUPERSEDED = "SUPERSEDED"


class VolumeIdRefusal(RuntimeError):
    def __init__(self, message, verdict=None):
        super().__init__(message)
        self.verdict = verdict


def _key(s) -> str:
    return str(s or "").strip().upper().replace("_", "").replace("-", "")


parse_volume_id_from_url = OI.parse_any_volume_token


def official_registry(resolutions) -> tuple:
    """(registry, registry_source) holding ONLY PROVEN_BY_OFFICIAL_CATALOG_RESOLUTION identities."""
    return OI.registry_from_resolutions(resolutions), OI.REGISTRY_SOURCE


def default_registry() -> dict:
    """{scroll_key: {\"scroll\", \"official\": set, \"superseded\": set, \"sources\": [...]}}."""
    reg = {}

    def row(scroll):
        return reg.setdefault(_key(scroll), {"scroll": scroll, "official": set(),
                                             "superseded": set(), "sources": [], "scans": set()})
    survey = OI.load_survey()
    for scroll_name, s in ((survey or {}).get("samples") or {}).items():
        for sid in s.get("scans") or {}:
            row(scroll_name)["scans"].add(sid)
        for vid in s.get("volumes") or {}:
            e = row(scroll_name)
            e["official"].add(vid)
        if s.get("volumes"):
            row(scroll_name)["sources"].append("public survey %s" % survey.get("checked_at"))
    p = paths.find_artifact("acquisition_survey", "ACQUISITION_SURVEY.json")
    try:
        doc = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        for r in doc.get("rows") or []:
            si = r.get("scan_identity") or {}
            title = r.get("physical_title")
            if not (title and si.get("scan_id")):
                continue
            e = row(title)
            e["official"].add(str(si["scan_id"]))
            if si.get("v1_scan_id") and si.get("v1_still_listed") is False                     and str(si["v1_scan_id"]) not in e["scans"]:
                e["superseded"].add(str(si["v1_scan_id"]))
            e["sources"].append("acquisition survey %s" % p)
    except (OSError, ValueError):
        pass
    p2 = paths.find_artifact("volume_standings", "VOLUMES.json")
    try:
        doc2 = json.loads(pathlib.Path(p2).read_text(encoding="utf-8"))
        for v in doc2.get("volumes") or []:
            if v.get("scroll") and v.get("volume_id"):
                e = row(v["scroll"])
                e["official"].add(str(v["volume_id"]))
                e["sources"].append("volume standings %s" % p2)
    except (OSError, ValueError):
        pass
    return reg


def verify(checkpoint: str, *, scroll, declared_volume_id, observed_volume_id,
           registry=None, registry_source: str | None = None) -> dict:
    """One checkpoint's verdict."""
    reasons = []
    if checkpoint not in CHECKPOINTS:
        reasons.append("unknown checkpoint %r" % checkpoint)
    reg = registry if registry is not None else default_registry()
    src = registry_source or ("default: public survey + volume standings" if registry is None else "CALLER_SUPPLIED")
    entry = reg.get(_key(scroll)) if scroll else None
    d, o = (str(declared_volume_id or "").strip(), str(observed_volume_id or "").strip())
    state = VERIFIED
    if not scroll:
        reasons.append("UNKNOWN: no scroll declared")
        state = UNKNOWN
    if not d:
        reasons.append("UNKNOWN: no declared volume id")
        state = UNKNOWN
    if not o:
        reasons.append("UNKNOWN: the %s artefact yields no volume id" % checkpoint)
        state = UNKNOWN
    if scroll and entry is None:
        reasons.append("UNKNOWN: %s has no official volume record in %s" % (scroll, src))
        state = UNKNOWN
    if entry is not None and d:
        if d in entry["superseded"]:
            reasons.append("SUPERSEDED: %s is a v1 id no longer listed for %s; official is %s"
                           % (d, scroll, sorted(entry["official"])))
            state = SUPERSEDED
        elif d not in entry["official"] and d in entry.get("scans", ()):
            reasons.append("UNKNOWN: %s is an acquisition (scan) id of %s, not a volume id; its "
                           "official volumes are %s" % (d, scroll, sorted(entry["official"])))
            state = UNKNOWN
        elif d not in entry["official"]:
            reasons.append("UNKNOWN: %s is not an official volume id for %s (official: %s)"
                           % (d, scroll, sorted(entry["official"])))
            state = UNKNOWN
    if d and o and d != o:
        reasons.append("MISMATCH: declared %s, the %s artefact names %s" % (d, checkpoint, o))
        state = MISMATCH
    return {"contract": CONTRACT, "checkpoint": checkpoint, "scroll": scroll,
            "declared_volume_id": d or None, "observed_volume_id": o or None,
            "state": state if not reasons else (state if state != VERIFIED else UNKNOWN),
            "verified": not reasons, "reasons": reasons, "registry_source": src,
            "official_for_scroll": sorted(entry["official"]) if entry else []}


def _require(v: dict) -> dict:
    if not v["verified"]:
        raise VolumeIdRefusal("volume id %s at %s for %s: %s"
                              % (v["state"], v["checkpoint"], v["scroll"], "; ".join(v["reasons"])),
                              verdict=v)
    return v


def before_acquisition(*, scroll, declared_volume_id, source_url, registry=None,
                       registry_source=None) -> dict:
    """Point 1."""
    return _require(verify("ACQUISITION", scroll=scroll, declared_volume_id=declared_volume_id,
                           observed_volume_id=parse_volume_id_from_url(source_url),
                           registry=registry, registry_source=registry_source))


def before_inference(*, scroll, declared_volume_id, store_identity: dict, registry=None,
                     registry_source=None) -> dict:
    """Point 2."""
    si = store_identity or {}
    observed = si.get("volume_id") or parse_volume_id_from_url(si.get("source_url")
                                                               or si.get("canonical_source_url"))
    if si.get("physical_scroll") and _key(si.get("physical_scroll")) != _key(scroll):
        v = verify("INFERENCE", scroll=scroll, declared_volume_id=declared_volume_id,
                   observed_volume_id=observed, registry=registry, registry_source=registry_source)
        v["reasons"].append("MISMATCH: store belongs to %s, inference declared %s"
                            % (si.get("physical_scroll"), scroll))
        v.update(verified=False, state=MISMATCH)
        return _require(v)
    return _require(verify("INFERENCE", scroll=scroll, declared_volume_id=declared_volume_id,
                           observed_volume_id=observed, registry=registry,
                           registry_source=registry_source))


def before_package_export(*, scroll, declared_volume_id, package_volume_ids, registry=None,
                          registry_source=None) -> dict:
    """Point 3."""
    ids = sorted({str(x) for x in (package_volume_ids or []) if str(x).strip()})
    if len(ids) > 1:
        v = verify("PACKAGE_EXPORT", scroll=scroll, declared_volume_id=declared_volume_id,
                   observed_volume_id=ids[0], registry=registry, registry_source=registry_source)
        v["reasons"].append("MISMATCH: the package names %d volumes %s" % (len(ids), ids))
        v.update(verified=False, state=MISMATCH)
        return _require(v)
    return _require(verify("PACKAGE_EXPORT", scroll=scroll, declared_volume_id=declared_volume_id,
                           observed_volume_id=ids[0] if ids else None, registry=registry,
                           registry_source=registry_source))
