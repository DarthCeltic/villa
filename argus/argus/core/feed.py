"""The machine-readable feed: what a UI is allowed to know, and how it knows it."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.adapters.vigiles_decision import BLINDING_MARKERS
from argus.core import heartbeat as HB
from argus.core.contracts import Terminal, sha256_file

FEED_SCHEMA = "argus-feed-v1"

OPERATIONAL = ("PENDING", "RUNNING", "STALLED", "COMPLETE", "REFUSED", "UNKNOWN")

SEALED_KEYS = ("verdict", "candidate", "score", "mean", "hits", "threshold", "prob",
               "preview", "thumbnail", "map", "rule_output", "counts")


def sealed_by(p: Path) -> str | None:
    """The blinding marker governing this path, if any."""
    p = Path(p)
    for d in [p, *p.parents]:
        for m in BLINDING_MARKERS:
            if (d / m).is_file():
                return str(d / m)
        if d == d.parent:
            break
    return None


def _strip_sealed(obj):
    """Remove anything a sealed run must not reveal, at any depth."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(s in k.lower() for s in SEALED_KEYS):
                out[k] = "<sealed>"
            else:
                out[k] = _strip_sealed(v)
        return out
    if isinstance(obj, list):
        return [_strip_sealed(v) for v in obj]
    return obj


