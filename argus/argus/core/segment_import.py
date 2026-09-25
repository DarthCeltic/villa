"""import.segment: bring a segment you already hold on disk into ARGUS, and say honestly what it is."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from argus.core import actions as A
from argus.core import eligible_target_operation_gate as ETG
from argus.core import exposure as EXP
from argus.core import native_3d_provider as N3P
from argus.core import official_identity as OI
from argus.core import paths
from argus.core import scroll_ids
from argus.core import tifxyz_geometry as TG
from argus.core import user_data as U
from argus.core import volume_id_gate as VG

CONTRACT = "argus-segment-import-plan-v1"
RECORD_SCHEMA = "argus-imported-segment-v1"
RECEIPT_SCHEMA = "argus-segment-import-receipt-v1"
ROOTS_ENV = "ARGUS_SEGMENT_IMPORT_ROOTS"
PLAN_FIELDS = frozenset({"path", "attach_volume_source", "attested_by", "attestation_reason",
                         "approved_plan_sha256"})
CODES = ("MISSING_PARAMETER", "BAD_PARAMETER", "PATH_TRAVERSAL", "PATH_OUTSIDE_ROOTS",
         "NOT_A_DIRECTORY", "NOT_TIFXYZ", "UNKNOWN_IDENTITY", "IDENTITY_CONFLICT",
         "INCOMPATIBLE")
MAX_BBOX_POINTS = 64_000_000


class SegmentRefusal(Exception):
    def __init__(self, code: str, why: str, **evidence):
        assert code in CODES, code
        super().__init__("%s: %s" % (code, why))
        self.code, self.why, self.evidence = code, why, evidence


def _sha256_json(value) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())



def allowed_roots() -> list:
    """Where a segment may be imported from."""
    env = os.environ.get(ROOTS_ENV)
    if env:
        return [Path(x) for x in env.split(os.pathsep) if x.strip()]
    roots = [paths.science_data(), paths.data(), paths.cache(), paths.artifact_write_root()]
    try:
        roots.insert(0, U.segment_import_inbox())
    except paths.PathContractViolation:
        pass
    return roots


def resolve_local_directory(raw) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise SegmentRefusal("MISSING_PARAMETER", "path must name a local directory")
    if "\x00" in raw or len(raw) > 400:
        raise SegmentRefusal("BAD_PARAMETER", "path is not a plausible directory name")
    if paths.is_remote_path(raw) or not paths.plain_local_path(raw):
        raise SegmentRefusal("BAD_PARAMETER", "path must be a plain local directory (no network path, stream or device form)")
    if ".." in raw.replace("\\", "/").split("/"):
        raise SegmentRefusal("PATH_TRAVERSAL",
                             "path contains a '..' segment; name the directory itself, not a "
                             "route out of a folder")
    if not Path(raw).is_absolute():
        raise SegmentRefusal("PATH_OUTSIDE_ROOTS",
                             "path must be absolute so it can be checked against the allowed "
                             "import roots", roots=[str(r) for r in allowed_roots()])
    real = Path(os.path.realpath(raw))
    roots = allowed_roots()
    if not any(paths.within_root(real, Path(os.path.realpath(r))) for r in roots):
        raise SegmentRefusal("PATH_OUTSIDE_ROOTS",
                             "%s is not inside an allowed import root (a link or junction that "
                             "leads out of one does not count)" % real,
                             roots=[str(r) for r in roots])
    if not real.is_dir():
        raise SegmentRefusal("NOT_A_DIRECTORY", "%s is not a directory" % real)
    return real



def inspect_geometry(d: Path) -> dict:
    """Completeness, TIFF headers, matching x/y/z shapes and the hashes."""
    missing = [n for n in (*N3P.TIFXYZ_REQUIRED, N3P.TIFXYZ_META_FILE) if not (d / n).is_file()]
    if missing:
        raise SegmentRefusal("NOT_TIFXYZ", "not a tifxyz directory: %s missing" % missing,
                             missing=missing)
    try:
        meta = json.loads((d / N3P.TIFXYZ_META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SegmentRefusal("NOT_TIFXYZ", "meta.json is unreadable: %s" % e) from None
    if not isinstance(meta, dict) or meta.get("format") != "tifxyz":
        raise SegmentRefusal("NOT_TIFXYZ", "meta.json declares format %r, not 'tifxyz'"
                             % (meta.get("format") if isinstance(meta, dict) else None))
    shapes = {}
    for n in N3P.TIFXYZ_REQUIRED:
        with (d / n).open("rb") as fh:
            if fh.read(4) not in N3P._TIFF_MAGIC:
                raise SegmentRefusal("NOT_TIFXYZ", "%s does not carry a TIFF header" % n)
        try:
            import tifffile
            with tifffile.TiffFile(str(d / n)) as tf:
                shapes[n] = list(tf.pages[0].shape)
        except ImportError:
            shapes = {}
            break
        except Exception as e:
            raise SegmentRefusal("NOT_TIFXYZ", "%s is not a readable TIFF: %s"
                                 % (n, str(e)[:120])) from None
    if shapes and len({tuple(s) for s in shapes.values()}) != 1:
        raise SegmentRefusal("NOT_TIFXYZ", "x, y and z differ in shape %s" % shapes,
                             shapes=shapes)
    names = [*N3P.TIFXYZ_REQUIRED, N3P.TIFXYZ_META_FILE]
    if (d / N3P.VOLUME_SOURCE_FILE).is_file():
        names.append(N3P.VOLUME_SOURCE_FILE)
    shape_yx = next(iter(shapes.values())) if shapes else None
    return {"files": {n: {"sha256": U.sha256_file(d / n), "bytes": (d / n).stat().st_size}
                      for n in names},
            "shape_yx": shape_yx,
            "shape_checked": bool(shapes),
            "canvas_full_resolution": _canvas(shape_yx, meta.get("scale")),
            "bbox": _bbox(d, meta.get("bbox"), shape_yx),
            "meta": {k: meta.get(k) for k in ("format", "uuid", "type", "scale", "bbox")
                     if k in meta}}


def _canvas(shape_yx, scale) -> dict:
    if not shape_yx or len(shape_yx) < 2:
        return {"shape_yx": None, "why": "the grid shape was not read"}
    try:
        sx, sy = (float(scale[0]), float(scale[1])) if isinstance(scale, (list, tuple)) \
            else (float(scale), float(scale))
    except (TypeError, ValueError, IndexError):
        return {"shape_yx": None, "why": "meta.json carries no usable scale"}
    height, width = TG.full_resolution_shape(shape_yx[:2], (sy, sx))
    return {"shape_yx": [height, width], "stored_shape_yx": [int(shape_yx[0]), int(shape_yx[1])],
            "scale_xy": [sx, sy],
            "basis": "lround(stored / float32(scale)) per axis, as Villa sizes tifxyz"}


def _bbox(d: Path, stored, shape_yx) -> dict:
    if not shape_yx or int(shape_yx[0]) * int(shape_yx[1]) > MAX_BBOX_POINTS:
        return {"state": "NOT_CHECKED", "stored": stored, "recomputed": None,
                "reasons": ["the grid is too large to recompute the bbox in a plan"
                            if shape_yx else "the grid shape was not read"]}
    try:
        import tifffile
        x, y, z = (tifffile.imread(str(d / name)) for name in N3P.TIFXYZ_REQUIRED)
        return TG.bbox_report(stored, x, y, z)
    except Exception as exc:
        return {"state": "NOT_CHECKED", "stored": stored, "recomputed": None,
                "reasons": ["x/y/z could not be read for a bbox check: %s" % str(exc)[:120]]}


def geometry_warnings(geometry: dict) -> list[str]:
    bbox = geometry.get("bbox") or {}
    if bbox.get("state") == "BBOX_UNTRUSTED":
        return ["BBOX_UNTRUSTED: " + "; ".join(bbox["reasons"])
                + ". The valid-point box is reported; meta.json is unchanged."]
    return []



def _plausible_bbox(bb) -> bool:
    """A stored bbox that is inverted or negative is reported by the geometry check as untrusted and is not evidence of which volume the segment lies in."""
    try:
        lo, hi = bb
        return all(float(a) >= 0 and float(b) >= float(a) for a, b in zip(lo, hi)) and len(lo) == len(hi) == 3
    except (TypeError, ValueError):
        return False


def gather_evidence(d: Path, declared=None) -> dict:
    """What the segment says about itself, in the order the identity gate trusts it: the volume_source.txt line, then the `<id>-on-<volume id>-<pitch>um` name Villa gives a segment, then the extent in..."""
    ev = {"names": [], "scroll_hints": []}
    if declared:
        ev["names"].append({"kind": "volume_source_file", "text": declared})
    ev["names"].append({"kind": "segment_name", "text": d.name})
    if d.parent.name:
        ev["scroll_hints"].append({"kind": "the folder holding the segment", "text": d.parent.name})
    try:
        meta = json.loads((d / N3P.TIFXYZ_META_FILE).read_text(encoding="utf-8"))
        bb = meta.get("bbox")
        if isinstance(bb, list) and len(bb) == 2 and _plausible_bbox(bb):
            ev["bbox"] = bb
    except (OSError, ValueError, AttributeError):
        pass
    return ev


def read_identity(d: Path, attach, attested_by, reason) -> dict:
    declared = None
    src = d / N3P.VOLUME_SOURCE_FILE
    if src.is_file():
        try:
            declared = N3P._read_source_line(src)
        except (OSError, ValueError) as e:
            raise SegmentRefusal("UNKNOWN_IDENTITY", "volume_source.txt is unreadable: %s"
                                 % e) from None
    attach = str(attach).rstrip(chr(13) + chr(10)) if attach else None
    if declared and attach and declared != attach:
        raise SegmentRefusal("IDENTITY_CONFLICT",
                             "the attached volume source differs from the segment's own "
                             "volume_source.txt; an attachment never overrides the file",
                             volume_source_txt=declared, attached=attach)
    auto = OI.identify_volume(gather_evidence(d, declared))
    summary = {k: auto[k] for k in ("state", "confidence", "evidence_used", "contradictions",
                                    "reasons", "survey")}
    if auto["contradictions"]:
        raise SegmentRefusal("IDENTITY_CONFLICT",
                             "the evidence in the segment disagrees with itself or with the "
                             "official survey: %s. Nothing is guessed and an attestation cannot "
                             "outvote the data." % "; ".join(auto["contradictions"]),
                             auto_identification=summary)
    if declared:
        basis, source = "VOLUME_SOURCE_FILE", declared
    elif auto["state"] == "IDENTIFIED":
        source = (auto["identity"]["source_url"] or "").rstrip("/")
        if attach and OI.parse_volume_store_token(attach) != auto["identity"]["volume_id"]:
            raise SegmentRefusal("IDENTITY_CONFLICT",
                                 "the attached volume source names a different volume than the "
                                 "one the segment's own evidence identifies (%s)"
                                 % auto["identity"]["volume_id"], attached=attach,
                                 auto_identification=summary)
        basis = "AUTO_IDENTIFIED"
    elif attach:
        if not (isinstance(attested_by, str) and attested_by.strip()
                and isinstance(reason, str) and len(reason.strip()) >= 8):
            raise SegmentRefusal("UNKNOWN_IDENTITY",
                                 "an attached identity needs attested_by (who vouches for it) "
                                 "and attestation_reason (at least 8 characters); it is never "
                                 "accepted anonymously", auto_identification=summary)
        basis, source = "OPERATOR_ATTESTED", attach
    else:
        raise SegmentRefusal("UNKNOWN_IDENTITY",
                             "no volume_source.txt, so the segment does not say which volume it was "
                             "traced on, and the automatic identification could not settle it: %s. It can be "
                             "attached manually with attested_by and attestation_reason, but "
                             "never silently" % ("; ".join(auto["reasons"]) or "no evidence"),
                             auto_identification=summary)
    return {"basis": basis, "volume_source": source,
            "attested_by": attested_by.strip() if basis == "OPERATOR_ATTESTED" else None,
            "attestation_reason": reason.strip() if basis == "OPERATOR_ATTESTED" else None,
            "auto_identification": summary,
            "confidence": auto["confidence"] if basis == "AUTO_IDENTIFIED" else None}


def _pitch_from_records(canon: str, token: str) -> dict:
    """Pitch from official records only, never from a directory or store name."""
    for scroll, vid, row in OI.survey_volumes():
        if vid == token and row.get("pixel_size_um"):
            return {"pitch_um": row["pixel_size_um"], "basis": "public official survey"}
    try:
        p = paths.find_artifact("acquisition_survey", "ACQUISITION_SURVEY.json")
        for r in json.loads(Path(p).read_text(encoding="utf-8")).get("rows") or []:
            if (r.get("scan_identity") or {}).get("scan_id") == token \
                    and (r.get("pitch_energy") or {}).get("pitch_um"):
                return {"pitch_um": r["pitch_energy"]["pitch_um"], "basis": "official acquisition survey"}
    except (OSError, ValueError):
        pass
    try:
        p = paths.find_artifact("volume_standings", "VOLUMES.json")
        for v in json.loads(Path(p).read_text(encoding="utf-8")).get("volumes") or []:
            if str(v.get("volume_id")) == token and v.get("voxel_um"):
                return {"pitch_um": v["voxel_um"], "basis": "official volume standings"}
    except (OSError, ValueError):
        pass
    return {"pitch_um": None, "basis": "no official record carries a pitch for this volume"}


CEILING_ELIGIBLE = (
    "GEOMETRY_ONLY: %s is an eligible prize target in the target registry, so the eligible-target "
    "operation gate permits only geometry operations on it. Importing this segment authorises no "
    "ink inference, detector, training or reading, and it is not a prize-eligibility claim.")
CEILING_UNDECIDED = (
    "GEOMETRY_ONLY (fail-closed): the target registry could not settle whether %s is an eligible "
    "prize target, so the eligible-target ceiling applies until it can. Importing authorises no "
    "ink inference, detector, training or reading.")


def _store_volume_id(norm: str, token: str) -> str:
    """Return the full store identity without letting ``-masked`` consume the volume name."""
    parts = [p for p in norm.split("?")[0].rstrip("/").split("/") if p]
    if len(parts) < 2 or parts[-2] != "volumes" or not parts[-1].endswith(".zarr"):
        return token
    name = parts[-1][:-len(".zarr")]
    if name.endswith("-masked"):
        name = name[:-len("-masked")]
    return name if name.startswith(token) else token


def compatibility(identity: dict) -> dict:
    source = identity["volume_source"]
    norm = source.replace("\\", "/")
    segment = ETG.scroll_segment_from_source(norm)
    if segment is None:
        raise SegmentRefusal("UNKNOWN_IDENTITY",
                             "the volume source names no <scroll>/volumes/<id>.zarr path, so no "
                             "scroll can be read from it", volume_source=source)
    try:
        canon = scroll_ids.resolve(segment)
    except KeyError as e:
        raise SegmentRefusal("UNKNOWN_IDENTITY", "the volume source names scroll %r, which is "
                             "not a registered scroll (%s)" % (segment, e)) from None
    token = OI.parse_volume_store_token(norm)
    if not token:
        raise SegmentRefusal("UNKNOWN_IDENTITY", "no 14-digit volume id could be read from the "
                             "volume source", volume_source=source)
    vg = VG.verify("ACQUISITION", scroll=canon, declared_volume_id=token,
                   observed_volume_id=token)
    if not vg["verified"]:
        code = "INCOMPATIBLE" if vg["state"] == VG.SUPERSEDED else "UNKNOWN_IDENTITY"
        raise SegmentRefusal(code, "volume %s is %s for %s: %s"
                             % (token, vg["state"], canon, "; ".join(vg["reasons"])),
                             volume_gate=vg)
    volume_id = _store_volume_id(norm, token)
    eligible_target, registered = None, None
    try:
        entry = ETG._find_entry(ETG.load_registry()["doc"], canon)
        if entry is not None:
            gp = entry.get("grand_prize_2027") or {}
            eligible_target, registered = bool(gp.get("eligible")), gp.get("volume_id")
    except (OSError, ValueError):
        pass
    gate = ETG.preflight(declared_physical_scroll=canon, target_volume_id=volume_id,
                         operation_class=ETG.GEOMETRY_ONLY, authorized=True,
                         target_volume_source=norm)
    gate = {k: gate[k] for k in ("verdict", "reasons", "eligible_target", "registered_volume_id",
                                 "contract")}
    if eligible_target is None:
        state = "UNKNOWN"
    elif not eligible_target:
        state = "NOT_ELIGIBLE"
    else:
        state = "ELIGIBLE_EXACT_VOLUME" if registered == volume_id else \
            "ELIGIBLE_SCROLL_OTHER_VOLUME"
    ceiling = []
    if state in ("ELIGIBLE_EXACT_VOLUME", "ELIGIBLE_SCROLL_OTHER_VOLUME"):
        ceiling.append(CEILING_ELIGIBLE % canon)
    elif state == "UNKNOWN":
        ceiling.append(CEILING_UNDECIDED % canon)
    try:
        exp = EXP.target_exposure(canon)
        exposure = {"state": "EXPOSED_OR_FENCED" if exp["kinds"] or exp["classifications"]
                    else "NO_RECORD", "kinds": exp["kinds"],
                    "classifications": exp["classifications"], "fresh": exp["fresh"],
                    "banner": exp["contamination_banner"]}
    except (EXP.TargetRegistryRefusal, OSError, ValueError) as e:
        exposure = {"state": "UNKNOWN", "why": "the target exposure registry cannot be read: %s"
                    % str(e)[:120]}
    if exposure["state"] in ("EXPOSED_OR_FENCED", "UNKNOWN"):
        ceiling.append("EXPOSURE: %s. Results on this scroll are apparatus or development work, "
                       "never unseen-scroll generalization; ink-model agreement is not "
                       "independent confirmation." % (exposure.get("banner")
                                                      or "exposure could not be established"))
    return {"scroll": canon, "volume_token": token, "volume_id": volume_id,
            "official_volume": {"state": vg["state"], "registry_source": vg["registry_source"]},
            "pitch": _pitch_from_records(canon, token),
            "eligibility": {"state": state, "eligible_target": eligible_target,
                            "registered_eligible_volume_id": registered, "gate": gate},
            "exposure": exposure, "claim_ceiling": ceiling,
            "not_claimed": ["a qualified detector", "unseen-scroll generalization",
                            "prize eligibility", "independent physical ground truth"]}



def segment_key(files: dict, volume_source: str) -> str:
    return _sha256_json({"files": {n: files[n]["sha256"] for n in sorted(files)
                                   if n != N3P.VOLUME_SOURCE_FILE},
                         "volume_source": volume_source})[:20]


def _verified(ident: dict) -> bool:
    return ident["basis"] == "VOLUME_SOURCE_FILE" or (
        ident["basis"] == "AUTO_IDENTIFIED" and ident.get("confidence") == "HIGH")


def plan(params: dict) -> dict:
    unknown = sorted(set(params) - PLAN_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "import.segment does not accept %s" % unknown)
    body = {"schema": CONTRACT,
            "changes": ["reads the named directory and hashes its tifxyz files",
                        "on approval: registers the segment in the user-data store with its "
                        "hashes, identity binding and claim ceiling, and writes a receipt"],
            "cost": {"gpu": "none", "network": "none", "bytes_copied": 0,
                     "seconds": "hashing the three tifs"},
            "leases": [], "reversible": True,
            "may_refuse": list(CODES) + ["PLAN_NOT_APPROVED"],
            "executes_a_process": False,
            "note": "the segment is referenced where it lies, never copied"}
    try:
        d = resolve_local_directory(params.get("path"))
        geo = inspect_geometry(d)
        ident = read_identity(d, params.get("attach_volume_source"), params.get("attested_by"),
                              params.get("attestation_reason"))
        compat = compatibility(ident)
    except SegmentRefusal as e:
        body.update(ready=False, would_be_refused=True, refusal_code=e.code,
                    planning_refusal="%s: %s" % (e.code, e.why),
                    note="%s: %s" % (e.code, e.why), changes=["nothing is registered"],
                    evidence=e.evidence)
        body["plan_sha256"] = _sha256_json({"schema": CONTRACT, "code": e.code, "why": e.why})
        return body
    key = segment_key(geo["files"], ident["volume_source"])
    already = U.imported_segment_path(compat["scroll"], key).is_file()
    body.update(
        ready=True, segment_key=key, already_registered=already,
        directory=str(d), geometry=geo, identity=dict(ident, verified=_verified(ident)),
        compatibility=compat, claim_ceiling=compat["claim_ceiling"],
        warnings=geometry_warnings(geo),
        registers_under=U.IMPORTED_SEGMENTS_DIR + " in the user-data store")
    el = compat["eligibility"]
    body["changes"] = [
        "%d files hashed, %s; nothing is copied" % (
            len(geo["files"]), "x/y/z shape %s" % geo["shape_yx"] if geo["shape_yx"] else
            "shapes not checked"),
        "identity %s: %s, volume %s (official volume %s); %s" % (
            "VERIFIED from volume_source.txt" if ident["basis"] == "VOLUME_SOURCE_FILE"
            else "AUTO-IDENTIFIED (%s confidence) from the segment's own evidence" % ident["confidence"]
            if ident["basis"] == "AUTO_IDENTIFIED"
            else "ATTESTED by %s, not verified" % ident["attested_by"],
            compat["scroll"], compat["volume_id"], compat["official_volume"]["state"],
            "pitch %s um (%s)" % (compat["pitch"]["pitch_um"], compat["pitch"]["basis"])
            if compat["pitch"]["pitch_um"] else compat["pitch"]["basis"]),
        "eligibility: %s%s" % (el["state"], "" if el["gate"]["verdict"] == "PERMITTED"
                               else " (gate: %s)" % "; ".join(el["gate"]["reasons"])[:200]),
        *(["full-resolution canvas %d x %d (rows x columns), as Villa sizes it"
           % tuple(geo["canvas_full_resolution"]["shape_yx"])]
          if geo["canvas_full_resolution"].get("shape_yx") else []),
        *geometry_warnings(geo),
        *compat["claim_ceiling"],
        "on approval: registers it in the user-data store with its hashes and this identity "
        "binding, and writes a receipt" + ("; it is already registered, so nothing is rewritten"
                                           if already else "")]
    stable = json.loads(json.dumps(body, default=str))
    for k in ("evidence", "note", "changes", "cost", "already_registered", "registers_under"):
        stable.pop(k, None)
    body["plan_sha256"] = _sha256_json(stable)
    return body



def execute(params: dict, *, actor: str) -> dict:
    p = plan(params)
    if not p.get("ready"):
        raise A.Refused(p["refusal_code"], p["planning_refusal"], plan=p)
    if params.get("approved_plan_sha256") != p["plan_sha256"]:
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the plan hash returned by /plan; the "
                        "directory or the records may have changed since it was planned",
                        expected=p["plan_sha256"])
    record = {"schema": RECORD_SCHEMA, "segment_key": p["segment_key"],
              "scroll": p["compatibility"]["scroll"], "directory": p["directory"],
              "files": p["geometry"]["files"], "shape_yx": p["geometry"]["shape_yx"],
              "geometry": p["geometry"], "warnings": p["warnings"],
              "meta": p["geometry"]["meta"], "identity": p["identity"],
              "compatibility": p["compatibility"], "claim_ceiling": p["claim_ceiling"],
              "registered_by": actor, "registered_utc": _utc(), "plan_sha256": p["plan_sha256"],
              "copied_bytes": 0}
    receipt = {"schema": RECEIPT_SCHEMA, "segment_key": p["segment_key"],
               "scroll": record["scroll"], "plan_sha256": p["plan_sha256"],
               "approved_plan_sha256": params["approved_plan_sha256"], "actor": actor,
               "utc": record["registered_utc"], "files": record["files"],
               "identity_basis": p["identity"]["basis"], "volume_source":
               p["identity"]["volume_source"], "claim_ceiling": p["claim_ceiling"],
               "geometry": p["geometry"], "warnings": p["warnings"],
               "executed_a_process": False, "copied_bytes": 0}
    if p["already_registered"]:
        return {"status": "OK", "registered": False, "already_registered": True,
                "segment_key": p["segment_key"], "scroll": record["scroll"],
                "claim_ceiling": p["claim_ceiling"], "plan_sha256": p["plan_sha256"]}
    written = U.register_imported_segment(record, receipt)
    return {"status": "OK", "registered": True, "already_registered": False,
            "segment_key": p["segment_key"], "scroll": record["scroll"],
            "identity_basis": p["identity"]["basis"], "claim_ceiling": p["claim_ceiling"],
            "record": written["record"], "receipt": written["receipt"],
            "plan_sha256": p["plan_sha256"]}
