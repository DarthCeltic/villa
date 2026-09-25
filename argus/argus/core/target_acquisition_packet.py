"""Target-acquisition packet: the ONLY way a prize target's bytes may ever be authorised for fetch."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import secrets
import time

from argus.core import official_identity as OI
from argus.core import paths

CONTRACT = "argus-target-acquisition-packet-v1"
PACKET_TYPE = CONTRACT
OUTPUT_CLASS = "EXPLORATORY_TARGET_SIGNAL"
PACKET_DIR_PARTS = ("target_acquisition_packets", "packets")

PERMITTABLE_STAGES = ("acquire", "surface_controls", "grow_controls", "render_controls")
FORBIDDEN_STAGES = ("ink", "ink_on_target", "detector", "inference", "training", "blind_hunt",
                    "candidate_search", "prize_submission", "publication")

FLOORS_GIB = {"data": 20.0, "cache": 20.0}
MAX_AREA_CM2 = 4.0
MAX_TTL_HOURS = 72
UNKNOWN_VALUES = (None, "", "UNKNOWN")
VOLUME_ID_RE = OI.TOKEN_RE
IDENTITY_PROVEN = OI.PROVEN

STOPPING_RULES = (
  "STOP_IF_IDENTITY_GUESSED: any volume id, pitch, energy, chunk layout or store identity that is "
  "assumed rather than read from the official record or the sealed store",
  "STOP_IF_DATA_PADDED: any ROI clamped, padded, resampled or filled to fit",
  "STOP_IF_GATE_WEAKENED: any gate, threshold, floor or ceiling relaxed to let the route continue",
  "STOP_IF_TARGET_OUTPUT_RETUNES: any target output used to choose, tune or re-place anything",
  "STOP_IF_AREA_EXCEEDS_LIMIT: any surface, render or claim whose measured flattened area exceeds MAX_AREA_CM2",
  "STOP_IF_CEILING_EXCEEDED: any phase exceeding its object count or byte ceiling",
)

MUST_NOT_AUTHORISE = (
  "BLIND_HUNT", "ink or detector output on the target", "training on target data",
  "full-volume acquisition", "any fetch outside the listed phases", "spending a sealed holdout",
  "prize submission", "public publication", "viewing target pixels outside review_policy",
)

REVIEW_POLICY_KEYS = ("default", "viewable_artifacts", "viewers", "when", "blinded_with_controls",
                      "forbidden_views")

LAUNCH_FIELDS = (
  "runner_sha256", "module_manifest_sha256", "contract_sha256", "plan_sha256",
  "input_manifest_sha256", "checkpoint_sha256", "science_env_sha256", "command_sha256",
  "git_commit", "dirty_patch_sha256", "dependency_sha256",
)


class PacketRefusal(RuntimeError):
    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = list(problems or [])


def _canon(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


def _key(s) -> str:
    return str(s or "").strip().upper().replace("_", "").replace("-", "").replace(" ", "")



def roi_physical_mm(roi: dict, pitch_um: float) -> dict:
    p = float(pitch_um)
    return {a: (int(roi[a + "1"]) - int(roi[a + "0"])) * p / 1000.0 for a in ("z", "y", "x")}


def area_bound_cm2(roi: dict, pitch_um: float) -> dict:
    """Single-crossing surface bound: ab + bc + ca over the box's physical edges (see docstring)."""
    mm = roi_physical_mm(roi, pitch_um)
    a, b, c = (mm["z"] / 10.0, mm["y"] / 10.0, mm["x"] / 10.0)
    bound = a * b + b * c + c * a
    return {"edges_cm": {"z": a, "y": b, "x": c}, "bound_cm2": bound,
            "formula": "ab+bc+ca (single-crossing surface; flat sheet at any orientation included)",
            "not_bounded": "a sheet meeting an axis-parallel line k>1 times, or several wraps; "
                           "closed by STOP_IF_AREA_EXCEEDS_LIMIT at the measured flattened surface",
            "within_limit": bound <= MAX_AREA_CM2}



