"""Heavy storage work yields to science reads."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import pathlib
import time

from argus.core import owned_process_tree as OPT

CONTRACT = "argus-science-arbiter-v1"

SCIENCE_JOBS = (
  "argus-exploratory-gpu",
  "argus-science-read",
)

HEAVY_STORAGE_WORK = (
  "ARCHIVE_TAR_CREATE",
  "BULK_SHA_SCAN",
  "DATASET_HYDRATION",
  "LARGE_DATA_DRIVE_READ",
  "CHUNK_CACHE_SWEEP",
)

RUN = "RUN"
YIELD = "YIELD"
UNKNOWN_SO_YIELD = "UNKNOWN_SO_YIELD"


class ArbiterRefusal(RuntimeError):
    """Raised when a heavy job would start against an active science lease."""


def _read_lease(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


CONVEYOR_CATALOGUE = pathlib.Path(_argus_public_path('home', 'state/storage/catalog.db'))

HEARTBEAT_STALE_AFTER_S = 600


def active_conveyor() -> dict:
    """What the storage conveyor is doing right now, read from ITS authority."""
    out = {"catalogue": str(CONVEYOR_CATALOGUE), "readable": False,
           "daemon": None, "leases": [], "why": ""}
    if not CONVEYOR_CATALOGUE.is_file():
        out["why"] = "no conveyor catalogue on this machine"
        return out
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % CONVEYOR_CATALOGUE.as_posix(), uri=True,
                              timeout=5.0)
        try:
            for row in con.execute("select pid, generation, heartbeat, state, current_action "
                                   "from daemon limit 1"):
                out["daemon"] = {"pid": row[0], "generation": row[1], "heartbeat": row[2],
                                 "state": row[3], "action": row[4]}
            for row in con.execute("select resource, owner_pid, action, acquired "
                                   "from leases"):
                out["leases"].append({"resource": row[0], "owner_pid": row[1],
                                      "action": row[2], "acquired": row[3]})
            out["readable"] = True
        finally:
            con.close()
    except Exception as exc:
        out["why"] = "catalogue unreadable (%s)" % type(exc).__name__
        return out

    d = out["daemon"] or {}
    alive = bool(d.get("pid")) and OPT._alive(int(d["pid"]))
    state = str(d.get("state", "")).upper()
    out["busy"] = alive and (
        state in ("WORKING", "ARCHIVING", "VERIFYING", "HYDRATING") or bool(out["leases"]))
    out["holder_alive"] = alive

    out["heartbeat_age_s"] = None
    hb = d.get("heartbeat")
    if hb:
        try:
            import datetime as _dt
            when = _dt.datetime.strptime(hb, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=_dt.timezone.utc)
            out["heartbeat_age_s"] = round(
                (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds())
        except (ValueError, TypeError):
            pass

    age = out["heartbeat_age_s"]
    if not d:
        out["health"] = "NO_DAEMON_RECORD"
    elif not alive:
        out["health"] = "DEAD"
        out["alert"] = ("the conveyor daemon (pid %s, generation %s) is GONE while the "
                        "catalogue still records state %s. Its last heartbeat was %s seconds "
                        "ago. Work it had approved will not advance until it is restarted, and "
                        "nothing about a quiet disk will say so."
                        % (d.get("pid"), d.get("generation"), state or "UNKNOWN",
                           age if age is not None else "an unknown number of"))
    elif age is not None and age > HEARTBEAT_STALE_AFTER_S:
        out["health"] = "STALLED"
        out["alert"] = ("the conveyor daemon is alive but has not updated its heartbeat for %d "
                        "seconds (state %s). Alive and progressing are different." % (age, state))
    elif out["busy"]:
        out["health"] = "WORKING"
    else:
        out["health"] = "IDLE"
    return out


def active_science() -> dict:
    """Which science jobs hold a live lease right now, and how confident we are of that."""
    out = {"contract": CONTRACT, "active": [], "unreadable": [], "stale": [],
           "checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    d = OPT.LEASE_DIR
    if not d.is_dir():
        out["lease_dir_present"] = False
        return out
    out["lease_dir_present"] = True
    for job in SCIENCE_JOBS:
        path = d / ("%s.json" % job)
        if not path.is_file():
            continue
        doc = _read_lease(path)
        if doc is None:
            out["unreadable"].append(job)
            continue
        pid = doc.get("pid")
        owner = doc.get("owner_identity") or {}
        if not pid or not OPT._alive(int(pid)):
            out["stale"].append({"job": job, "pid": pid, "why": "holder is gone"})
            continue
        if owner and not OPT.is_same_process(owner, int(pid)):
            out["stale"].append({"job": job, "pid": pid,
                                 "why": "pid is alive but is a different process; the number "
                                        "was recycled"})
            continue
        out["active"].append({"job": job, "pid": int(pid),
                              "command": (owner.get("command_line")
                                          or doc.get("command") or "")[:160],
                              "acquired_utc": doc.get("acquired_utc"),
                              "identity_known": bool(owner.get("identity_known"))})
    return out


def may_run(work: str) -> dict:
    """May this heavy storage job run right now?"""
    if work not in HEAVY_STORAGE_WORK:
        raise ArbiterRefusal(
          "unknown storage work %r. The competing kinds are named individually (%s) precisely "
          "so a new one has to be declared rather than assumed harmless."
          % (work, ", ".join(HEAVY_STORAGE_WORK)))
    st = active_science()
    if st["unreadable"]:
        return {"work": work, "decision": UNKNOWN_SO_YIELD,
                "why": "lease(s) %s could not be read, so whether science is running is "
                       "unknown. An unreadable lease yields: an hour of archiving is "
                       "recoverable and a contended fold is not." % st["unreadable"],
                "state": st}
    conv = active_conveyor()
    if conv.get("busy"):
        d = conv.get("daemon") or {}
        return {"work": work, "decision": YIELD,
                "why": "the storage conveyor is already working (pid %s, %s, %s). Two bulk "
                       "walks of one disk do not run twice as fast; they run a great deal "
                       "slower than one."
                       % (d.get("pid"), d.get("state"), str(d.get("action"))[:60]),
                "resume_when": "the conveyor finishes its current unit",
                "conveyor": conv, "state": st}
    if conv.get("readable") is False and conv.get("why"):
        return {"work": work, "decision": UNKNOWN_SO_YIELD,
                "why": "the conveyor's own catalogue could not be read (%s), so whether a bulk "
                       "storage job is already running is UNKNOWN." % conv["why"],
                "conveyor": conv, "state": st}

    if st["active"]:
        return {"work": work, "decision": YIELD,
                "why": "science is active: %s. On a spinning disk a concurrent bulk walk "
                       "turns a sequential read into a seek-bound one."
                       % [a["job"] for a in st["active"]],
                "resume_when": "the science lease is released; no operator action is needed",
                "state": st}
    return {"work": work, "decision": RUN,
            "why": "no live science lease. %s" % (
              "Stale leases ignored: %s" % st["stale"] if st["stale"]
              else "No stale leases either."),
            "state": st}


def require(work: str) -> dict:
    """Refuse rather than return, for callers that are about to start the work regardless."""
    v = may_run(work)
    if v["decision"] != RUN:
        raise ArbiterRefusal(
          "%s may not start: %s (%s)" % (work, v["decision"], v["why"]))
    return v


def wait_for_quiet(work: str, *, poll_s: int = 60, max_wait_s: int = 0, _sleep=time.sleep,
                   _now=time.monotonic) -> dict:
    """Block until the heavy work may run."""
    t0 = _now()
    polls = 0
    while True:
        v = may_run(work)
        polls += 1
        if v["decision"] == RUN:
            v["waited_s"] = round(_now() - t0, 1)
            v["polls"] = polls
            return v
        if max_wait_s and (_now() - t0) >= max_wait_s:
            v["waited_s"] = round(_now() - t0, 1)
            v["polls"] = polls
            v["gave_up"] = True
            return v
        _sleep(poll_s)


def status() -> dict:
    """One call for a UI or a status surface: what science is live, and what is held back."""
    st = active_science()
    _conv = active_conveyor()
    return {
      "contract": CONTRACT,
      "science": st,
      "conveyor": _conv,
      "conveyor_health": _conv.get("health"),
      "conveyor_alert": _conv.get("alert"),
      "storage_work": {w: may_run(w)["decision"] for w in HEAVY_STORAGE_WORK},
      "policy": "heavy storage work YIELDS to science reads; it is never disabled, so it "
                "resumes without anybody remembering to re-enable it.",
      "fail_closed_direction": "an unknown state yields. Losing archiving time is recoverable; "
                               "a fold contending for a spinning disk is not.",
    }
