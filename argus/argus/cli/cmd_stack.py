"""argus start | stop | status -- the Docker stack, one command each, with bounded waits and plain errors."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
UI_URL = "http://127.0.0.1:8792"
OBSERVE_URL = "http://127.0.0.1:18787"
PORTS = {8792: "the ARGUS UI", 18787: "the read-only service"}
PROFILES = ("cpu_only", "gpu_6gb", "gpu_12gb")
MIN_BUILD_GIB = 10.0
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


MAX_STATUS_BYTES = 1 << 20


def _which(name):
    from argus.cli import hardware

    return hardware.which_on_path(name)


def _clean(text):
    """Text that came from a service on a port is shown to a terminal: control characters (escape sequences) are removed first."""
    return "".join(c for c in str(text) if c.isprintable()) if text is not None else text


def _run(cmd, timeout):
    try:
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %d s" % timeout
    except OSError as exc:
        return 127, "", "%s: %s" % (type(exc).__name__, exc)


def _http_json(url, timeout=4.0):
    """(status, json-or-None)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read(MAX_STATUS_BYTES).decode("utf-8", "replace") or "null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read(MAX_STATUS_BYTES).decode("utf-8", "replace") or "null")
        except ValueError:
            return exc.code, None
    except (OSError, ValueError):
        return None, None


