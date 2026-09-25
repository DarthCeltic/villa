"""Many agents, one machine: the stack registry, admission by memory, idle eviction, the GPU cap."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus.core import gpu_budget, instance_guard as IG

ROOT = Path(__file__).resolve().parents[2]


def test_policy_selftests():
    assert IG.selftest()
    assert gpu_budget.selftest()


def test_a_pinned_or_busy_stack_is_never_retired(monkeypatch):
    monkeypatch.setenv("ARGUS_MIN_COMMIT_FREE_GIB", "6")
    now = 50_000.0
    rows = [{"stack": "busy", "started_epoch": now - 30}, {"stack": "pin", "started_epoch": 0, "pinned": True}]
    with pytest.raises(IG.StackRefused) as exc:
        IG.admit("observe", 1, env={"ARGUS_HOME": "new"}, rows=rows, commit_free=6.5, cost=lambda v: 1.5,
                 evict=lambda k: pytest.fail("evicted %s" % k), now=now)
    msg = str(exc.value)
    assert "stop-stack" in msg and "find" in msg and "busy" in msg and "PINNED" in msg


def test_health_probes_do_not_count_as_use(tmp_path):
    marker = tmp_path / "m.active"
    marker.touch()
    os.utime(marker, (1, 1))
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])

    import asyncio
    wrapped = IG.track(app, marker)
    asyncio.run(wrapped({"type": "http", "path": "/api/live"}, None, None))
    assert os.path.getmtime(marker) == 1
    asyncio.run(wrapped({"type": "http", "path": "/api/scroll_status"}, None, None))
    assert os.path.getmtime(marker) > 1 and seen == ["/api/live", "/api/scroll_status"]


def test_child_env_caps_torch_without_touching_the_parent(monkeypatch):
    monkeypatch.delenv("PYTORCH_CUDA_ALLOC_CONF", raising=False)
    env = gpu_budget.child_env(total_gib=8.0)
    assert env["PYTORCH_CUDA_ALLOC_CONF"] == "per_process_memory_fraction:%.4f" % (gpu_budget.cap_gib(8.0) / 8.0)
    assert "PYTORCH_CUDA_ALLOC_CONF" not in os.environ


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(tmp_path, name, port, **extra):
    home = tmp_path / name
    home.mkdir(exist_ok=True)
    env = dict(os.environ, ARGUS_HOME=str(home), ARGUS_REPO=str(ROOT), PYTHONPATH=str(ROOT),
               ARGUS_INSTANCE_REGISTRY=str(tmp_path / "registry"), PYTHONUNBUFFERED="1", **extra)
    log = open(tmp_path / ("%s.log" % name), "w")
    return subprocess.Popen([sys.executable, "-m", "argus.serve", "observe", "--port", str(port)],
                            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)


def _wait_registered(tmp_path, name, timeout=180):
    """The row for stack `name`."""
    home = os.path.normcase(os.path.abspath(str(tmp_path / name)))
    t0 = time.time()
    while time.time() - t0 < timeout:
        for p in (tmp_path / "registry").glob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if os.path.normcase(os.path.abspath(rec.get("argus_home") or "")) == home:
                return rec
        time.sleep(0.5)
    raise AssertionError("stack %s never registered" % name)


@pytest.mark.skipif(os.name != "nt", reason="registry integration measured on the Windows host")
def test_real_services_register_refuse_and_retire(tmp_path):
    a = _start(tmp_path, "a", _free_port())
    try:
        rec = _wait_registered(tmp_path, "a")
        assert rec["role"] == "observe" and rec["stack"].startswith(os.path.normcase(str(tmp_path / "a")))

        b = _start(tmp_path, "b", _free_port(), ARGUS_MAX_STACKS="1")
        assert b.wait(timeout=180) == 3
        assert "REFUSED" in (tmp_path / "b.log").read_text(encoding="utf-8")
        assert a.poll() is None, "a refusal must never stop the running stack"

        c = _start(tmp_path, "c", _free_port(), ARGUS_MAX_STACKS="1", ARGUS_IDLE_EVICT_MINUTES="0")
        try:
            _wait_registered(tmp_path, "c")
            a.wait(timeout=60)
            assert a.poll() is not None, "the idle stack was not retired"
            assert "retired an idle ARGUS stack" in (tmp_path / "c.log").read_text(encoding="utf-8")
        finally:
            c.kill()
    finally:
        if a.poll() is None:
            a.kill()
