"""The conveyor must yield to science, resume by itself, and fail closed when unsure."""
from __future__ import annotations

import json

import pytest

from argus.core import owned_process_tree as OPT
from argus.core import science_arbiter as SA


@pytest.fixture(autouse=True)
def _idle_conveyor(monkeypatch):
    """Every test below declares the conveyor IDLE unless it is testing the conveyor."""
    monkeypatch.setattr(SA, "active_conveyor", lambda: {
        "readable": True, "busy": False, "holder_alive": False,
        "daemon": None, "leases": [], "why": ""})


@pytest.fixture
def leases(tmp_path, monkeypatch):
    monkeypatch.setattr(OPT, "LEASE_DIR", tmp_path)
    return tmp_path


def _write(leases, job, pid, *, identity=True, create_time=1.0, cmd="python fold A"):
    doc = {"contract": OPT.CONTRACT, "job": job, "pid": pid, "command": cmd,
           "acquired_utc": "2026-09-11T00:00:00Z", "tree": []}
    if identity:
        doc["owner_identity"] = {"pid": pid, "create_time": create_time, "command_line": cmd,
                                 "identity_known": True}
    (leases / ("%s.json" % job)).write_text(json.dumps(doc), encoding="utf-8")


def test_no_lease_means_the_conveyor_runs(leases):
    for work in SA.HEAVY_STORAGE_WORK:
        assert SA.may_run(work)["decision"] == SA.RUN


def test_a_live_fold_makes_every_heavy_job_yield(leases, monkeypatch):
    _write(leases, "argus-exploratory-gpu", 4242)
    monkeypatch.setattr(OPT, "_alive", lambda pid: True)
    monkeypatch.setattr(OPT, "is_same_process", lambda rec, pid: True)
    for work in SA.HEAVY_STORAGE_WORK:
        v = SA.may_run(work)
        assert v["decision"] == SA.YIELD
        assert "spinning disk" in v["why"]
    with pytest.raises(SA.ArbiterRefusal, match="may not start"):
        SA.require("ARCHIVE_TAR_CREATE")


def test_a_dead_holder_does_not_pin_the_conveyor_shut_forever(leases, monkeypatch):
    """A run that dies without releasing must not stop archiving for good."""
    _write(leases, "argus-exploratory-gpu", 4242)
    monkeypatch.setattr(OPT, "_alive", lambda pid: False)
    v = SA.may_run("ARCHIVE_TAR_CREATE")
    assert v["decision"] == SA.RUN
    assert v["state"]["stale"][0]["why"] == "holder is gone"


def test_a_recycled_pid_is_not_mistaken_for_a_live_fold(leases, monkeypatch):
    """The PID is alive but belongs to something else."""
    _write(leases, "argus-exploratory-gpu", 4242)
    monkeypatch.setattr(OPT, "_alive", lambda pid: True)
    monkeypatch.setattr(OPT, "is_same_process", lambda rec, pid: False)
    v = SA.may_run("BULK_SHA_SCAN")
    assert v["decision"] == SA.RUN
    assert "recycled" in v["state"]["stale"][0]["why"]


def test_an_unreadable_lease_yields_rather_than_guessing(leases, monkeypatch):
    (leases / "argus-exploratory-gpu.json").write_text("{ not json", encoding="utf-8")
    v = SA.may_run("DATASET_HYDRATION")
    assert v["decision"] == SA.UNKNOWN_SO_YIELD
    assert "recoverable" in v["why"]


def test_an_undeclared_kind_of_heavy_work_is_refused_not_waved_through(leases):
    with pytest.raises(SA.ArbiterRefusal, match="declared rather than assumed harmless"):
        SA.may_run("SOMETHING_NEW_AND_PROBABLY_FINE")


def test_the_conveyor_resumes_on_its_own_when_science_finishes(leases, monkeypatch):
    """The whole point of yielding rather than disabling: nobody has to re-enable it."""
    _write(leases, "argus-exploratory-gpu", 4242)
    state = {"alive": True}
    monkeypatch.setattr(OPT, "_alive", lambda pid: state["alive"])
    monkeypatch.setattr(OPT, "is_same_process", lambda rec, pid: True)
    assert SA.may_run("ARCHIVE_TAR_CREATE")["decision"] == SA.YIELD

    slept = []

    def fake_sleep(s):
        slept.append(s)
        if len(slept) == 3:
            state["alive"] = False

    clock = {"t": 0.0}

    def fake_now():
        clock["t"] += 60.0
        return clock["t"]

    v = SA.wait_for_quiet("ARCHIVE_TAR_CREATE", poll_s=60, _sleep=fake_sleep, _now=fake_now)
    assert v["decision"] == SA.RUN
    assert v["polls"] == 4 and len(slept) == 3