def _http_ok(url, timeout=4.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 400
    except (OSError, ValueError):
        return False


def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _check(name, state, why, fix=None):
    return {"check": name, "state": state, "why": why, "fix": fix}


def preflight(*, run=_run, which=_which, port_free=_port_free, http_ok=_http_ok, disk_free_gib=None, stack_up=None):
    """What must be true before `docker compose up`."""
    checks = []
    docker = which("docker") or which("docker.exe")
    if not docker:
        return [_check("docker", FAIL, "docker is not on PATH", "install Docker Desktop, start it once, and run this again")]
    checks.append(_check("docker", PASS, "found %s" % docker))
    rc, out, err = run([docker, "info", "--format", "{{.ServerVersion}}"], 20)
    if rc != 0:
        checks.append(_check("docker engine", FAIL, "the Docker engine is not answering (%s)" % (err.strip().splitlines() or ["no detail"])[-1][:120],
                             "open Docker Desktop and wait until it says it is running"))
        return checks
    checks.append(_check("docker engine", PASS, "engine %s" % out.strip()))
    rc, out, err = run([docker, "compose", "version", "--short"], 20)
    checks.append(_check("docker compose", PASS, "compose %s" % out.strip()) if rc == 0 else
                  _check("docker compose", FAIL, "the compose plugin is missing", "update Docker Desktop (compose v2 is included)"))
    checks.append(_check("compose file", PASS, str(REPO / "docker-compose.yml")) if (REPO / "docker-compose.yml").is_file() else
                  _check("compose file", FAIL, "docker-compose.yml is not next to the source", "run this from a full ARGUS checkout"))
    if stack_up is not None:
        mine = stack_up
    else:
        rc, out, _ = run([docker, "compose", "ps", "--status", "running", "-q"], 20)
        mine = rc == 0 and bool(out.strip())
    for port, what in PORTS.items():
        if port_free(port):
            checks.append(_check("port %d" % port, PASS, "free for %s" % what))
        elif mine:
            checks.append(_check("port %d" % port, PASS, "already serving %s (the stack is up)" % what))
        else:
            checks.append(_check("port %d" % port, FAIL, "something else is using it", "stop that program, or free port %d" % port))
    free = disk_free_gib
    if free is None:
        try:
            free = shutil.disk_usage(REPO).free / (1 << 30)
        except OSError:
            free = None
    if free is None:
        checks.append(_check("disk", UNKNOWN, "free space could not be read", None))
    elif free < MIN_BUILD_GIB and not mine:
        checks.append(_check("disk", FAIL, "%.1f GiB free; the first build needs about %.0f GiB" % (free, MIN_BUILD_GIB), "free disk space, then run this again"))
    else:
        checks.append(_check("disk", PASS, "%.1f GiB free" % free))
    return checks


def _print_checks(checks, out):
    for c in checks:
        print("  %-8s %-14s %s" % (c["state"], c["check"], c["why"]), file=out)
        if c["state"] != PASS and c.get("fix"):
            print("           -> %s" % c["fix"], file=out)


def _wait_ready(timeout_s, out, *, http_json=_http_json, http_ok=_http_ok, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout_s
    last = None
    while clock() < deadline:
        ui = http_ok(UI_URL + "/health")
        status, rec = http_json(OBSERVE_URL + "/api/ready")
        last = (ui, status, rec)
        if ui and status == 200:
            return True, last
        sleep(2)
    return False, last


def cmd_start(args, out) -> int:
    if args.profile:
        os.environ["ARGUS_PROFILE"] = args.profile
    print("checking this machine before starting...", file=out)
    checks = preflight()
    _print_checks(checks, out)
    if any(c["state"] == FAIL for c in checks):
        print("\nARGUS did not start: fix the FAIL line(s) above and run `argus start` again. Nothing was changed.", file=out)
        return 1
    docker = _which("docker") or _which("docker.exe")
    cmd = [docker, "compose", "up", "-d"] + ([] if args.no_build else ["--build"])
    print("\nstarting the stack%s (first build takes several minutes; up to %d s)..." % ("" if args.no_build else " and building", args.timeout), file=out)
    rc, _, err = _run(cmd, args.timeout)
    if rc != 0:
        tail = "\n".join((err.strip().splitlines() or ["no detail"])[-6:])
        print("\nARGUS did not start: docker compose exited %d.\n%s\nrun `docker compose ps` and `docker compose logs --tail 40` to see why." % (rc, tail), file=out)
        return 1
    if args.no_wait:
        print("started; not waiting. Run `argus status` to see when it is ready.", file=out)
        return 0
    ok, last = _wait_ready(args.timeout, out)
    if not ok:
        ui, status, rec = last or (False, None, None)
        print("\nthe containers started but ARGUS is not ready yet (UI %s, service %s)." % ("up" if ui else "down", status if status else "not answering"), file=out)
        if rec and rec.get("next"):
            print("  next: %s" % _clean(rec["next"]), file=out)
        print("  run `argus status` for the full picture.", file=out)
        return 1
    print("\nARGUS is ready at %s" % UI_URL, file=out)
    if args.profile:
        print("profile %s reported by the service. The containers have no GPU passthrough; GPU stages need a native install." % args.profile, file=out)
    return 0


def cmd_stop(args, out) -> int:
    docker = _which("docker") or _which("docker.exe")
    if not docker:
        print("docker is not on PATH, so there is nothing for this command to stop.", file=out)
        return 1
    rc, _, err = _run([docker, "compose", "down" if args.down else "stop"], 180)
    if rc != 0:
        print("could not stop the stack: %s" % ((err.strip().splitlines() or ["no detail"])[-1]), file=out)
        return 1
    print("stopped. %s Volumes and receipts were not touched." % ("Containers removed." if args.down else "Containers kept; `argus start` resumes them."), file=out)
    return 0


def gather_status(*, run=None, http_json=None, http_ok=None, which=None):
    run, http_json, http_ok, which = run or _run, http_json or _http_json, http_ok or _http_ok, which or _which
    docker = which("docker") or which("docker.exe")
    services = []
    if docker:
        rc, out, _ = run([docker, "compose", "ps", "--format", "json"], 30)
        if rc == 0:
            for line in out.splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                services.append({"service": d.get("Service"), "state": d.get("State"), "health": d.get("Health") or None})
    ui = http_ok(UI_URL + "/health")
    status, rec = http_json(OBSERVE_URL + "/api/ready")
    if status is None:
        live_status, _ = http_json(OBSERVE_URL + "/api/live")
        state = "RUNNING_NOT_ANSWERING_READINESS" if live_status == 200 else "NOT_RUNNING"
    elif status == 200:
        state = "READY"
    elif status == 503 and rec:
        state = "NOT_READY"
    else:
        state = "ANSWERING_WITHOUT_READINESS"
    return {"state": state, "ui": "up" if ui else "down", "services": services, "readiness": rec, "docker": bool(docker)}


def cmd_status(args, out) -> int:
    st = gather_status()
    if args.json:
        print(json.dumps(st, indent=1), file=out)
    else:
        print("ARGUS: %s   UI: %s   docker: %s" % (st["state"], st["ui"], "found" if st["docker"] else "not found"), file=out)
        for s in st["services"]:
            print("  service %-8s %-9s %s" % (s["service"], s["state"], s["health"] or ""), file=out)
        rec = st["readiness"]
        if rec:
            print("  profile: %s   overall: %s" % ((rec.get("profile") or {}).get("id") or "none", rec.get("state")), file=out)
            for c in rec.get("checks", []):
                print("  %-9s %-20s %s" % (_clean(c["state"]), _clean(c["check"]), _clean(c["why"])), file=out)
            if rec.get("next"):
                print("  next: %s" % _clean(rec["next"]), file=out)
        elif st["state"] == "ANSWERING_WITHOUT_READINESS":
            print("  a service answers on %s but has no /api/ready: it is an older ARGUS build, or something else. Run `argus start` from this checkout to replace it." % OBSERVE_URL, file=out)
        elif st["state"] == "NOT_RUNNING":
            print("  nothing is answering on %s. Run `argus start`." % OBSERVE_URL, file=out)
    return {"READY": 0, "NOT_READY": 1}.get(st["state"], 2)


def run(argv, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(prog="argus", add_help=True)
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("start", help="start the Docker stack and wait until ARGUS is ready")
    p.add_argument("--no-build", action="store_true", help="reuse the images that are already built")
    p.add_argument("--no-wait", action="store_true", help="return once the containers are started")
    p.add_argument("--profile", choices=PROFILES, help="resource profile the service reports and sizes its workers by")
    p.add_argument("--timeout", type=int, default=600, help="seconds to allow for build and readiness (30-1800)")
    p = sub.add_parser("stop", help="stop the stack, keeping its data")
    p.add_argument("--down", action="store_true", help="also remove the containers (volumes are never removed)")
    p = sub.add_parser("status", help="is it running, is it ready, and if not, why")
    p.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if a.verb == "start":
        a.timeout = max(30, min(1800, a.timeout))
        return cmd_start(a, out)
    return cmd_stop(a, out) if a.verb == "stop" else cmd_status(a, out)