PIXEL_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif", ".mp4", ".npy", ".npz",
             ".zarr", ".ply", ".obj", ".nrrd", ".h5")
TEXT_EXT = (".json", ".jsonl", ".md", ".txt", ".yaml", ".yml", ".csv")
ACCESS_WORDS = re.compile(
  r"(?i)\b(render(?:ed|s)?|thumbnail|screenshot|montage|contact.sheet|slice[sd]?|preview|viewed|"
  r"inspect(?:ed|ion)?|detector.output|ink.?map|inference|grown|grow(?:ing)?|traced|seeded|"
  r"chunks?|voxels?|intensity|histogram|hellinger|staged|fetched|downloaded|level-?[0-5])\b")


def scan_prior_inspection(*, scroll: str, store_id: str, text_roots, name_roots,
                          exclude_parts=(), max_text_bytes: int = 8 << 20, window: int = 4) -> dict:
    """Candidate pixel accesses of `scroll`."""
    pat = re.compile(r"(?i)pherc_?0*%s|%s" % (re.escape(str(scroll).upper().replace("PHERC", "")
                                                          .lstrip("0")), re.escape(store_id)))
    hits, scanned_files, skipped_big, unreadable = [], 0, [], []

    def excluded(p: str) -> bool:
        s = p.replace("\\", "/")
        return any(x in s for x in exclude_parts)

    for root in name_roots:
        r = pathlib.Path(root)
        if not r.exists():
            unreadable.append("%s (absent)" % r)
            continue
        for dirpath, dirnames, filenames in os.walk(r):
            if excluded(dirpath):
                dirnames[:] = []
                continue
            keep = []
            for d in dirnames:
                if d.lower().endswith(".zarr"):
                    if pat.search(d):
                        hits.append({"kind": "PIXEL_STORE_DIR", "path": str(pathlib.Path(dirpath) / d)})
                    continue
                keep.append(d)
            dirnames[:] = keep
            for f in filenames:
                if f.lower().endswith(PIXEL_EXT) and pat.search(f):
                    fp = pathlib.Path(dirpath) / f
                    if not excluded(str(fp)):
                        hits.append({"kind": "PIXEL_FILE_NAMED", "path": str(fp)})
    for root in text_roots:
        r = pathlib.Path(root)
        if not r.exists():
            unreadable.append("%s (absent)" % r)
            continue
        files = [r] if r.is_file() else []
        if r.is_dir():
            for dirpath, dirnames, filenames in os.walk(r):
                if excluded(dirpath):
                    dirnames[:] = []
                    continue
                dirnames[:] = [d for d in dirnames if not d.lower().endswith(".zarr")]
                files.extend(pathlib.Path(dirpath) / f for f in filenames
                             if f.lower().endswith(TEXT_EXT))
        for fp in files:
            if excluded(str(fp)):
                continue
            try:
                size = fp.stat().st_size
                if size > max_text_bytes:
                    skipped_big.append({"path": str(fp), "bytes": size})
                    continue
                raw = fp.read_bytes()
            except OSError as e:
                unreadable.append("%s (%s)" % (fp, type(e).__name__))
                continue
            scanned_files += 1
            text = raw.decode("utf-8", errors="replace")
            if not pat.search(text):
                continue
            lines = text.splitlines()
            ctx_hits = []
            for i, ln in enumerate(lines):
                if pat.search(ln):
                    lo, hi = max(0, i - window), min(len(lines), i + window + 1)
                    words = sorted({m.group(0).lower() for m in
                                    ACCESS_WORDS.finditer("\n".join(lines[lo:hi]))})
                    if words:
                        ctx_hits.append({"line": i + 1, "words": words[:8]})
            if ctx_hits:
                hits.append({"kind": "TEXT_RECORD_ACCESS_CONTEXT", "path": str(fp),
                             "sha256": hashlib.sha256(raw).hexdigest(),
                             "lines": len(ctx_hits), "first": ctx_hits[:3]})
    complete = not skipped_big and not unreadable
    return {"scroll": scroll, "store_id": store_id, "text_roots": [str(x) for x in text_roots],
            "name_roots": [str(x) for x in name_roots], "exclude_parts": list(exclude_parts),
            "scanned_text_files": scanned_files, "hits": hits, "hit_count": len(hits),
            "skipped_oversize": skipped_big, "unreadable_or_absent": unreadable,
            "complete": complete}


