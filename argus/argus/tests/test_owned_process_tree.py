"""'The task was stopped' and 'the work is gone' are different statements."""
from __future__ import annotations

import json
import os

import pytest

from argus.core import owned_process_tree as OPT


@pytest.fixture
def leases(tmp_path, monkeypatch):
    monkeypatch.setattr(OPT, "LEASE_DIR", tmp_path / "leases")
    return tmp_path / "leases"


@pytest.fixture
def no_subprocess(monkeypatch):
    """Bookkeeping tests must not depend on what is running on this machine right now."""
    monkeypatch.setattr(OPT, "descendants", lambda pid: [])
    monkeypatch.setattr(OPT, "_alive", lambda pid: False)


def test_the_tree_walk_really_walks_this_machine():
    """Not mocked: the whole failure was that descendants went unseen, so the one thing that must not be faked is the walk itself."""
    out = OPT.descendants(os.getpid())
    assert isinstance(out, list)
    for row in out:
        assert isinstance(row["pid"], int) and row["pid"] > 0
        assert isinstance(row["working_set_mb"], int)


@pytest.mark.skipif(os.name != "nt", reason="the liveness probe of owned_process_tree runs through Windows PowerShell")
def test_a_second_job_is_refused_while_the_first_is_alive(leases, monkeypatch):
    monkeypatch.setattr(OPT, "descendants", lambda pid: [])
    OPT.acquire("gpu", pid=os.getpid(), command="first")
    with pytest.raises(OPT.LeaseRefusal) as e:
        OPT.acquire("gpu", pid=os.getpid(), command="second")
    assert "already holds a lease" in str(e.value)


def test_a_lease_whose_work_is_gone_does_not_block_the_next_job(leases, no_subprocess):
    OPT.acquire("gpu", pid=999_999, command="finished")
    OPT.acquire("gpu", pid=os.getpid(), command="next")
    doc = json.loads((leases / "gpu.json").read_text(encoding="utf-8"))
    assert doc["command"] == "next"


def test_the_lease_records_the_work_and_not_only_the_wrapper(leases, monkeypatch):
    monkeypatch.setattr(OPT, "descendants",
                        lambda pid: [{"pid": 4242, "working_set_mb": 814}])
    doc = OPT.acquire("gpu", pid=1234, command="python -m vesuvius.predict")
    assert doc["pid"] == 1234
    assert doc["tree"] == [{"pid": 4242, "working_set_mb": 814}]
    assert doc["command"] == "python -m vesuvius.predict"


def test_refresh_picks_up_workers_that_appeared_after_launch(leases, monkeypatch):
    """A lease taken at t=0 is incomplete: the four workers did not exist yet."""
    monkeypatch.setattr(OPT, "descendants", lambda pid: [])
    OPT.acquire("gpu", pid=1234, command="c")
    monkeypatch.setattr(OPT, "descendants",
                        lambda pid: [{"pid": p, "working_set_mb": 814} for p in range(1, 5)])
    assert len(OPT.refresh("gpu")["tree"]) == 4


def test_an_orphaned_tree_is_a_refusal_and_reports_what_it_holds(leases, monkeypatch):
    monkeypatch.setattr(OPT, "descendants",
                        lambda pid: [{"pid": 2, "working_set_mb": 814},
                                     {"pid": 3, "working_set_mb": 814}])
    monkeypatch.setattr(OPT, "_alive", lambda pid: pid in (1, 2, 3))
    OPT.acquire("gpu", pid=1, command="c")
    r = OPT.verify_exit("gpu")
    assert r["clean"] is False and r["orphan_count"] == 3
    assert r["orphan_working_set_mb"] == 1628
    with pytest.raises(OPT.LeaseRefusal) as e:
        OPT.require_clean_exit("gpu")
    assert "1628 MB" in str(e.value)


def test_a_job_with_no_lease_is_clean_rather_than_an_error(leases, no_subprocess):
    assert OPT.verify_exit("never-ran")["clean"] is True


def test_release_keeps_the_lease_file_when_the_work_is_still_alive(leases, monkeypatch):
    """Deleting the record of a process that is still running is how it becomes invisible."""
    monkeypatch.setattr(OPT, "descendants", lambda pid: [])
    monkeypatch.setattr(OPT, "_alive", lambda pid: True)
    OPT.acquire("gpu", pid=1, command="c")
    r = OPT.release("gpu")
    assert r["released"] is False
    assert (leases / "gpu.json").is_file()


def test_release_drops_the_lease_once_the_work_is_verified_gone(leases, no_subprocess):
    OPT.acquire("gpu", pid=1, command="c")
    assert OPT.release("gpu")["released"] is True
    assert not (leases / "gpu.json").exists()


def test_audit_finds_a_lease_this_session_did_not_take(leases, monkeypatch):
    """A later session must be able to find and clean up work it did not launch."""
    monkeypatch.setattr(OPT, "descendants", lambda pid: [{"pid": 9, "working_set_mb": 9500}])
    monkeypatch.setattr(OPT, "_alive", lambda pid: True)
    OPT.acquire("stranded", pid=8, command="launched by somebody else")
    a = OPT.audit()
    assert a["leases"] == 1
    assert [r["job"] for r in a["orphans"]] == ["stranded"]
