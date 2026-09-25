"""The campaign heartbeat: what is being worked on, written down rather than inferred."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import os
import tempfile
import time
from pathlib import Path

HEARTBEAT = Path(_argus_public_path('home', 'runs/CAMPAIGN_HEARTBEAT.json'))

STALE_FACTOR = 2.5

FORBIDDEN = ("auc", "score", "candidate_preview", "map", "prediction", "metric", "ci95",
             "heatmap", "sealed_value")


def beat(stage: str, *, done: int = 0, total: int | None = None, note: str = "",
         interval_s: float = 900.0, lane: str = "", path: Path = HEARTBEAT) -> dict:
    """Write the current state."""
    for k in FORBIDDEN:
        if k in (note or "").lower().replace(" ", "_"):
            raise ValueError("a heartbeat note may not carry %r; status lines are not a "
                             "side channel for sealed values" % k)
    now = time.time()
    prev = read(path, enforce_stale=False) or {}
    started = prev.get("started_utc") if prev.get("stage") == stage else None
    started_at = prev.get("started_at") if prev.get("stage") == stage else None
    rec = {
        "schema": "argus-campaign-heartbeat-v1",
        "stage": stage, "lane": lane,
        "done": int(done), "total": (int(total) if total is not None else None),
        "note": note,
        "interval_s": float(interval_s),
        "beat_at": now,
        "beat_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "started_at": started_at or now,
        "started_utc": started or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    elapsed = now - rec["started_at"]
    if rec["done"] > 0 and elapsed > 0:
        rate = rec["done"] / elapsed
        rec["units_per_hour"] = round(rate * 3600.0, 2)
        if rec["total"]:
            left = max(rec["total"] - rec["done"], 0)
            rec["eta_seconds_range"] = [int(left / (rate * 1.6)), int(left / (rate * 0.6))]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, path)
    return rec


def read(path: Path = HEARTBEAT, *, enforce_stale: bool = True) -> dict | None:
    """Read the beat and, unless told otherwise, decide whether it is still credible."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not enforce_stale:
        return rec
    age = time.time() - float(rec.get("beat_at") or 0)
    rec["age_s"] = round(age, 1)
    limit = float(rec.get("interval_s") or 900.0) * STALE_FACTOR
    rec["stale_after_s"] = limit
    rec["state"] = "STALLED" if age > limit else "RUNNING"
    rec["state_basis"] = (
        "the last beat is %.0fs old against a declared interval of %.0fs; a writer that "
        "stopped renewing is not still working" % (age, rec.get("interval_s") or 0)
        if rec["state"] == "STALLED" else
        "beat renewed within its declared interval")
    if rec.get("total"):
        rec["progress_pct"] = round(100.0 * rec["done"] / rec["total"], 1)
    else:
        rec["progress_pct"] = None
        rec["progress_note"] = ("total unknown, so no percentage is shown; %d done"
                                % rec.get("done", 0))
    return rec


def clear(path: Path = HEARTBEAT) -> None:
    """Explicitly end a campaign stage."""
    try:
        Path(path).unlink()
    except OSError:
        pass
