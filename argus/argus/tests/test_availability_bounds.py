"""A caller cannot park the service's workers or make it walk the disk or the network on every request."""
import asyncio
import subprocess
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from argus.core import single_flight as SF
from argus.core import worker_pool as WP


def test_a_follower_stops_waiting_for_a_hung_leader_instead_of_parking_forever():
    memo = SF.SingleFlightTTL(ttl_s=5, wait_s=0.3)
    release = threading.Event()
    started = threading.Event()

    def hung():
        started.set()
        release.wait(5)
        return "late"

    leader = threading.Thread(target=lambda: memo.get("k", hung))
    leader.start()
    assert started.wait(2)
    t0 = time.monotonic()
    with pytest.raises(SF.SingleFlightTimeout):
        memo.get("k", lambda: "never")
    assert 0.25 < time.monotonic() - t0 < 2
    release.set()
    leader.join(3)
    assert memo.get("k", lambda: "recomputed") == "late"


def test_peek_returns_a_fresh_answer_without_computing_or_waiting_and_misses_otherwise():
    clock = {"t": 0.0}
    memo = SF.SingleFlightTTL(ttl_s=2, clock=lambda: clock["t"])
    assert memo.peek("k") is SF.MISS
    memo.get("k", lambda: 41)
    assert memo.peek("k") == 41
    clock["t"] = 3.0
    assert memo.peek("k") is SF.MISS


def test_a_cache_hit_is_answered_even_when_every_worker_and_queue_slot_is_taken(monkeypatch):
    from argus.service import app as A

    pool = WP.BoundedPool("read-heavy", max_workers=1, max_pending=1)
    monkeypatch.setattr(A, "_HEAVY_POOL", pool)
    memo = SF.SingleFlightTTL(ttl_s=30)
    gate = threading.Event()

    async def scenario():
        held = [asyncio.ensure_future(pool.run(gate.wait, 10)) for _ in range(2)]
        await asyncio.sleep(0.1)
        with pytest.raises(WP.PoolSaturated):
            await A._heavy_memo(memo, "k", lambda: "computed")
        memo._done["k"] = (time.monotonic() + 30, "cached")
        assert await A._heavy_memo(memo, "k", lambda: "recomputed") == "cached"
        gate.set()
        await asyncio.gather(*held)

    asyncio.run(scenario())
    pool.shutdown()


def test_surfaces_concurrent_callers_share_one_computation(monkeypatch):
    from argus.core import operations_status as OS
    from argus.service import app as A

    calls = []

    def fake():
        calls.append(1)
        time.sleep(0.15)
        return {"schema": "fake"}

    A._SURFACES_MEMO.clear()
    monkeypatch.setattr(OS, "all_surfaces", fake)
    c = TestClient(A.app)
    out = []
    threads = [threading.Thread(target=lambda: out.append(c.get("/api/surfaces").status_code)) for _ in range(12)]
    [t.start() for t in threads]
    [t.join(20) for t in threads]
    assert out == [200] * 12 and len(calls) == 1
    A._SURFACES_MEMO.clear()


def test_a_hung_shared_computation_answers_503_not_a_parked_request(monkeypatch):
    from argus.service import app as A

    def boom(*a, **k):
        raise SF.SingleFlightTimeout("still running")

    monkeypatch.setattr(A._SURFACES_MEMO, "peek", lambda k: SF.MISS)
    monkeypatch.setattr(A._SURFACES_MEMO, "get", boom)
    r = TestClient(A.app).get("/api/surfaces")
    assert r.status_code == 503 and r.headers["retry-after"] == "5" and r.json()["error"] == "BUSY"


def test_a_forced_rebuild_is_served_from_the_last_build_if_it_is_fresh_enough():
    from argus.service import receipts as R

    builds = []
    cache = R.Cache(lambda **kw: builds.append(1) or {"generated_at": time.time(), "n": len(builds)}, 300.0, "generated_at")
    cache.get()
    for _ in range(20):
        cache.get(force=True, min_force_age_s=R.Cache.FORCE_MIN_AGE_S)
    assert len(builds) == 1
    cache.get(force=True)
    assert len(builds) == 2
    cache.value["generated_at"] -= R.Cache.FORCE_MIN_AGE_S + 1
    cache.get(force=True, min_force_age_s=R.Cache.FORCE_MIN_AGE_S)
    assert len(builds) == 3


def test_the_live_network_refresh_is_honoured_at_most_once_per_interval(monkeypatch):
    from argus.service import app as A

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="a" * 40 + "\trefs/heads/main\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(A, "_LAST_LS_REMOTE", -1e9)
    c = TestClient(A.app)
    first = c.get("/api/current_state", params={"refresh": 1})
    second = c.get("/api/current_state", params={"refresh": 1})
    assert first.status_code == 200 and second.status_code == 200
    ls_remote = [cmd for cmd in calls if "ls-remote" in cmd]
    assert len(ls_remote) == 1
    body = str(second.json())
    assert "not repeated" in body