def attestation(scan: dict, *, rulings=None, required_roots=()) -> dict:
    """PASS only when the scan is complete, covers every required root, and every hit is cleared by an operator ruling bound to the hit's path (and sha256 when the hit has one)."""
    rulings = rulings or []
    uncleared = []
    for h in scan.get("hits") or []:
        ok = any(r.get("path") == h["path"] and r.get("authorised_by") == "operator"
                 and (h.get("sha256") is None or r.get("sha256") == h.get("sha256"))
                 and r.get("ruling") == "NOT_INSPECTION" for r in rulings)
        if not ok:
            uncleared.append(h)
    covered = set(scan.get("text_roots") or []) | set(scan.get("name_roots") or [])
    missing_roots = [str(r) for r in required_roots if str(r) not in covered]
    reasons = []
    if uncleared:
        reasons.append("%d uncleared prior-access hit(s)" % len(uncleared))
    if not scan.get("complete"):
        reasons.append("scan incomplete (oversize/unreadable/absent roots)")
    if missing_roots:
        reasons.append("required roots not scanned: %s" % missing_roots)
    state = "PASS" if not reasons else ("FAIL" if uncleared else "UNKNOWN")
    return {"state": state, "reasons": reasons, "uncleared": uncleared,
            "missing_roots": missing_roots, "scan_sha256": _canon(scan)}



def disk_preflight(free_gib: dict) -> dict:
    reasons = []
    for drive, floor in FLOORS_GIB.items():
        v = free_gib.get(drive)
        if v is None:
            reasons.append("%s free space UNKNOWN" % drive)
        elif float(v) < floor:
            reasons.append("%s free %.2f GiB < floor %.0f GiB" % (drive, float(v), floor))
    return {"state": "PASS" if not reasons else "FAIL", "free_gib": free_gib,
            "floors_gib": dict(FLOORS_GIB), "reasons": reasons}


def conveyor_preflight(status: dict | None) -> dict:
    """The conveyor must be READABLE and running no step."""
    if not isinstance(status, dict) or "daemon" not in status:
        return {"state": "UNKNOWN", "reasons": ["conveyor status unreadable"], "status": status}
    d = status.get("daemon") or {}
    q = status.get("queue") or {}
    busy = []
    if str(d.get("current_action") or "").strip():
        busy.append("daemon current_action=%r" % d.get("current_action"))
    if q.get("RUNNING"):
        busy.append("queue RUNNING=%s" % q.get("RUNNING"))
    return {"state": "PASS" if not busy else "FAIL", "reasons": busy,
            "daemon_state": d.get("state"), "current_action": d.get("current_action"),
            "queue": q}



def _unknown(v) -> bool:
    return v in UNKNOWN_VALUES or (isinstance(v, str) and v.strip().upper().startswith("UNKNOWN"))


def control_from_resolution(control_id: str, resolution: dict) -> dict:
    """The packet's control binding, built ONLY from an official_identity resolution."""
    r = resolution or {}
    return {"control_id": control_id, "identity_status": r.get("state"),
            "identity_contract": r.get("contract"), "identity_sha256": r.get("identity_sha256"),
            "resolved_identity": r.get("resolved_identity"),
            "failing_fields": sorted((r.get("failing_fields") or {}).keys())}


def control_identity_problems(c: dict) -> list:
    """A control is PROVEN only by official catalogue resolution."""
    out = []
    cid = c.get("control_id")
    if c.get("identity_status") != IDENTITY_PROVEN:
        out.append("%s identity %s (requires %s)" % (cid, c.get("identity_status"), IDENTITY_PROVEN))
    if c.get("identity_contract") != OI.CONTRACT:
        out.append("%s identity not resolved by %s" % (cid, OI.CONTRACT))
    ri = c.get("resolved_identity")
    if not isinstance(ri, dict) or _unknown(c.get("identity_sha256")) \
            or OI.canon_sha(ri) != c.get("identity_sha256"):
        out.append("%s identity hash absent or does not re-derive from its resolved identity" % cid)
    return out


