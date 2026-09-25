"""Many agents, one machine: ARGUS stacks are admitted by memory, reused, and evicted when idle."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

DEFAULT_MAX_STACKS = 8
DEFAULT_MIN_COMMIT_FREE_GIB = 6.0
DEFAULT_STACK_COST_GIB = 1.5
DEFAULT_IDLE_EVICT_MINUTES = 30.0
ACTIVITY_WRITE_EVERY_S = 30.0
HEALTH_PATHS = ("/api/live", "/health", "/ui/health", "/api/health", "/healthz")


class StackRefused(RuntimeError):
    """Raised with a message a person can act on."""


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def registry_dir() -> Path:
    env = os.environ.get("ARGUS_INSTANCE_REGISTRY")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "ARGUS" / "instances"


def max_stacks() -> int:
    return max(1, int(_env_float("ARGUS_MAX_STACKS", DEFAULT_MAX_STACKS)))


def min_commit_free_gib() -> float:
    return _env_float("ARGUS_MIN_COMMIT_FREE_GIB", DEFAULT_MIN_COMMIT_FREE_GIB)


def idle_evict_s() -> float:
    return 60.0 * _env_float("ARGUS_IDLE_EVICT_MINUTES", DEFAULT_IDLE_EVICT_MINUTES)


def stack_key(env=None) -> str:
    """Where a stack runs from."""
    env = os.environ if env is None else env
    home = env.get("ARGUS_HOME") or "(default home)"
    root = env.get("ARGUS_REPO") or env.get("PYTHONPATH") or str(Path(__file__).resolve().parents[2])
    return "%s | %s" % (os.path.normcase(os.path.abspath(home)),
                        os.path.normcase(os.path.abspath(root.split(os.pathsep)[0])))


def commit_free_gib() -> float | None:
    """Commit charge still available (Windows: GlobalMemoryStatusEx.ullAvailPageFile)."""
    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullAvailPageFile / 2**30
        except Exception:
            return None
        return None
    try:
        import psutil
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        return (vm.available + sw.free) / 2**30
    except Exception:
        return None


def _stack_pids(svc: list) -> list:
    pids = [int(s["pid"]) for s in svc if s.get("pid")]
    return pids + _ui_pids(next((s.get("ui_port") for s in svc if s.get("ui_port")), None))


def stack_cost_gib(svc: list) -> float | None:
    """Commit charge held by a stack's processes and their children (private bytes)."""
    try:
        import psutil
    except ImportError:
        return None
    seen, total = set(), 0
    for pid in _stack_pids(svc):
        try:
            pr = psutil.Process(pid)
            procs = [pr] + pr.children(recursive=True)
            try:
                parent = pr.parent()
                if parent and "argus.serve" in " ".join(parent.cmdline()):
                    procs.append(parent)
            except psutil.Error:
                pass
        except psutil.Error:
            continue
        for p in procs:
            if p.pid in seen:
                continue
            seen.add(p.pid)
            try:
                mi = p.memory_info()
                total += getattr(mi, "private", 0) or getattr(mi, "vms", 0)
            except psutil.Error:
                pass
    return total / 2**30 if seen else None


def last_active(svc: list) -> float:
    """Newest real request across a stack's services (the activity marker's mtime), else its start."""
    best = 0.0
    for s in svc:
        m = s.get("activity_marker")
        try:
            best = max(best, os.path.getmtime(m)) if m else best
        except OSError:
            pass
        best = max(best, float(s.get("started_epoch") or 0))
    return best


def _identity(pid: int) -> dict:
    from argus.core import owned_process_tree as OPT
    return OPT.identity(pid)


def _same(rec: dict) -> bool:
    from argus.core import owned_process_tree as OPT
    return OPT.is_same_process(rec.get("identity") or {}, int(rec.get("pid", -1)))


