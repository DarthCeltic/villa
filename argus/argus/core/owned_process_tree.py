"""One lease per job, every descendant tracked, and an orphan is a refusal rather than a surprise."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import os
import pathlib
import subprocess
import time

CONTRACT = "argus-owned-process-tree-v1"

LEASE_DIR = pathlib.Path(_argus_public_path('home', 'state/leases'))


class LeaseRefusal(RuntimeError):
    """Raised rather than returned."""


def identity(pid: int) -> dict:
    """PID plus the two things that make it unambiguous: creation time and command line."""
    try:
        import psutil
        pr = psutil.Process(int(pid))
        return {"pid": int(pid), "create_time": round(float(pr.create_time()), 3),
                "command_line": " ".join(pr.cmdline())[:400], "identity_known": True}
    except Exception as e:
        return {"pid": int(pid), "create_time": None, "command_line": None,
                "identity_known": False, "why": type(e).__name__}


def is_same_process(recorded: dict, pid: int) -> bool:
    """Is the live PID the SAME process the lease recorded, or a recycled handle?"""
    now = identity(pid)
    if not now["identity_known"] or not recorded.get("identity_known"):
        return False
    if recorded.get("create_time") is None:
        return False
    return (abs(float(recorded["create_time"]) - float(now["create_time"])) < 0.5
            and recorded.get("command_line") == now["command_line"])


def _alive(pid: int) -> bool:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "if (Get-Process -Id %d -ErrorAction SilentlyContinue) "
                            "{'1'} else {'0'}" % pid],
                           capture_output=True, text=True, timeout=120)
        return r.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def descendants(pid: int) -> list:
    """Every pid under this one, transitively."""
    code = (
      "$ids=@(%d); $all=Get-CimInstance Win32_Process | "
      "Select-Object ProcessId,ParentProcessId,WorkingSetSize; "
      "$out=@(); $frontier=$ids; "
      "while($frontier.Count -gt 0){ $next=@(); foreach($f in $frontier){ "
      "foreach($p in $all){ if($p.ParentProcessId -eq $f){ $out+=$p; $next+=$p.ProcessId } } } "
      "$frontier=$next } "
      "$out | ForEach-Object { '{0},{1}' -f $_.ProcessId, [int]($_.WorkingSetSize/1MB) }" % pid)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", code],
                           capture_output=True, text=True, timeout=300)
        out = []
        for line in (r.stdout or "").splitlines():
            parts = line.strip().split(",")
            if len(parts) == 2 and parts[0].isdigit():
                out.append({"pid": int(parts[0]), "working_set_mb": int(parts[1])})
        return out
    except (OSError, subprocess.SubprocessError):
        return []


def acquire(job: str, *, pid: int, command: str, allow_existing: bool = False) -> dict:
    """Take the lease for this job kind."""
    LEASE_DIR.mkdir(parents=True, exist_ok=True)
    path = LEASE_DIR / ("%s.json" % job)

    if path.is_file() and not allow_existing:
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            prior = {}
        owner = prior.get("owner_identity") or {}
        holder = prior.get("pid")
        held = bool(holder) and _alive(int(holder)) and (
          is_same_process(owner, int(holder)) if owner else True)
        live = [p for p in [d["pid"] for d in prior.get("tree", [])]
                if p and _alive(int(p))] if held else []
        if held:
            raise LeaseRefusal(
              "job %r already holds a lease: pid %s (%s) with %d live descendant(s) %s. "
              "Starting a second is how two jobs come to share a card and neither number means "
              "anything."
              % (job, holder, (owner.get("command_line") or "command unknown")[:60],
                 len(live), live[:6]))
        if holder and _alive(int(holder)) and owner and not is_same_process(owner, int(holder)):
            prior_note = ("pid %s is alive but is NOT the process this lease recorded -- the "
                          "number was recycled, so the lease is stale and is being taken over"
                          % holder)
            print("[owned_process_tree] %s" % prior_note, flush=True)

    doc = {"contract": CONTRACT, "job": job, "pid": pid, "command": command,
           "owner_identity": identity(pid),
           "acquired_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "tree": descendants(pid)}
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def refresh(job: str) -> dict:
    """Re-walk the tree."""
    path = LEASE_DIR / ("%s.json" % job)
    if not path.is_file():
        raise LeaseRefusal("no lease for %r" % job)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["tree"] = descendants(int(doc["pid"]))
    doc["refreshed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def verify_exit(job: str) -> dict:
    """'Stopped' and 'gone' are different statements."""
    path = LEASE_DIR / ("%s.json" % job)
    if not path.is_file():
        return {"job": job, "lease": False, "clean": True,
                "why": "no lease recorded for this job"}
    doc = json.loads(path.read_text(encoding="utf-8"))
    pids = [int(doc["pid"])] + [int(d["pid"]) for d in doc.get("tree", [])]
    still = descendants(int(doc["pid"]))
    alive = [p for p in pids if _alive(p)]
    held = sum(d["working_set_mb"] for d in still)
    return {
      "job": job, "lease": True, "recorded_pids": pids,
      "still_alive": alive, "orphan_count": len(alive),
      "orphan_working_set_mb": held,
      "clean": not alive,
      "why_this_is_checked": "a stop that kills a shell proves nothing about the process it "
                             "started. A resident orphan can make the next job "
                             "fail on an allocation that looks absurdly small.",
    }


def require_clean_exit(job: str) -> dict:
    r = verify_exit(job)
    if not r["clean"]:
        raise LeaseRefusal(
          "job %r left %d orphaned process(es) holding %d MB: %s. Release them before the next "
          "job, or the next failure will be an absurd allocation error rather than this one."
          % (job, r["orphan_count"], r["orphan_working_set_mb"], r["still_alive"][:6]))
    return r


def release(job: str, *, terminate: bool = False) -> dict:
    """Drop the lease."""
    path = LEASE_DIR / ("%s.json" % job)
    if not path.is_file():
        return {"job": job, "released": False, "why": "no lease"}
    doc = json.loads(path.read_text(encoding="utf-8"))
    killed = []
    if terminate:
        for p in [int(doc["pid"])]:
            try:
                subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"],
                               capture_output=True, timeout=300)
                killed.append(p)
            except (OSError, subprocess.SubprocessError):
                pass
    r = verify_exit(job)
    if r["clean"]:
        path.unlink(missing_ok=True)
    return {"job": job, "released": r["clean"], "terminated": killed, "verification": r}


def audit() -> dict:
    """Every lease on this machine and whether its work is really gone."""
    LEASE_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(LEASE_DIR.glob("*.json")):
        try:
            rows.append(verify_exit(p.stem))
        except Exception as exc:
            rows.append({"job": p.stem, "error": str(exc)})
    return {"contract": CONTRACT, "leases": len(rows), "rows": rows,
            "orphans": [r for r in rows if r.get("lease") and not r.get("clean")]}