def problems(p: dict, *, now: float | None = None) -> list:
    """Every binding checked."""
    out = []
    if not isinstance(p, dict):
        return ["packet is not a mapping"]
    if p.get("packet_type") != PACKET_TYPE or p.get("contract") != CONTRACT:
        out.append("TYPE: packet_type/contract must be %s" % CONTRACT)
    if not str(p.get("packet_id") or "").strip():
        out.append("TERMS: packet_id missing")
    if p.get("single_use") is not True:
        out.append("TERMS: single_use must be true")
    if p.get("authorised_by") != "operator":
        out.append("TERMS: authorised_by must be 'operator'")
    if p.get("output_class") != OUTPUT_CLASS:
        out.append("TERMS: output_class must be %s" % OUTPUT_CLASS)
    ttl = p.get("ttl_hours")
    if not isinstance(ttl, (int, float)) or not 0 < ttl <= MAX_TTL_HOURS:
        out.append("TERMS: ttl_hours must be in (0, %d]" % MAX_TTL_HOURS)
    if not set(MUST_NOT_AUTHORISE) <= set(p.get("must_not_authorise") or ()):
        out.append("TERMS: must_not_authorise dropped %s"
                   % sorted(set(MUST_NOT_AUTHORISE) - set(p.get("must_not_authorise") or ())))
    if not set(STOPPING_RULES) <= set(p.get("stopping_rules") or ()):
        out.append("STOPPING_RULES: missing %d required rule(s)"
                   % len(set(STOPPING_RULES) - set(p.get("stopping_rules") or ())))

    launch = p.get("launch") or {}
    for f in LAUNCH_FIELDS:
        if _unknown(launch.get(f)):
            out.append("LAUNCH: %s unbound/UNKNOWN" % f)
    if (launch.get("relevant_dirty") or []):
        out.append("LAUNCH: imported files dirty: %s" % launch.get("relevant_dirty"))

    t = p.get("target") or {}
    for f in ("scroll", "volume_id", "volume_store", "store_id", "pitch_um", "energy_kev",
              "identity_source_sha256"):
        if _unknown(t.get(f)):
            out.append("TARGET: %s unbound/UNKNOWN" % f)
    vid = str(t.get("volume_id") or "")
    if not VOLUME_ID_RE.match(vid):
        out.append("TARGET: volume_id %r is not an exact 14-digit official id" % vid)
    if vid and not str(t.get("volume_store") or "").startswith(vid + "-"):
        out.append("TARGET: volume_store does not carry volume_id as its prefix")
    if vid in (t.get("superseded_volume_ids") or []):
        out.append("TARGET: volume_id is a superseded id")
    urls = t.get("canonical_urls") or {}
    if set(urls) != {"dl", "s3"}:
        out.append("TARGET: canonical_urls must name exactly both mirrors (dl, s3)")
    else:
        from argus.core import volume_acquisition as VA
        from argus.core import volume_id_gate as VG
        try:
            ids = {VA.store_id(u) for u in urls.values()}
            if ids != {t.get("store_id")}:
                out.append("TARGET: mirrors derive store ids %s, packet binds %r"
                           % (sorted(ids), t.get("store_id")))
            if {VG.parse_volume_id_from_url(u) for u in urls.values()} != {vid}:
                out.append("TARGET: a mirror URL does not name volume_id %s" % vid)
        except Exception as e:
            out.append("TARGET: mirror URLs refused: %s" % e)
        v = VG.verify("ACQUISITION", scroll=t.get("scroll"), declared_volume_id=vid,
                      observed_volume_id=VG.parse_volume_id_from_url(urls.get("dl")))
        if not v["verified"]:
            out.append("TARGET: volume id gate %s: %s" % (v["state"], "; ".join(v["reasons"])))

    lic = p.get("licence") or {}
    if lic.get("ruling") != "PERMITS_LOCAL_FETCH_AND_USE" or _unknown(lic.get("spdx")) \
            or lic.get("family") in (None, "UNDECLARED", "PROPRIETARY") \
            or lic.get("operator_fence") not in (None, ""):
        out.append("LICENCE: ruling %r family %r spdx %r fence %r does not permit fetch"
                   % (lic.get("ruling"), lic.get("family"), lic.get("spdx"),
                      lic.get("operator_fence")))
    if lic.get("redistribute") is not False and lic.get("family") == "NONCOMMERCIAL":
        out.append("LICENCE: NONCOMMERCIAL data must record redistribute=false")

    npi = p.get("no_prior_inspection") or {}
    if npi.get("state") != "PASS":
        out.append("NO_PRIOR_INSPECTION: %s (%s)" % (npi.get("state"),
                                                     "; ".join(npi.get("reasons") or [])))

    roi = p.get("roi") or {}
    rule = roi.get("placement_rule") or {}
    if _unknown(rule.get("sha256")) or rule.get("sha256") != _canon(rule.get("text")):
        out.append("ROI: placement rule text/hash absent or does not match")
    if rule.get("uses_target_data") is not False:
        out.append("ROI: placement rule must declare uses_target_data=false")
    vox = roi.get("voxels")
    if not isinstance(vox, dict) or not all(isinstance(vox.get(k), int) for k in
                                            ("z0", "z1", "y0", "y1", "x0", "x1")):
        out.append("ROI: exact integer voxel box absent")
    else:
        if not all(0 <= vox[a + "0"] < vox[a + "1"] for a in "zyx"):
            out.append("ROI: empty or negative axis")
        elif not _unknown(t.get("pitch_um")):
            ab = area_bound_cm2(vox, float(t["pitch_um"]))
            if not ab["within_limit"]:
                out.append("ROI: area bound %.4f cm2 > %.1f" % (ab["bound_cm2"], MAX_AREA_CM2))
            if roi.get("physical_mm") != roi_physical_mm(vox, float(t["pitch_um"])):
                out.append("ROI: physical_mm does not match voxels x pitch")
        if roi.get("full_volume") is not False:
            out.append("ROI: must declare full_volume=false")

    phases = p.get("phases") or []
    if not phases:
        out.append("PHASES: none")
    total_bytes = 0
    for ph in phases:
        name = ph.get("phase")
        for f in ("array_path", "key_list_hash", "planned_objects", "byte_ceiling",
                  "acquisition_id", "store_id"):
            if f not in ph or (f != "array_path" and _unknown(ph.get(f))):
                out.append("PHASES: %s lacks %s" % (name, f))
        if ph.get("store_id") != t.get("store_id"):
            out.append("PHASES: %s store_id differs from target" % name)
        klh = ph.get("key_list_hash") or {}
        if isinstance(klh, dict) and klh.get("count") != ph.get("planned_objects"):
            out.append("PHASES: %s key count %r != planned_objects %r"
                       % (name, klh.get("count"), ph.get("planned_objects")))
        if ph.get("planned_upper_bound_bytes", 0) > ph.get("byte_ceiling", -1):
            out.append("PHASES: %s upper bound exceeds its ceiling" % name)
        if name != "A0" and ph.get("roi") != vox:
            out.append("PHASES: %s ROI is not the packet ROI" % name)
        if ph.get("requests_made") != 0:
            out.append("PHASES: %s dry run did not record zero requests" % name)
        total_bytes += int(ph.get("byte_ceiling") or 0)
    if phases and total_bytes != p.get("total_byte_ceiling"):
        out.append("PHASES: total_byte_ceiling %r != sum %d" % (p.get("total_byte_ceiling"),
                                                               total_bytes))
    if phases and sum(int(ph.get("planned_objects") or 0) for ph in phases) \
            != p.get("total_objects"):
        out.append("PHASES: total_objects does not match the phases")

    pre = p.get("preflight") or {}
    for part in ("disk", "conveyor"):
        if (pre.get(part) or {}).get("state") != "PASS":
            out.append("PREFLIGHT: %s %s" % (part, (pre.get(part) or {}).get("state")))

    ctrls = p.get("controls") or []
    if not ctrls:
        out.append("CONTROLS: none fixed in advance")
    for c in ctrls:
        if _unknown(c.get("control_id")):
            out.append("CONTROLS: a control has no id")
        out.extend("CONTROLS: %s" % x for x in control_identity_problems(c))

    rp = p.get("review_policy") or {}
    missing = [k for k in REVIEW_POLICY_KEYS if k not in rp]
    if missing:
        out.append("REVIEW_POLICY: missing %s" % missing)
    elif rp.get("default") != "SEALED_UNVIEWED":
        out.append("REVIEW_POLICY: default must be SEALED_UNVIEWED")

    st = list(p.get("permitted_stages") or [])
    if not st:
        out.append("STAGES: none permitted")
    bad = [s for s in st if s not in PERMITTABLE_STAGES]
    if bad:
        out.append("STAGES: not permittable (ink/detector/training are never): %s" % bad)
    if not set(FORBIDDEN_STAGES) <= set(p.get("forbidden_stages") or ()):
        out.append("STAGES: forbidden_stages must list %s" % list(FORBIDDEN_STAGES))
    return out



