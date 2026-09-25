"""The advisor: what to set up next, argued from the record rather than from opinion."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

TOPICS: dict[str, dict] = {
    "gpu_absent": {
        "title": "Reading at scale without a GPU",
        "terms": ["vram", "gpu", "inference", "batch"],
        "when": "no accelerator is visible",
    },
    "disk_tight": {
        "title": "Staging volume material with little free space",
        "terms": ["chunk cache", "disk", "staging", "evict"],
        "when": "a data root reports little free space",
    },
    "detector_unqualified": {
        "title": "Reading an acquisition the detector was never qualified on",
        "terms": ["control", "qualified", "acquisition", "calibration"],
        "when": "no known-ink control exists at the target acquisition",
    },
    "geometry_gate": {
        "title": "Meshes that look like sheets and are not",
        "terms": ["winding", "sheet", "jump", "growth"],
        "when": "a geometry stage refused a mesh",
    },
    "inference_geometry": {
        "title": "The reading configuration itself",
        "terms": ["stride", "overlap-add", "gaussian", "coverage"],
        "when": "always, before trusting any map",
    },
}


def _record() -> dict:
    """The findings record, or an empty one with the reason."""
    try:
        import corpus_query

        return corpus_query.load()
    except Exception as e:
        return {"__error__": "%s: %s" % (type(e).__name__, str(e)[:160])}


def retracted_ids(record: dict) -> set:
    """Every finding some OTHER finding withdrew."""
    out = set()
    for ident, f in record.items():
        if ident.startswith("__"):
            continue
        out |= set(f.get("retracts") or [])
        status = (f.get("status") or "").upper()
        if any(w in status for w in ("RETRACT", "WITHDRAWN", "INVALIDATED", "SUPERSEDED")):
            out.add(ident)
        head = "%s %s" % (f.get("title") or "", f.get("status") or "")
        for verb, target in re.findall(
            r"\b(WITHDRAWS|RETRACTS|CORRECTS|SUPERSEDES|INVALIDATES)\s+([A-Z]{1,4}-\d+)",
            head.upper(),
        ):
            del verb
            out.add(target)
    return out


def _search(record: dict, terms: list[str], limit: int = 4,
            retracted: set | None = None) -> list:
    """Findings whose title mentions these terms, most load-bearing first."""
    retracted = retracted if retracted is not None else retracted_ids(record)
    hits = []
    for ident, f in record.items():
        if ident.startswith("__") or ident in retracted:
            continue
        title = f.get("title") or ""
        hay = title.lower()
        score = sum(3 for t in terms if t in hay)
        if not score:
            continue
        if f.get("has_check"):
            score += 4
        hits.append((score, ident, title, f))
    hits.sort(key=lambda h: (-h[0], -_num(h[1])))
    out = []
    for score, ident, title, f in hits[:limit]:
        led = f.get("ledger") or {}
        out.append({
            "id": ident,
            "title": title[:260],
            "status": f.get("status", "")[:180],
            "checkable": bool(f.get("has_check")),
            "command": (led.get("command") or "")[:400],
            "score": score,
            "read_it": "python scripts/corpus_query.py show %s" % ident,
        })
    return out


def _num(ident: str) -> int:
    m = re.search(r"(\d+)", ident)
    return int(m.group(1)) if m else 0


def advise(snapshot: dict | None = None) -> dict:
    """Measured conditions, each with what the record already says about it."""
    from argus.core import oversight as O

    snap = snapshot or O.snapshot(live_sources=False)
    record = _record()
    err = record.get("__error__")

    retracted = set() if err else retracted_ids(record)
    conditions = []
    gpu = snap["gpu"]
    if not gpu.get("present"):
        conditions.append(("gpu_absent", gpu.get("why", "no accelerator reported")))
    else:
        for c in gpu.get("cards", []):
            if (c.get("vram_total_mib") or 0) < 8000:
                conditions.append(
                    ("gpu_absent",
                     "%s has %s MiB of VRAM, which is below what a full-segment read wants"
                     % (c["name"], c["vram_total_mib"])))
    seen_disks = set()
    for d in snap["disks"]:
        if not d.get("present") or (d.get("free_gib") or 0) >= 200:
            continue
        key = (d.get("total_gib"), d.get("free_gib"))
        if key in seen_disks:
            continue
        seen_disks.add(key)
        conditions.append(
            ("disk_tight", "%s has %s GiB free of %s"
             % (d["root"], d["free_gib"], d.get("total_gib"))))
    conditions.append(
        ("detector_unqualified",
         "a reading at an acquisition with no known-ink control is refused by VIGILES "
         "before inference begins"))
    conditions.append(
        ("inference_geometry",
         "the reading geometry is pinned in the detector's own constants and asserted "
         "from the live values, not from the file's own text"))

    items = []
    for key, measured in conditions:
        topic = TOPICS[key]
        found = [] if err else _search(record, topic["terms"], retracted=retracted)
        items.append({
            "topic": key,
            "title": topic["title"],
            "measured": measured,
            "record_says": found,
            "note": ("nothing in the record speaks to this condition; treat any advice "
                     "here as untested" if not found and not err else None),
        })

    return {
        "schema": "argus-advisor-v1",
        "record_available": err is None,
        "record_error": err,
        "record_size": 0 if err else len([k for k in record if not k.startswith("__")]),
        "retracted_excluded": 0 if err else len(retracted),
        "conditions": items,
        "acts": False,
        "note": ("every item cites a finding, and retracted findings are excluded. This "
                 "advisor retrieves and ranks; it does not summarise, and it never "
                 "installs, downloads or configures anything."),
    }