def live(*, same=_same) -> list:
    """Every registered service whose process is still the one that registered."""
    d = registry_dir()
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = None
        if rec and same(rec):
            out.append(rec)
            continue
        for q in (p, p.with_suffix(".active")):
            try:
                q.unlink()
            except OSError:
                pass
    return out


def stacks(rows: list) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r.get("stack", "?"), []).append(r)
    return out


def describe(rows: list, *, now=None) -> str:
    groups = stacks(rows)
    if not groups:
        return "  (no ARGUS stack is registered as running)"
    now = time.time() if now is None else now
    lines = []
    for key, svc in sorted(groups.items()):
        ports = ", ".join("%s:%s" % (s.get("role"), s.get("port")) for s in sorted(svc, key=lambda s: str(s.get("role"))))
        ui = next((s.get("ui_port") for s in svc if s.get("ui_port")), None)
        idle_min = (now - last_active(svc)) / 60.0
        pinned = any(s.get("pinned") for s in svc)
        lines.append("  %s\n    services %s%s; build %s; idle %.0f min%s"
                     % (key, ports, (", UI %s" % ui) if ui else "",
                        (next((s.get("build_sha") for s in svc if s.get("build_sha")), None) or "unrecorded")[:12],
                        idle_min, "; PINNED" if pinned else ""))
    return "\n".join(lines)


def admit(role: str, port: int, *, env=None, rows=None, commit_free=None, cost=None, evict=None,
          now=None) -> dict:
    """Admit a service (evicting idle stacks if the memory budget needs it) or raise StackRefused."""
    env = os.environ if env is None else env
    rows = live() if rows is None else rows
    cost = stack_cost_gib if cost is None else cost
    evict = (lambda key: stop_stack(key)) if evict is None else evict
    now = time.time() if now is None else now
    key = stack_key(env)
    groups = stacks(rows)
    if key in groups:
        return {"stack": key, "role": role, "port": int(port), "joined_existing_stack": True, "evicted": []}
    others = {k: v for k, v in groups.items()}
    measured = [c for c in (cost(v) for v in others.values()) if c]
    need = max(measured) if measured else DEFAULT_STACK_COST_GIB
    free = commit_free_gib() if commit_free is None else commit_free
    reserve = min_commit_free_gib()
    limit = max_stacks()
    evicted = []
    while True:
        short = free is not None and free - need < reserve
        crowded = len(others) >= limit
        if not short and not crowded:
            break
        idle = [(last_active(v), k) for k, v in others.items()
                if not any(s.get("pinned") for s in v) and now - last_active(v) >= idle_evict_s()]
        if not idle:
            why = ("only %.1f GiB of commit charge is free; this stack needs about %.1f GiB and the "
                   "machine keeps %.1f GiB in reserve" % (free, need, reserve)) if short else \
                  ("%d stacks are running and the ceiling is %d (ARGUS_MAX_STACKS)" % (len(others), limit))
            raise StackRefused(
                "REFUSED: %s, and no running stack is idle long enough to retire (%d min, "
                "ARGUS_IDLE_EVICT_MINUTES) or unpinned. Reuse one (python -m argus.serve find ...) or stop "
                "one (python -m argus.serve stop-stack \"<stack>\"). Running stacks:\n%s"
                % (why, idle_evict_s() / 60, describe(rows, now=now)))
        _, victim = min(idle)
        freed = cost(others[victim]) or need
        evict(victim)
        evicted.append(victim)
        others.pop(victim)
        if free is not None:
            free += freed
    return {"stack": key, "role": role, "port": int(port), "joined_existing_stack": False,
            "commit_free_gib": free, "stack_cost_gib": need, "evicted": evicted}


