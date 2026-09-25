"""What is ACTUALLY happening on this machine, derived from every canonical root."""
from __future__ import annotations

import json
import os
import pathlib
import time

SCHEMA = "argus-activity-v1"

CLASSES = (
    "ARGUS_SCIENTIFIC_JOB",
    "ACQUISITION_DOWNLOAD",
    "ARCHIVE_STORAGE",
    "EXTERNAL_TRAINING",
    "GPU_COMPUTE",
    "CPU_BACKGROUND",
)

CLASS_MEANS = {
    "ARGUS_SCIENTIFIC_JOB":
        "a scoring, training or evaluation pass owned by ARGUS itself, evidenced by a "
        "worker-written progress receipt or by the side-work governor's live view of it",
    "ACQUISITION_DOWNLOAD":
        "volume or segment bytes arriving from an upstream data source",
    "ARCHIVE_STORAGE":
        "bytes being written to or read from long-term storage (archive upload, cache "
        "materialisation, mirror sync)",
    "EXTERNAL_TRAINING":
        "work running on hardware that is not this machine -- a pod, a hosted job runner -- "
        "which this machine can only know about through a receipt",
    "GPU_COMPUTE":
        "the accelerator is busy, whoever owns it. This is a hardware observation and makes "
        "no claim about which job it belongs to",
    "CPU_BACKGROUND":
        "sustained CPU load with no other class claiming it",
}

STATES = ("ACTIVE", "NONE_OBSERVED", "UNKNOWN")

VERDICTS = ("ACTIVE", "IDLE_OBSERVED", "ACTIVITY_UNKNOWN")

FRESH_S = 900.0

STALL_S = 3600.0

GPU_BUSY_MIB = 1500
GPU_BUSY_PCT = 50

CPU_BUSY_PCT = 35.0

PROGRESS_NAMES = ("PROGRESS.json", "HEARTBEAT.json", "heartbeat.json")

MAX_DEPTH = 3


class _Root:
    """One declared root and whether it could actually be read."""

    __slots__ = ("var", "path", "readable", "why")

    def __init__(self, var: str, path: pathlib.Path):
        self.var, self.path = var, pathlib.Path(path)
        self.readable, self.why = self._probe()

    def _probe(self):
        try:
            if not self.path.is_dir():
                return False, "the declared root is not a directory on this machine"
            os.scandir(self.path).close()
            return True, None
        except OSError as exc:
            return False, "%s while opening the root" % type(exc).__name__

    def render(self) -> dict:
        return {"root": self.var, "readable": self.readable, "why": self.why}


def _roots() -> list:
    """Every root that could carry evidence of work, by contract name."""
    from argus.core import paths

    out = []
    seen = set()
    for var, p in (("ARGUS_REPO/artifacts", paths.repo("artifacts")),
                   ("ARGUS_LEGACY_ROOT/artifacts", paths.legacy("artifacts")),
                   ("ARGUS_RUNS_ROOT", paths.root("ARGUS_RUNS_ROOT")),
                   ("ARGUS_SCIENCE_DATA_ROOT", paths.root("ARGUS_SCIENCE_DATA_ROOT"))):
        rp = pathlib.Path(p)
        if rp in seen:
            continue
        seen.add(rp)
        out.append(_Root(var, rp))
    return out


def _iter_progress(root: pathlib.Path, now: float) -> list:
    """Every worker-written progress receipt under one root, newest first."""
    found, blocked = [], []

    def walk(d: pathlib.Path, depth: int):
        if depth > MAX_DEPTH:
            return
        try:
            entries = list(os.scandir(d))
        except OSError as exc:
            blocked.append("%s at depth %d" % (type(exc).__name__, depth))
            return
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    walk(pathlib.Path(e.path), depth + 1)
                elif e.name in PROGRESS_NAMES:
                    st = e.stat()
                    found.append({"key": _key(root, pathlib.Path(e.path)),
                                  "age_s": round(now - st.st_mtime, 1),
                                  "path": pathlib.Path(e.path)})
            except OSError as exc:
                blocked.append(type(exc).__name__)
    walk(root, 0)
    found.sort(key=lambda r: r["age_s"])
    return [found, blocked]