_ROOT_OVERRIDE = None


def set_root_for_tests(path):
    global _ROOT_OVERRIDE
    prev, _ROOT_OVERRIDE = _ROOT_OVERRIDE, path
    return prev


def packet_root() -> pathlib.Path:
    if _ROOT_OVERRIDE is not None:
        return pathlib.Path(_ROOT_OVERRIDE)
    return paths.artifact_write_root().joinpath(*PACKET_DIR_PARTS)


def packet_path(pid: str) -> pathlib.Path:
    return packet_root() / ("TAP_%s.json" % pid)


def consumption_path(pid: str) -> pathlib.Path:
    return packet_root() / ("TAP_CONSUMED_%s.json" % pid)


def _body_hash(doc: dict) -> str:
    return _canon({k: v for k, v in doc.items() if k != "packet_sha256"})


def _excl_write(p: pathlib.Path, body: dict, what: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PacketRefusal("%s %s already exists; single use means it is never rewritten"
                            % (what, p)) from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(body, indent=1, default=str))
        fh.flush()
        os.fsync(fh.fileno())


def issue(body: dict) -> dict:
    """Refuses unless `problems()` is empty."""
    probs = problems(body)
    if probs:
        raise PacketRefusal("REFUSING TO ISSUE target-acquisition packet:\n  - "
                            + "\n  - ".join(probs), probs)
    doc = {k: v for k, v in body.items() if not k.startswith("_")}
    doc["issued_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc["expires_epoch"] = int(time.time() + float(doc["ttl_hours"]) * 3600)
    doc["packet_sha256"] = _body_hash(doc)
    p = packet_path(doc["packet_id"])
    _excl_write(p, doc, "packet")
    return dict(doc, path=str(p))


def load(pid: str) -> dict:
    p = packet_path(pid)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PacketRefusal("packet %r unreadable at %s (%s); UNKNOWN refuses" % (pid, p, e)) \
            from None
    if doc.get("packet_sha256") != _body_hash(doc):
        raise PacketRefusal("packet %r content hash does not match its body; edited since issue"
                            % pid)
    return doc


def launch(pid: str, *, live_launch: dict, live_preflight: dict, now: float | None = None) -> dict:
    """Re-check everything live, then CONSUME before any request."""
    doc = load(pid)
    probs = ["ISSUED BODY NO LONGER VALID: %s" % x for x in problems(doc)]
    if consumption_path(pid).exists():
        probs.append("ALREADY CONSUMED; single use")
    if (now or time.time()) > float(doc.get("expires_epoch") or 0):
        probs.append("EXPIRED")
    for f in LAUNCH_FIELDS:
        want, got = (doc.get("launch") or {}).get(f), (live_launch or {}).get(f)
        if _unknown(got):
            probs.append("LAUNCH: %s not measurable live" % f)
        elif want != got:
            probs.append("LAUNCH: %s MISMATCH" % f)
    if (live_launch or {}).get("relevant_dirty"):
        probs.append("LAUNCH: imported files dirty now")
    for part in ("disk", "conveyor"):
        if ((live_preflight or {}).get(part) or {}).get("state") != "PASS":
            probs.append("PREFLIGHT (live): %s %s" % (part, ((live_preflight or {}).get(part)
                                                             or {}).get("state")))
    if probs:
        raise PacketRefusal("launch refused for %s:\n  - %s" % (pid, "\n  - ".join(probs)), probs)
    nonce = secrets.token_hex(16)
    record = {"contract": CONTRACT, "packet_id": pid, "packet_sha256": doc["packet_sha256"],
              "launch_nonce": nonce,
              "consumed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "live_launch": {f: live_launch.get(f) for f in LAUNCH_FIELDS},
              "live_preflight": live_preflight}
    _excl_write(consumption_path(pid), record, "consumption record")
    return {"packet_type": PACKET_TYPE, "packet_id": pid, "scroll": doc["target"]["scroll"],
            "packet_sha256": doc["packet_sha256"], "launch_nonce": nonce}


def verify_for_stage(token, *, scroll, stage, plan: dict | None = None,
                     now: float | None = None) -> dict:
    """The only verifier of a target authority."""
    reasons = []
    st = str(stage or "").strip().lower()
    if st in FORBIDDEN_STAGES or st not in PERMITTABLE_STAGES:
        reasons.append("STAGE_NOT_PERMITTABLE: %r (ink/detector/training on a target is never "
                       "authorised by any packet)" % stage)
    if not isinstance(token, dict) or token.get("packet_type") != PACKET_TYPE:
        reasons.append("NOT_A_TARGET_ACQUISITION_TOKEN")
        return {"verified": False, "reasons": reasons}
    pid = str(token.get("packet_id") or "")
    try:
        doc = load(pid)
    except PacketRefusal as e:
        return {"verified": False, "reasons": reasons + ["PACKET: %s" % e]}
    if doc.get("packet_sha256") != token.get("packet_sha256"):
        reasons.append("TOKEN_PACKET_HASH_MISMATCH")
    try:
        cons = json.loads(consumption_path(pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cons = None
        reasons.append("NOT_CONSUMED: a packet authorises only after launch() consumed it")
    if cons is not None and (cons.get("launch_nonce") != token.get("launch_nonce")
                             or cons.get("packet_sha256") != doc.get("packet_sha256")):
        reasons.append("TOKEN_NOT_FROM_THE_CONSUMING_LAUNCH")
    if _key(doc["target"]["scroll"]) != _key(scroll) or _key(token.get("scroll")) != _key(scroll):
        reasons.append("SCROLL_MISMATCH")
    if st and st not in (doc.get("permitted_stages") or []):
        reasons.append("STAGE_NOT_IN_PACKET: %r" % stage)
    if (now or time.time()) > float(doc.get("expires_epoch") or 0):
        reasons.append("EXPIRED")
    if st == "acquire":
        if not isinstance(plan, dict):
            reasons.append("NO_PLAN: an acquisition must present its plan")
        else:
            match = [ph for ph in doc.get("phases") or []
                     if ph.get("acquisition_id") == plan.get("acquisition_id")]
            if not match:
                reasons.append("PLAN_NOT_IN_PACKET: acquisition_id %r" % plan.get("acquisition_id"))
            else:
                ph = match[0]
                if ph.get("key_list_hash") != plan.get("key_list_hash") \
                        or ph.get("planned_objects") != plan.get("planned_objects") \
                        or int(plan.get("byte_ceiling") or 1 << 62) > int(ph.get("byte_ceiling")) \
                        or plan.get("store_id") != doc["target"]["store_id"] \
                        or str(plan.get("volume_id")) != str(doc["target"]["volume_id"]):
                    reasons.append("PLAN_DIFFERS_FROM_PACKET_PHASE %s" % ph.get("phase"))
    return {"verified": not reasons, "reasons": reasons, "packet_id": pid,
            "output_class": OUTPUT_CLASS}