def register(role: str, port: int, *, env=None) -> dict:
    """Record this process."""
    import atexit
    env = os.environ if env is None else env
    d = registry_dir()
    d.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    p = d / ("%d.json" % pid)
    marker = p.with_suffix(".active")
    marker.touch()
    rec = {"schema": "argus-instance-v2", "pid": pid, "identity": _identity(pid), "role": role,
           "port": int(port), "stack": stack_key(env), "argus_home": env.get("ARGUS_HOME"),
           "source_root": env.get("ARGUS_REPO"), "ui_port": env.get("ARGUS_UI_PORT"),
           "build_sha": env.get("ARGUS_BUILD_SHA"), "pinned": env.get("ARGUS_STACK_PIN") == "1",
           "activity_marker": str(marker), "started_epoch": time.time(),
           "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    os.replace(tmp, p)

    def _drop():
        for q in (p, marker):
            try:
                q.unlink()
            except OSError:
                pass
    atexit.register(_drop)
    return rec


def track(app, marker: str | os.PathLike):
    """Wrap an ASGI app so real requests (not health probes) refresh the stack's activity marker, at most once every ACTIVITY_WRITE_EVERY_S seconds."""
    marker = str(marker)
    last = [0.0]

    async def wrapped(scope, receive, send):
        if scope.get("type") in ("http", "websocket") and scope.get("path") not in HEALTH_PATHS:
            t = time.time()
            if t - last[0] >= ACTIVITY_WRITE_EVERY_S:
                last[0] = t
                try:
                    os.utime(marker, None)
                except OSError:
                    pass
        await app(scope, receive, send)
    return wrapped


def find(*, build: str | None = None, root: str | None = None, home: str | None = None) -> list:
    """Running stacks an agent could attach to instead of starting its own."""
    out = []
    for key, svc in stacks(live()).items():
        b = next((s.get("build_sha") for s in svc if s.get("build_sha")), "") or ""
        r = next((s.get("source_root") for s in svc if s.get("source_root")), "") or ""
        h = next((s.get("argus_home") for s in svc if s.get("argus_home")), "") or ""
        if build and not b.startswith(build):
            continue
        if root and os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.path.abspath(r or "?")):
            continue
        if home and os.path.normcase(os.path.abspath(home)) != os.path.normcase(os.path.abspath(h or "?")):
            continue
        out.append({"stack": key, "build_sha": b or None, "source_root": r or None, "argus_home": h or None,
                    "ports": {s.get("role"): s.get("port") for s in svc},
                    "ui_port": next((s.get("ui_port") for s in svc if s.get("ui_port")), None)})
    return out


def _ui_pids(ui_port) -> list:
    if not ui_port:
        return []
    try:
        import psutil
        return sorted({c.pid for c in psutil.net_connections(kind="tcp")
                       if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == int(ui_port) and c.pid})
    except Exception:
        return []


def _kill_tree(pid: int) -> int:
    """A process, its children, and launcher parents (a venv shim, or npm/cmd for a dev server) that exist only to wait on it."""
    import psutil
    try:
        pr = psutil.Process(pid)
    except psutil.Error:
        return 0
    victims = pr.children(recursive=True) + [pr]
    try:
        for parent in pr.parents()[:3]:
            cmd = " ".join(parent.cmdline()).lower()
            if "argus.serve" in cmd or "run dev" in cmd or "vite" in cmd:
                victims.append(parent)
            else:
                break
    except psutil.Error:
        pass
    n = 0
    for v in victims:
        try:
            v.kill()
            n += 1
        except psutil.Error:
            pass
    return n


def stop_stack(which: str) -> dict:
    """Stop one stack: matched by its key, its ARGUS_HOME, or any of its ports."""
    groups = stacks(live())
    chosen = [k for k, svc in groups.items()
              if which == k or which == (svc[0].get("argus_home") or "")
              or any(str(s.get("port")) == str(which) or str(s.get("ui_port")) == str(which) for s in svc)]
    if len(chosen) != 1:
        return {"stopped": 0, "why": ("no running stack matches %r" % which) if not chosen
                else "%r matches %d stacks; use the full stack key" % (which, len(chosen))}
    svc = groups[chosen[0]]
    killed = 0
    for pid in _ui_pids(next((s.get("ui_port") for s in svc if s.get("ui_port")), None)):
        killed += _kill_tree(pid)
    for s in svc:
        killed += _kill_tree(int(s["pid"]))
        base = registry_dir() / ("%d" % int(s["pid"]))
        for q in (base.with_suffix(".json"), base.with_suffix(".active")):
            try:
                q.unlink()
            except OSError:
                pass
    return {"stopped": killed, "stack": chosen[0]}


