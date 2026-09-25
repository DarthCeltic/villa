"""Structured progress, written by whoever is doing the work."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

FILENAME = "heartbeat.json"

STALE_AFTER_S = 600.0


class Heartbeat:
    """Write-side."""

    def __init__(self, d: Path, *, unit: str, total: int | None = None,
                 stage: str = "", clock=time.time):
        self.path = Path(d) / FILENAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.unit, self.total, self.stage, self.clock = unit, total, stage, clock
        self.done = 0
        self.started = clock()
        self.state = "STARTING"
        self.detail = ""
        self.write()

    def advance(self, n: int = 1, *, detail: str = ""):
        self.done += n
        self.state = "RUNNING"
        self.detail = detail
        self.write()

    def set_total(self, total: int | None):
        """Called once the work is enumerated."""
        self.total = total
        self.write()

    def finish(self, state: str = "DONE", *, detail: str = ""):
        self.state, self.detail = state, detail
        self.write()

    def write(self):
        rec = {"schema": "argus-heartbeat-v1", "state": self.state, "stage": self.stage,
               "done": int(self.done), "total": self.total, "unit": self.unit,
               "detail": self.detail[:200], "pid": os.getpid(),
               "started_at": self.started, "updated_at": self.clock()}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        tmp.replace(self.path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.finish("FAILED" if exc_type else "DONE",
                    detail="" if not exc_type else "%s: %s" % (exc_type.__name__, str(exc)[:150]))
        return False


def read(d: Path, *, now=None, stale_after: float = STALE_AFTER_S) -> dict:
    """Read-side."""
    p = Path(d) / FILENAME
    now = time.time() if now is None else now
    if not p.is_file():
        return {"present": False, "state": "UNKNOWN", "done": None, "total": None,
                "fraction": None, "age_s": None, "stale": None,
                "why": "this worker writes no structured heartbeat, so its progress is "
                       "unknown rather than estimated"}
    try:
        r = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return {"present": True, "state": "UNREADABLE", "done": None, "total": None,
                "fraction": None, "age_s": None, "stale": None, "why": str(e)[:150]}
    age = max(0.0, now - float(r.get("updated_at", 0)))
    total = r.get("total")
    done = r.get("done")
    frac = (done / total) if (isinstance(total, int) and total > 0
                              and isinstance(done, int)) else None
    stale = age > stale_after and r.get("state") in ("STARTING", "RUNNING")
    return {"present": True, "state": ("STALLED" if stale else r.get("state", "UNKNOWN")),
            "reported_state": r.get("state"), "stage": r.get("stage"),
            "done": done, "total": total, "unit": r.get("unit"),
            "fraction": frac, "age_s": round(age, 1), "stale": bool(stale),
            "pid": r.get("pid"), "detail": r.get("detail"),
            "started_at": r.get("started_at"), "updated_at": r.get("updated_at"),
            "why": None if total is not None else
                   "the worker has not enumerated its work, so there is no denominator"}