def test_wait_can_give_up_without_claiming_it_may_run(leases, monkeypatch):
    _write(leases, "argus-exploratory-gpu", 4242)
    monkeypatch.setattr(OPT, "_alive", lambda pid: True)
    monkeypatch.setattr(OPT, "is_same_process", lambda rec, pid: True)
    clock = {"t": 0.0}

    def fake_now():
        clock["t"] += 100.0
        return clock["t"]

    v = SA.wait_for_quiet("ARCHIVE_TAR_CREATE", poll_s=1, max_wait_s=150,
                          _sleep=lambda s: None, _now=fake_now)
    assert v.get("gave_up") is True
    assert v["decision"] != SA.RUN, "giving up must never be reported as permission"


def test_status_names_the_archive_and_the_chunk_cache_explicitly(leases):
    """Both must be named explicitly rather than hidden inside a generic category."""
    s = SA.status()
    assert "ARCHIVE_TAR_CREATE" in s["storage_work"]
    assert "CHUNK_CACHE_SWEEP" in s["storage_work"]
    assert "never disabled" in s["policy"]



def test_a_working_conveyor_makes_heavy_work_yield(leases, monkeypatch, tmp_path):
    """The storage conveyor keeps its own lease table, in its own catalogue."""
    monkeypatch.setattr(SA, "active_conveyor", lambda: {
        "readable": True, "busy": True, "holder_alive": True,
        "daemon": {"pid": 4321, "state": "WORKING", "action": "archive as_x"},
        "leases": [{"resource": "step:as_x", "owner_pid": 4321}]})
    v = SA.may_run("ARCHIVE_TAR_CREATE")
    assert v["decision"] == SA.YIELD
    assert "already working" in v["why"]


def test_an_unreadable_conveyor_catalogue_yields_rather_than_reading_as_idle(leases, monkeypatch):
    """A schema guess that degrades to 'nothing is happening' is worse than a crash."""
    monkeypatch.setattr(SA, "active_conveyor", lambda: {
        "readable": False, "why": "catalogue unreadable (OperationalError)",
        "daemon": None, "leases": []})
    v = SA.may_run("BULK_SHA_SCAN")
    assert v["decision"] == SA.UNKNOWN_SO_YIELD
    assert "UNKNOWN" in v["why"]


def test_an_idle_conveyor_does_not_block_anything(leases, monkeypatch):
    """Without this, 'yield to the conveyor' would just be 'never run'."""
    monkeypatch.setattr(SA, "active_conveyor", lambda: {
        "readable": True, "busy": False, "holder_alive": False,
        "daemon": {"pid": 1, "state": "IDLE"}, "leases": []})
    assert SA.may_run("ARCHIVE_TAR_CREATE")["decision"] == SA.RUN


def test_the_live_catalogue_is_read_only_and_never_written():
    """This project does not own that catalogue."""
    import pathlib as _pl
    src = _pl.Path(SA.__file__).read_text(encoding="utf-8")
    src = src[src.index("def active_conveyor"):src.index("def active_science")]
    assert "mode=ro" in src, "the conveyor catalogue must be opened read-only"
    for forbidden in ("insert", "update ", "delete", "drop"):
        assert forbidden not in src.lower(), "active_conveyor must only read"



def _conv(**kw):
    base = {"readable": True, "busy": False, "holder_alive": True, "leases": [],
            "daemon": {"pid": 1, "generation": 1, "state": "IDLE",
                       "heartbeat": "2026-09-12T00:00:00Z"}, "why": ""}
    base.update(kw)
    return base


def test_a_dead_conveyor_is_not_reported_as_an_idle_one(monkeypatch):
    """A dead daemon and an idle one are different states."""
    import argus.core.owned_process_tree as _opt
    monkeypatch.setattr(_opt, "_alive", lambda pid: False)
    monkeypatch.setattr(SA, "CONVEYOR_CATALOGUE", __import__("pathlib").Path("nope.db"))
    out = _conv(holder_alive=False)
    out["daemon"]["state"] = "PAUSED"
    assert out["busy"] is False
    monkeypatch.setattr(SA, "active_conveyor", lambda: dict(out, health="DEAD",
                                                            alert="daemon is GONE"))
    s = SA.status()
    assert s["conveyor_health"] == "DEAD"
    assert s["conveyor_alert"]


def test_an_idle_conveyor_is_not_alerted_on(monkeypatch):
    """Without this, every quiet moment would raise an alarm and the alarm would be ignored."""
    monkeypatch.setattr(SA, "active_conveyor", lambda: _conv(health="IDLE", alert=None))
    s = SA.status()
    assert s["conveyor_health"] == "IDLE"
    assert not s["conveyor_alert"]


def test_the_stale_threshold_is_generous_enough_not_to_cry_wolf():
    """A false 'stalled' during a long verify trains the reader to ignore the real one."""
    assert SA.HEARTBEAT_STALE_AFTER_S >= 300