USAGE = """python -m argus.serve instances                       the stacks on this machine, their idle time and the memory budget
python -m argus.serve find [--build SHA] [--root P] [--home P]   a running stack to reuse (JSON)
python -m argus.serve admit                           may this environment start a stack? (evicts idle stacks if needed; exit 3 = refused)
python -m argus.serve stop-stack <stack|ARGUS_HOME|port>"""


def cli(argv) -> int:
    cmd = argv[0] if argv else "instances"
    if cmd == "instances":
        rows = live()
        free = commit_free_gib()
        print("ARGUS stacks running: %d (ceiling %d); commit charge free: %s; reserve kept: %.1f GiB; "
              "idle stacks retire after %.0f min when memory is needed"
              % (len(stacks(rows)), max_stacks(), "%.1f GiB" % free if free is not None else "unknown",
                 min_commit_free_gib(), idle_evict_s() / 60))
        print(describe(rows))
        return 0
    if cmd == "find":
        opts = dict(zip(argv[1::2], argv[2::2]))
        print(json.dumps(find(build=opts.get("--build"), root=opts.get("--root"), home=opts.get("--home")), indent=1))
        return 0
    if cmd == "stop-stack" and len(argv) >= 2:
        r = stop_stack(argv[1])
        print(json.dumps(r))
        return 0 if r.get("stopped") else 1
    if cmd == "admit":
        try:
            r = admit("launcher", 0)
        except StackRefused as e:
            print(str(e))
            return 3
        print("admitted: %s" % json.dumps(r))
        return 0
    print(USAGE)
    return 2


def selftest() -> bool:
    prior = {k: os.environ.get(k) for k in ("ARGUS_MAX_STACKS", "ARGUS_MIN_COMMIT_FREE_GIB", "ARGUS_IDLE_EVICT_MINUTES")}
    try:
        os.environ.update({"ARGUS_MAX_STACKS": "8", "ARGUS_MIN_COMMIT_FREE_GIB": "6", "ARGUS_IDLE_EVICT_MINUTES": "30"})
        now = 10_000.0
        rows = [{"stack": "A", "role": "observe", "port": 1, "started_epoch": now - 3600},
                {"stack": "B", "role": "observe", "port": 2, "started_epoch": now - 60},
                {"stack": "P", "role": "observe", "port": 3, "started_epoch": now - 9000, "pinned": True}]
        gone = []
        r = admit("observe", 9, env={"ARGUS_HOME": "C"}, rows=rows, commit_free=7.0, cost=lambda v: 1.5,
                  evict=gone.append, now=now)
        assert r["evicted"] == ["A"] and gone == ["A"], r
        try:
            admit("observe", 9, env={"ARGUS_HOME": "C"}, rows=rows[1:], commit_free=7.0, cost=lambda v: 1.5,
                  evict=gone.append, now=now)
            return False
        except StackRefused:
            pass
        ok = admit("observe", 9, env={"ARGUS_HOME": "C"}, rows=rows, commit_free=20.0, cost=lambda v: 1.5,
                   evict=gone.append, now=now)
        assert ok["evicted"] == []
        key = stack_key({"ARGUS_HOME": "A2"})
        joined = admit("command", 9, env={"ARGUS_HOME": "A2"}, rows=[dict(rows[0], stack=key)], commit_free=0.1,
                       cost=lambda v: 1.5, evict=gone.append, now=now)
        assert joined["joined_existing_stack"]
        return True
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