def _key(root: pathlib.Path, p: pathlib.Path) -> str:
    """A stable identifier for a receipt that is NOT a local absolute path."""
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.name


def _read_json(p: pathlib.Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_PROGRESS_FIELDS = ("segment", "blocks_done", "blocks_total", "percent", "eta_min",
                    "eta_utc", "elapsed_min", "current_orientation", "last_block_s",
                    "mean_s_per_block", "observed_utc", "state", "stage", "done", "total",
                    "unit")


def _scientific(now: float, roots: list, ledger: dict | None,
                permit: dict | None = None) -> dict:
    """Is an ARGUS scientific job in flight?"""
    witnesses, evidence, unknown = [], [], []

    try:
        if permit is None:
            from argus.core import side_permit as SP
            permit = SP.permit_state()
        st = permit
        seg = st.get("segment")
        alive = st.get("scorer_alive")
        if alive and seg:
            witnesses.append("side_work_permit")
            evidence.append({
                "witness": "side_work_permit",
                "what": "the side-work governor reports the scorer alive",
                "segment": seg,
                "blocks_seen": st.get("blocks_seen"),
                "recent_block_s": st.get("recent_block_s"),
                "permit_age_s": st.get("age_s"),
                "observed_by": "argus.core.side_permit.permit_state",
            })
        elif st.get("reason") in ("NO_PERMIT_FILE", "PERMIT_STALE", "PERMIT_UNPARSEABLE"):
            unknown.append("the side-work governor is %s, so it can neither confirm nor "
                           "deny a running scorer" % st.get("reason"))
        else:
            evidence.append({"witness": "side_work_permit",
                             "what": "the governor is live and reports no scorer",
                             "observed_by": "argus.core.side_permit.permit_state"})
    except Exception as exc:
        unknown.append("the side-work governor could not be read (%s)"
                       % type(exc).__name__)

    for r in roots:
        if not r.readable:
            unknown.append("%s could not be read, so any work recorded under it is "
                           "unaccounted for" % r.var)
            continue
        found, blocked = _iter_progress(r.path, now)
        for b in blocked:
            unknown.append("part of %s could not be walked (%s)" % (r.var, b))
        for f in found[:6]:
            doc = _read_json(f["path"]) or {}
            fresh = f["age_s"] <= FRESH_S
            row = {"witness": "progress_receipt",
                   "root": r.var,
                   "receipt": f["key"],
                   "age_s": f["age_s"],
                   "fresh": fresh,
                   "observed_by": "argus.core.activity._iter_progress"}
            for k in _PROGRESS_FIELDS:
                if isinstance(doc, dict) and k in doc:
                    row[k] = doc[k]
            if fresh:
                witnesses.append("progress_receipt")
                evidence.append(row)
            elif f["age_s"] <= STALL_S:
                witnesses.append("progress_receipt_stalling")
                row["note"] = ("this receipt has stopped advancing. A worker that died "
                               "looks exactly like one that is working, unless the age is "
                               "shown -- so it is shown.")
                evidence.append(row)

    if ledger is None:
        unknown.append("the governed job service was not consulted")
    elif ledger.get("readable"):
        n = ledger.get("running") or 0
        row = {"witness": "job_ledger", "running": n,
               "abandoned_running_records": ledger.get("abandoned_running_records"),
               "newest_running_age_s": ledger.get("newest_running_age_s"),
               "records": ledger.get("n"),
               "observed_by": "argus.core.activity.job_ledger"}
        if n:
            witnesses.append("job_ledger")
        else:
            row["what"] = ("the governed job ledger has no job whose record is still being "
                           "touched. That is a statement about this ledger only.")
        evidence.append(row)
    else:
        unknown.append("the governed job ledger is unreadable (%s)"
                       % (ledger.get("why") or "no reason given"))

    if witnesses:
        state = "ACTIVE"
    elif unknown:
        state = "UNKNOWN"
    else:
        state = "NONE_OBSERVED"
    return {"state": state, "witnesses": sorted(set(witnesses)), "evidence": evidence,
            "unknown_because": unknown}


def job_ledger() -> dict:
    """The governed job ledger, read directly off disk."""
    try:
        from argus.core import actions as A
        d = pathlib.Path(A.JOBS)
        if not d.is_dir():
            return {"readable": True, "running": 0, "n": 0,
                    "note": "the ledger directory does not exist yet; no job has ever run"}
        now = time.time()
        running, abandoned, n = 0, 0, 0
        newest = None
        for e in os.scandir(d):
            if not e.is_dir():
                continue
            n += 1
            doc = _read_json(pathlib.Path(e.path) / "job.json") or {}
            if str(doc.get("state", "")).upper() not in ("RUNNING", "STARTED", "PENDING"):
                continue
            age = now - e.stat().st_mtime
            if age <= STALL_S:
                running += 1
                newest = age if newest is None else min(newest, age)
            else:
                abandoned += 1
        return {"readable": True, "running": running, "abandoned_running_records": abandoned,
                "n": n, "newest_running_age_s": None if newest is None else round(newest, 1),
                "stall_s": STALL_S}
    except Exception as exc:
        return {"readable": False, "why": type(exc).__name__}


def _receipt_class(name: str, roots: list, now: float, patterns: tuple) -> dict:
    """A class whose only witness is a receipt directory matching `patterns`."""
    hits, unknown = [], []
    any_readable = False
    for r in roots:
        if not r.readable:
            unknown.append("%s unreadable" % r.var)
            continue
        any_readable = True
        try:
            for e in os.scandir(r.path):
                if not e.is_dir():
                    continue
                low = e.name.lower()
                if any(p in low for p in patterns):
                    age = now - e.stat().st_mtime
                    if age <= FRESH_S:
                        hits.append({"witness": "receipt_directory", "root": r.var,
                                     "receipt": e.name, "age_s": round(age, 1)})
        except OSError as exc:
            unknown.append("%s (%s)" % (r.var, type(exc).__name__))
    if hits:
        return {"state": "ACTIVE", "witnesses": ["receipt_directory"], "evidence": hits,
                "unknown_because": unknown}
    reason = ["no receipt under any readable root has been touched in the last %d s. That "
              "rules out a receipt-writing %s; it does not rule out one that writes only "
              "when it finishes." % (int(FRESH_S), name.lower().replace("_", " "))]
    if not any_readable:
        reason = ["no root could be read, so this class was not observed at all"]
    return {"state": "UNKNOWN", "witnesses": [], "evidence": [],
            "unknown_because": reason + unknown}


def _gpu(resources: dict | None) -> dict:
    """The accelerator, as a hardware observation with no claim about ownership."""
    if resources is None:
        return {"state": "UNKNOWN", "witnesses": [], "evidence": [],
                "unknown_because": ["no accelerator probe was supplied to this derivation"]}
    g = resources.get("gpu")
    if not g:
        return {"state": "UNKNOWN", "witnesses": [], "evidence": [],
                "unknown_because": ["the accelerator did not answer a query. A card that "
                                    "cannot be queried is not a card that is idle."]}
    busy = (g.get("used_mib") or 0) > GPU_BUSY_MIB or (g.get("util_pct") or 0) > GPU_BUSY_PCT
    row = {"witness": "accelerator_query", "used_mib": g.get("used_mib"),
           "total_mib": g.get("total_mib"), "util_pct": g.get("util_pct"),
           "threshold_mib": GPU_BUSY_MIB, "threshold_pct": GPU_BUSY_PCT,
           "observed_by": "nvidia-smi"}
    return {"state": "ACTIVE" if busy else "NONE_OBSERVED",
            "witnesses": ["accelerator_query"] if busy else [],
            "evidence": [row], "unknown_because": []}


def _cpu(resources: dict | None) -> dict:
    if resources is None or resources.get("cpu_pct") is None:
        return {"state": "UNKNOWN", "witnesses": [], "evidence": [],
                "unknown_because": ["no CPU probe answered"]}
    pct = resources["cpu_pct"]
    busy = pct > CPU_BUSY_PCT
    row = {"witness": "cpu_probe", "cpu_pct": pct, "threshold_pct": CPU_BUSY_PCT,
           "observed_by": "psutil.cpu_percent"}
    return {"state": "ACTIVE" if busy else "NONE_OBSERVED",
            "witnesses": ["cpu_probe"] if busy else [], "evidence": [row],
            "unknown_because": []}


def derive(*, resources: dict | None = None, ledger: dict | None = None,
           permit: dict | None = None, roots: list | None = None,
           now: float | None = None) -> dict:
    """The whole activity picture."""
    now = time.time() if now is None else now
    roots = _roots() if roots is None else roots
    runs_only = [r for r in roots if r.var in ("ARGUS_RUNS_ROOT", "ARGUS_REPO/artifacts",
                                               "ARGUS_LEGACY_ROOT/artifacts")]

    classes = {
        "ARGUS_SCIENTIFIC_JOB": _scientific(now, runs_only, ledger, permit),
        "ACQUISITION_DOWNLOAD": _receipt_class(
            "ACQUISITION_DOWNLOAD", runs_only, now,
            ("acquis", "download", "fetch", "ingest", "stage_asset")),
        "ARCHIVE_STORAGE": _receipt_class(
            "ARCHIVE_STORAGE", runs_only, now,
            ("archive", "upload", "mirror", "sync", "drive")),
        "EXTERNAL_TRAINING": _receipt_class(
            "EXTERNAL_TRAINING", runs_only, now,
            ("pod", "hf_job", "hfjob", "remote_train", "external")),
        "GPU_COMPUTE": _gpu(resources),
        "CPU_BACKGROUND": _cpu(resources),
    }
    for k, v in classes.items():
        v["means"] = CLASS_MEANS[k]

    active = sorted(k for k, v in classes.items() if v["state"] == "ACTIVE")
    unknown = sorted(k for k, v in classes.items() if v["state"] == "UNKNOWN")
    unreadable = [r.render() for r in roots if not r.readable]

    if active:
        verdict = "ACTIVE"
    elif unknown or unreadable:
        verdict = "ACTIVITY_UNKNOWN"
    else:
        verdict = "IDLE_OBSERVED"

    if verdict == "ACTIVE":
        headline = "Work in flight: " + ", ".join(a.replace("_", " ").lower() for a in active)
    elif verdict == "ACTIVITY_UNKNOWN":
        headline = ("Activity unknown -- %d of %d classes could not be observed"
                    % (len(unknown), len(CLASSES)))
    else:
        headline = ("Idle: every one of the %d activity classes was observable and none "
                    "reported work" % len(CLASSES))

    return {
        "schema": SCHEMA,
        "observed_epoch": now,
        "observed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "verdict": verdict,
        "headline": headline,
        "active_classes": active,
        "unknown_classes": unknown,
        "classes": classes,
        "roots": [r.render() for r in roots],
        "unreadable_roots": unreadable,
        "thresholds": {"fresh_s": FRESH_S, "stall_s": STALL_S,
                       "gpu_busy_mib": GPU_BUSY_MIB, "gpu_busy_pct": GPU_BUSY_PCT,
                       "cpu_busy_pct": CPU_BUSY_PCT, "max_depth": MAX_DEPTH},
        "rule": ("IDLE_OBSERVED requires every declared class to have been observable. One "
                 "unreadable root or one unwired probe yields ACTIVITY_UNKNOWN. Nothing in "
                 "this derivation can reach idle by failing."),
        "read_only": True,
    }


def _selftest() -> int:
    """Checks the ONE property that matters: nothing reaches idle by failing."""
    bad = 0

    missing = _Root("ARGUS_TEST_ABSENT", pathlib.Path("Z:/no/such/root/at/all"))
    if missing.readable:
        print("FAIL: a non-existent root reported readable"); bad += 1
    dead = {"allow": False, "reason": "NO_PERMIT_FILE",
            "detail": "the governor is not running"}
    sci = _scientific(time.time(), [missing], {"readable": True, "running": 0}, dead)
    if sci["state"] != "UNKNOWN":
        print("FAIL: an unreadable root gave %r, not UNKNOWN" % sci["state"]); bad += 1

    quiet = {"allow": True, "reason": "HEALTHY", "scorer_alive": False, "age_s": 1.0}
    sci2 = _scientific(time.time(), [], {"readable": False, "why": "HTTP 401"}, quiet)
    sci3 = _scientific(time.time(), [], {"readable": True, "running": 0}, quiet)
    if sci3["state"] != "NONE_OBSERVED":
        print("FAIL: full coverage with nothing running gave %r" % sci3["state"]); bad += 1
    hot = {"allow": True, "reason": "HEALTHY", "scorer_alive": True,
           "segment": "test-segment", "age_s": 1.0}
    if _scientific(time.time(), [], {"readable": True, "running": 0}, hot)["state"] != "ACTIVE":
        print("FAIL: a live scorer did not read ACTIVE"); bad += 1
    if sci2["state"] != "UNKNOWN":
        print("FAIL: an unreadable ledger gave %r" % sci2["state"]); bad += 1

    if _gpu(None)["state"] != "UNKNOWN" or _gpu({"gpu": None})["state"] != "UNKNOWN":
        print("FAIL: an unqueryable accelerator did not read UNKNOWN"); bad += 1
    if _gpu({"gpu": {"used_mib": 7000, "total_mib": 8000, "util_pct": 99}})["state"] != "ACTIVE":
        print("FAIL: a saturated accelerator did not read ACTIVE"); bad += 1

    idle_word = "IDLE"
    plain = [v for v in VERDICTS if v == idle_word]
    if plain:
        print("FAIL: %r is a verdict; idleness must always carry its coverage qualifier"
              % idle_word); bad += 1

    live = derive(resources={"gpu": {"used_mib": 7000, "total_mib": 8000, "util_pct": 99},
                             "cpu_pct": 5.0}, ledger=job_ledger())
    if live["verdict"] != "ACTIVE":
        print("FAIL: a saturated card did not make the machine ACTIVE (%s)" % live["verdict"])
        bad += 1

    contradiction = derive(
        resources={"gpu": {"used_mib": 7000, "total_mib": 8000, "util_pct": 99},
                   "cpu_pct": 2.0},
        ledger={"readable": True, "running": 0}, permit=quiet, roots=[])
    if contradiction["verdict"] == "IDLE_OBSERVED":
        print("FAIL: a saturated card with an empty ledger read as idle"); bad += 1
    if "GPU_COMPUTE" not in contradiction["active_classes"]:
        print("FAIL: the saturated card was not reported active"); bad += 1

    blind = derive(resources=None, ledger=None, permit=quiet, roots=[])
    if blind["verdict"] != "ACTIVITY_UNKNOWN":
        print("FAIL: a fully blind derivation gave %r" % blind["verdict"]); bad += 1
    if json.dumps(live).count(":\\\\") or json.dumps(live).count(":/"):
        pass
    for r in live["roots"]:
        if len(r["root"]) > 60 or ":" in r["root"].split("/")[0][1:]:
            print("FAIL: a root leaked something path-shaped: %r" % r["root"]); bad += 1

    print("activity selftest: %s" % ("PASS" if bad == 0 else "%d FAILURE(S)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