def run_record(run_dir: Path, *, now=None, collection: str | None = None) -> dict:
    """One run, as the UI receives it."""
    d = Path(run_dir)
    now = time.time() if now is None else now
    seal = sealed_by(d)
    receipt = d / "run.json"
    rec: dict = {
        "schema": FEED_SCHEMA,
        "run_id": d.name,
        "run_dir": str(d),
        "collection": collection,
        "target": None,
        "stage": None,
        "operational_state": "UNKNOWN",
        "highest_certified_stage": None,
        "terminal": None,
        "refusal_class": None,
        "refusal_reason": None,
        "progress": HB.read(d, now=now),
        "blinding": {"sealed": bool(seal), "marker": seal,
                     "note": ("scores, verdicts, candidate counts and previews are withheld "
                              "from this record while the experiment is sealed")
                             if seal else None},
        "artifacts": [], "hashes": {},
        "acquisition": None, "acquisition_family": None, "geometry": None,
        "geometry_refusal": None, "mesh_dir": None, "mesh_sha256": None,
        "orientation": None, "physical_window_mm": None, "detector": None,
    }

    if receipt.is_file():
        try:
            r = json.loads(receipt.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            rec["operational_state"] = "UNKNOWN"
            rec["refusal_reason"] = "the run receipt is unreadable: %s" % str(e)[:120]
            return rec
        if not str(r.get("schema", "")).startswith("argus-run"):
            rec["foreign"] = True
            rec["operational_state"] = "UNKNOWN"
            rec["refusal_reason"] = ("this run.json is not an ARGUS run receipt (schema %r)"
                                     % r.get("schema"))
            rec["artifacts"] = _artifacts(d, seal)
            return _strip_sealed(rec) if seal else rec
        rec["target"] = r.get("target")
        rec["collection"] = r.get("collection") or rec.get("collection")
        rec["terminal"] = r.get("terminal")
        rec["highest_certified_stage"] = r.get("highest_certified_stage")
        rec["operational_state"] = ("REFUSED" if r.get("terminal") == Terminal.REFUSED
                                    else "COMPLETE")
        det = r.get("detail") or {}
        rec["detail"] = det
        rec["refusal_class"] = det.get("refusal_class")
        rec["refusal_reason"] = det.get("reason")
        stages = r.get("stages") or []
        if not isinstance(stages, list):
            stages = []
        rec["stage"] = stages[-1].get("module") if stages else None
        for st in stages:
            ev = ((st.get("refusal") or {}).get("evidence") or {})
            if "sheet_following" in ev or "jump_fraction" in ev:
                rec["geometry_refusal"] = {
                    "verdict": "not one continuous sheet",
                    "sheet_following": ev.get("sheet_following"),
                    "jump_fraction": ev.get("jump_fraction"),
                    "step_max_ratio": ev.get("step_max_ratio"),
                    "step_median_vox": ev.get("step_median_vox"),
                    "n_edges": ev.get("n_edges"),
                }
        rec["stages"] = [{"module": s.get("module"), "status": s.get("status"),
                          "certified": s.get("certified"), "seconds": s.get("seconds"),
                          "dir": s.get("dir"),
                          "refusal_class": (s.get("refusal") or {}).get("refusal_class"),
                          "refusal_reason": (s.get("refusal") or {}).get("reason")}
                         for s in stages]
        rec["hashes"]["run.json"] = sha256_file(receipt)
        rec["environment"] = r.get("environment")
        rec.update(_from_stage_receipts(d, stages))
    else:
        hb = rec["progress"]
        rec["stage"] = hb.get("stage")
        rec["operational_state"] = {"STARTING": "RUNNING", "RUNNING": "RUNNING",
                                    "STALLED": "STALLED", "DONE": "COMPLETE",
                                    "FAILED": "REFUSED"}.get(hb.get("state"), "UNKNOWN")

    rec["artifacts"] = _artifacts(d, seal)
    return _strip_sealed(rec) if seal else rec


def _from_stage_receipts(d: Path, stages: list) -> dict:
    """Acquisition, geometry and mesh identity, read from the stage receipts themselves."""
    out: dict = {"acquisition": None, "acquisition_family": None, "geometry": None,
                 "mesh_dir": None, "mesh_sha256": None, "orientation": None,
                 "physical_window_mm": None, "detector": None}
    for st in stages:
        sd = st.get("dir")
        if not sd:
            continue
        b = Path(sd) / "bundle.json"
        if b.is_file():
            try:
                j = json.loads(b.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            bun = j.get("bundle") or {}
            topo = bun.get("topology") or {}
            frame = bun.get("frame_handshake") or {}
            out["acquisition"] = j.get("acquisition")
            out["acquisition_family"] = j.get("acquisition_family")
            out["mesh_sha256"] = bun.get("mesh_sha256")
            out["orientation"] = bun.get("orientation")
            out["physical_window_mm"] = bun.get("physical_window_mm")
            out["mesh_dir"] = ((bun.get("lineage") or {}).get("mesh_dir"))
            out["geometry"] = {
                "verdict": "sheet-following" if topo.get("sheet_following")
                           else "not one continuous sheet",
                "sheet_following": topo.get("sheet_following"),
                "vertices_inside_volume": frame.get("vertices_inside_volume"),
                "jump_fraction": topo.get("jump_fraction"),
                "step_max_ratio": topo.get("step_max_ratio"),
                "step_median_vox": topo.get("step_median_vox"),
                "n_edges": topo.get("n_edges"),
                "coverage": bun.get("coverage"),
                "grid_shape": bun.get("grid_shape"),
                "chunks_sampled": len(bun.get("chunk_identities") or []),
                "volume_shape": frame.get("volume_shape"),
            }
        plan = st.get("plan") or {}
        if plan.get("detector"):
            out["detector"] = plan["detector"]
    return out


def _artifacts(d: Path, seal: str | None) -> list:
    """Every file a run produced, with its hash."""
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.name.endswith(".tmp"):
            continue
        item = {"name": p.name, "bytes": p.stat().st_size,
                "relpath": str(p.relative_to(d))}
        viewable = p.suffix.lower() in (".png", ".jpg", ".jpeg", ".npy", ".tif", ".tiff")
        if seal and viewable:
            item["path"] = None
            item["withheld"] = "sealed"
        else:
            item["path"] = str(p)
            if p.stat().st_size <= 64 * 1024 * 1024:
                item["sha256"] = sha256_file(p)
        out.append(item)
    return out


READING_RECEIPT = "second_reading.json"

PREFLIGHT = "reading_preflight.json"


def readings_measured(d: Path) -> dict:
    """Completed readings over declared readings, both counted rather than estimated."""
    done = len(list(d.glob("*/" + READING_RECEIPT)))
    total, why = None, ("counted from %s receipts; this experiment declares no pair "
                        "count, so the total is unknown rather than assumed"
                        % READING_RECEIPT)
    pf = d / PREFLIGHT
    if pf.is_file():
        try:
            pairs = json.loads(pf.read_text(encoding="utf-8")).get("expected_pairs")
        except (ValueError, OSError):
            pairs = None
        if isinstance(pairs, int) and pairs > 0:
            total = pairs * 2
            why = ("numerator: %s receipts on disk. denominator: %d pairs the experiment "
                   "declared in %s, read in both depth orientations. Neither number is "
                   "estimated and neither is a heartbeat."
                   % (READING_RECEIPT, pairs, PREFLIGHT))
    return {"done": done, "total": total, "why": why}


def progress_from_readings(r: dict) -> dict:
    """Shape a measured reading count like a heartbeat, and say that it is not one."""
    frac = None
    if r["total"]:
        frac = min(1.0, r["done"] / float(r["total"]))
    return {"present": True, "state": "MEASURED_FROM_RECEIPTS",
            "done": r["done"], "total": r["total"], "fraction": frac,
            "unit": "readings", "age_s": None, "stale": None, "why": r["why"]}


def sealed_experiments(roots) -> list:
    """Directories carrying a blinding marker, whether or not ARGUS produced them."""
    out = []
    for root in roots:
        r = Path(root)
        if not r.is_dir():
            continue
        for d in sorted(x for x in r.iterdir() if x.is_dir()):
            marker = next((d / m for m in BLINDING_MARKERS if (d / m).is_file()), None)
            if marker is None:
                continue
            subs = sorted(x.name for x in d.iterdir() if x.is_dir())
            beat = HB.read(d)
            readings = readings_measured(d)
            progress = beat if beat.get("present") else progress_from_readings(readings)
            out.append({"run_id": d.name, "run_dir": str(d), "sealed": True,
                        "marker": str(marker),
                        "marker_text": marker.read_text(encoding="utf-8",
                                                        errors="replace")[:2000],
                        "surfaces_staged": len(subs),
                        "surfaces": subs,
                        "readings": readings,
                        "progress": progress,
                        "note": ("sealed: this experiment's decision belongs to its own "
                                 "controller and is taken once. No score, verdict or "
                                 "preview from it reaches this feed.")})
    return out


_RECORD_CACHE: dict = {}
LAST_SCAN: dict = {"seconds": None, "dirs_scanned": 0, "dirs_reused": 0, "runs": 0,
                   "listings_read": 0, "listings_reused": 0, "at": None}


_LISTING_CACHE: dict = {}


def _scan_children(p):
    """(path, mtime_ns, is_dir) for each child, from ONE scandir."""
    out = []
    try:
        import os as _os
        with _os.scandir(p) as it:
            for e in it:
                try:
                    out.append((Path(e.path), e.stat().st_mtime_ns, e.is_dir()))
                except OSError:
                    continue
    except OSError:
        return []
    out.sort(key=lambda t: str(t[0]))
    return out


def _listdir_cached(p, known_mtime=None, *, bypass=False):
    """Children of a directory, reused while its mtime is unchanged."""
    mt = known_mtime
    if mt is None:
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            return []
    key = str(p)
    hit = _LISTING_CACHE.get(key)
    if not bypass and hit is not None and hit[0] == mt:
        LAST_SCAN["listings_reused"] += 1
        return hit[1]
    kids = _scan_children(p)
    LAST_SCAN["listings_read"] += 1
    _LISTING_CACHE[key] = (mt, kids)
    return kids


def _cached_run_record(d, *, now, collection):
    """run_record for a directory, reused when its mtime has not moved."""
    try:
        mt = d.stat().st_mtime_ns
    except OSError:
        mt = None
    key = str(d)
    hit = _RECORD_CACHE.get(key)
    if hit is not None and mt is not None and hit[0] == mt:
        LAST_SCAN["dirs_reused"] += 1
        return hit[1]
    rec = run_record(d, now=now, collection=collection)
    LAST_SCAN["dirs_scanned"] += 1
    if mt is not None:
        _RECORD_CACHE[key] = (mt, rec)
    return rec


def observatory(roots, *, now=None, collections=None, force: bool = False) -> dict:
    """Every run under every root, plus the counts a dashboard needs."""
    now = time.time() if now is None else now
    collections = collections or {}
    _t0 = time.perf_counter()
    LAST_SCAN.update(dirs_scanned=0, dirs_reused=0,
                     listings_read=0, listings_reused=0)
    runs, missing = [], []
    for root in roots:
        r = Path(root)
        if not r.is_dir():
            missing.append(str(r))
            continue
        for d, _dmt, _dis in _listdir_cached(r, bypass=force):
            if not d.is_dir():
                continue
            if (d / "run.json").is_file() or (d / HB.FILENAME).is_file():
                runs.append(_cached_run_record(d, now=now,
                                               collection=collections.get(r.name)))
            for sub, _smt, _sis in _listdir_cached(d, known_mtime=_dmt):
                if not _sis:
                    continue
                if (sub / "run.json").is_file() or (sub / HB.FILENAME).is_file():
                    runs.append(_cached_run_record(
                        sub, now=now, collection=collections.get(r.name)))
    LAST_SCAN.update(seconds=round(time.perf_counter() - _t0, 3),
                     runs=len(runs), at=now)
    foreign = [r for r in runs if r.get("foreign")]
    runs = [r for r in runs if not r.get("foreign")]
    by = {}
    for r in runs:
        by[r["operational_state"]] = by.get(r["operational_state"], 0) + 1
    cert = {}
    for r in runs:
        k = r["highest_certified_stage"] or "NONE"
        cert[k] = cert.get(k, 0) + 1
    sealed = sealed_experiments(roots)
    return {"schema": FEED_SCHEMA, "generated_at": now, "runs": runs,
            "sealed_experiments": sealed,
            "counts": {"operational": by, "highest_certified": cert,
                       "sealed": sum(1 for r in runs if r["blinding"]["sealed"])
                                 + len(sealed),
                       "total": len(runs),
                       "foreign_receipts_ignored": len(foreign)},
            "missing_roots": missing,
            "note": ("every field is read from a receipt or a structured heartbeat; no "
                     "progress is estimated and no state is inferred from a live process")}
