"""Ledger v2 re-seal: planted defects that the seal must catch."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import hashlib
import json
import threading
from pathlib import Path

import pytest

from argus.core import actions as A
from argus.core import ledger_v2 as L

LIVE_V1 = Path(_argus_public_path('home', 'state/audit.jsonl'))


def _v1_hash(prev: str, rec: dict) -> str:
    return hashlib.sha256((prev + json.dumps(rec, sort_keys=True, default=str))
                          .encode("utf-8")).hexdigest()


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STATE", tmp_path)
    monkeypatch.setattr(A, "AUDIT", tmp_path / "audit.jsonl")
    for i in range(5):
        A.audit({"event": "e%d" % i})
    recs = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text("utf-8").splitlines()]
    fork = {"event": "forked", "utc": "2026-09-10T21:16:41Z", "prev_hash": recs[3]["this_hash"]}
    fork["this_hash"] = _v1_hash(fork["prev_hash"], fork)
    with (tmp_path / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(fork) + "\n")
    tail = {"event": "after", "utc": "x", "prev_hash": fork["this_hash"]}
    tail["this_hash"] = _v1_hash(tail["prev_hash"], tail)
    with (tmp_path / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(tail) + "\n")
    return tmp_path


def _bytes(p: Path) -> bytes:
    return p.read_bytes()


def _sabotage(p: Path, mutate) -> None:
    before = p.read_bytes()
    after = mutate(before)
    assert after != before, "sabotage changed nothing -- this test would pass for the wrong reason"
    p.write_bytes(after)


def _sealed(state):
    before = _bytes(A.AUDIT)
    g = L.seal(sealed_by={"script": "test"})
    assert _bytes(A.AUDIT) == before
    return g, before



def test_fixture_v1_is_broken_at_the_fork(state):
    assert A.verify_audit_chain() == {"status": "BROKEN", "at": 5, "why": "prev_hash does not chain"}


def test_genesis_binds_full_hash_size_break_and_suppresses_history(state):
    g, before = _sealed(state)
    p = g["predecessor"]
    assert p["sha256"] == hashlib.sha256(before).hexdigest() and len(p["sha256"]) == 64
    assert p["size_bytes"] == len(before)
    assert p["line_count"] == before.count(b"\n") == 7
    assert p["path"] == str(A.AUDIT).replace("\\", "/")
    assert p["historical_chain"]["status"] == "BROKEN" and p["break"]["at"] == 5
    assert p["break"]["all_link_breaks"][0]["prev_hash_names_record"] == 3
    a = g["attestation"]
    assert a["re_attested"] is False
    assert a["historical_certification"] == "SUPPRESSED"
    assert "POST_BREAK_ATTESTATION_LOST_UNVERIFIED" in a["post_break"]
    assert a["attested_by_v1_chain"] == "records 0..4"


def test_audit_after_seal_writes_v2_and_never_v1(state):
    g, before = _sealed(state)
    A.audit({"event": "new", "action": "preflight"})
    A.audit({"event": "new2"})
    assert _bytes(A.AUDIT) == before
    st = L.status()
    assert st["historical"]["state"] == L.HISTORICAL_CHAIN_BROKEN
    assert st["historical"]["chain"]["at"] == 5
    assert st["current"]["state"] == L.CURRENT_CHAIN_VERIFIED
    assert st["current"]["n"] == 3 and st["current"]["genesis_hash"] == g["this_hash"]
    assert st["certification"] == dict(st["certification"], historical_records="SUPPRESSED",
                                       new_records="ALLOWED")
    assert A.verify_audit_chain()["status"] == "BROKEN"


def test_unsealed_state_keeps_legacy_behaviour(state):
    assert not L.is_sealed()
    assert L.verify()["state"] == L.CURRENT_CHAIN_UNKNOWN



def test_tampered_old_file_breaks_the_genesis_binding(state):
    _sealed(state)
    A.audit({"event": "new"})
    assert L.verify()["state"] == L.CURRENT_CHAIN_VERIFIED
    _sabotage(A.AUDIT, lambda b: b.replace(b'"e1"', b'"e9"', 1))
    v = L.verify()
    assert v["state"] == L.CURRENT_CHAIN_BROKEN
    assert v["why"].startswith("PREDECESSOR_MODIFIED") and v["genesis_matches_predecessor"] is False
    assert L.status()["certification"]["new_records"] == "SUPPRESSED"


def test_same_length_single_byte_tamper_is_caught(state):
    _sealed(state)
    _sabotage(A.AUDIT, lambda b: b[:-2] + bytes([b[-2] ^ 1]) + b[-1:])
    assert L.verify()["state"] == L.CURRENT_CHAIN_BROKEN


def test_append_to_old_file_after_seal_is_not_verified(state):
    _sealed(state)
    _sabotage(A.AUDIT, lambda b: b + b'{"event":"stale writer"}\n')
    v = L.verify()
    assert v["state"] == L.CURRENT_CHAIN_UNKNOWN
    assert v["reason"] == "PREDECESSOR_APPENDED_AFTER_SEAL" and v["predecessor_sealed_prefix_intact"]


def test_acknowledged_stray_append_verifies_but_is_not_attested(state):
    _sealed(state)
    _sabotage(A.AUDIT, lambda b: b + b'{"event":"stale writer"}\n')
    assert L.verify()["state"] == L.CURRENT_CHAIN_UNKNOWN
    L.acknowledge_predecessor_append(why="test")
    v = L.verify()
    assert v["state"] == L.CURRENT_CHAIN_VERIFIED
    assert v["predecessor_post_seal_bytes_attested"] is False
    _sabotage(A.AUDIT, lambda b: b + b'{"event":"another"}\n')
    assert L.verify()["state"] == L.CURRENT_CHAIN_UNKNOWN


def test_acknowledgement_never_launders_prefix_tamper(state):
    _sealed(state)
    _sabotage(A.AUDIT, lambda b: b + b'{"event":"stale writer"}\n')
    L.acknowledge_predecessor_append(why="test")
    _sabotage(A.AUDIT, lambda b: b.replace(b'"e1"', b'"e9"', 1))
    assert L.verify()["state"] == L.CURRENT_CHAIN_BROKEN
    with pytest.raises(L.LedgerError):
        L.acknowledge_predecessor_append(why="launder")


def test_acknowledgement_refused_when_v1_has_not_grown(state):
    _sealed(state)
    with pytest.raises(L.LedgerError):
        L.acknowledge_predecessor_append(why="nothing to acknowledge")


def test_missing_old_file_is_unknown_not_verified(state):
    _sealed(state)
    A.AUDIT.unlink()
    assert L.verify()["state"] == L.CURRENT_CHAIN_UNKNOWN


def test_edited_v2_record_is_detected(state):
    _sealed(state)
    A.audit({"event": "one"})
    A.audit({"event": "two"})
    v2 = L.v2_path()
    _sabotage(v2, lambda b: b.replace(b'"one"', b'"ONE"', 1))
    v = L.verify()
    assert v["state"] == L.CURRENT_CHAIN_BROKEN and v["at"] == 1
    assert "own hash" in v["why"]


def test_edited_v2_record_with_rehashed_self_breaks_the_next_link(state):
    _sealed(state)
    A.audit({"event": "one"})
    A.audit({"event": "two"})
    v2 = L.v2_path()
    lines = v2.read_bytes().split(b"\n")
    rec = json.loads(lines[1])
    rec["event"]["event"] = "ONE"
    rec["this_hash"] = L.record_hash(rec)
    lines[1] = json.dumps(rec, sort_keys=True).encode()
    _sabotage(v2, lambda b: b"\n".join(lines))
    v = L.verify()
    assert v["state"] == L.CURRENT_CHAIN_BROKEN and v["at"] == 2
    assert v["why"] == "prev_hash does not chain"


def test_edited_genesis_is_detected(state):
    _sealed(state)
    v2 = L.v2_path()
    _sabotage(v2, lambda b: b.replace(b'"re_attested": false', b'"re_attested": true', 1))
    v = L.verify()
    assert v["state"] == L.CURRENT_CHAIN_BROKEN and v["at"] == 0


def test_reordered_v2_records_are_detected(state):
    _sealed(state)
    for e in ("a", "b", "c"):
        A.audit({"event": e})
    v2 = L.v2_path()

    def swap(b):
        ls = b.split(b"\n")
        ls[1], ls[2] = ls[2], ls[1]
        return b"\n".join(ls)
    _sabotage(v2, swap)
    v = L.verify()
    assert v["state"] == L.CURRENT_CHAIN_BROKEN and v["at"] == 1


def test_dropped_v2_record_is_detected(state):
    _sealed(state)
    for e in ("a", "b", "c"):
        A.audit({"event": e})
    v2 = L.v2_path()
    _sabotage(v2, lambda b: b"\n".join(l for i, l in enumerate(b.split(b"\n")) if i != 2))
    assert L.verify()["state"] == L.CURRENT_CHAIN_BROKEN


def test_double_seal_is_refused_and_changes_nothing(state):
    g, before = _sealed(state)
    A.audit({"event": "x"})
    v2_before = _bytes(L.v2_path())
    with pytest.raises(L.AlreadySealed):
        L.seal()
    assert _bytes(L.v2_path()) == v2_before and _bytes(A.AUDIT) == before
    assert L.verify()["state"] == L.CURRENT_CHAIN_VERIFIED


def test_marker_without_v2_refuses_and_never_falls_back_to_v1(state):
    _, before = _sealed(state)
    L.v2_path().unlink()
    with pytest.raises(L.LedgerError):
        A.audit({"event": "would have gone to v1"})
    assert _bytes(A.AUDIT) == before
    assert L.verify()["state"] == L.CURRENT_CHAIN_UNKNOWN


def test_concurrent_appends_do_not_fork(state):
    _sealed(state)
    errs = []

    def worker(k):
        try:
            for j in range(15):
                A.audit({"event": "t%d-%d" % (k, j)})
        except Exception as exc:
            errs.append(exc)
    ts = [threading.Thread(target=worker, args=(k,)) for k in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    v = L.verify()
    assert v["state"] == L.CURRENT_CHAIN_VERIFIED and v["n"] == 1 + 6 * 15


def test_dead_lock_is_reclaimed_without_unlink_race(state, monkeypatch):
    _sealed(state)
    lock = L._lock_path(L.v2_path())
    dead = {"pid": 2_147_483_647, "process_created": 1.0, "token": "dead-owner"}
    lock.write_text(json.dumps(dead), encoding="utf-8")
    monkeypatch.setattr(L, "LOCK_WAIT_S", 0.01)
    A.audit({"event": "after-dead-owner"})
    assert not lock.exists()
    assert not list(lock.parent.glob(lock.name + ".stale.*"))
    assert L.verify()["state"] == L.CURRENT_CHAIN_VERIFIED


def test_live_lock_is_never_stolen_by_age(state, monkeypatch):
    pytest.importorskip("psutil")
    import os
    import psutil

    _sealed(state)
    lock = L._lock_path(L.v2_path())
    live = {"pid": os.getpid(),
            "process_created": round(float(psutil.Process(os.getpid()).create_time()), 3),
            "token": "live-owner"}
    lock.write_text(json.dumps(live), encoding="utf-8")
    before = lock.read_bytes()
    monkeypatch.setattr(L, "LOCK_WAIT_S", 0.01)
    with pytest.raises(L.LedgerError, match="refusing rather than forking"):
        A.audit({"event": "must-not-append"})
    assert lock.read_bytes() == before
    assert not list(lock.parent.glob(lock.name + ".stale.*"))


def test_the_detector_is_not_simply_always_failing(state):
    """Control: an untouched sealed ledger verifies."""
    _sealed(state)
    A.audit({"event": "ok"})
    assert L.verify()["state"] == L.CURRENT_CHAIN_VERIFIED



def test_ledger_api_route_serves_two_separate_facts(state):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from argus.service import ledger_api

    app = FastAPI()
    app.include_router(ledger_api.router)
    c = TestClient(app)
    _sealed(state)
    d = c.get("/api/ledger/status").json()
    assert d["historical"]["state"] == "HISTORICAL_CHAIN_BROKEN"
    assert d["current"]["state"] == "CURRENT_CHAIN_VERIFIED"
    assert d["readable"] is True
