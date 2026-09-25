"""Command-service boundary: no token path in a 401, a cap on request size before authentication, and job ids that cannot climb out of the jobs directory."""
import pytest
from fastapi.testclient import TestClient

from argus.core import actions as A


@pytest.mark.parametrize("bad", ["../x", "..\\x", "a/b", "D:/x", "/etc/passwd", ".hidden", "", "a" * 200, "x..y", "job id", "\\\\server\\share\\x"])
def test_job_ids_that_could_leave_the_jobs_directory_are_refused(bad):
    with pytest.raises(A.Refused) as err:
        A.job_dir(bad)
    assert err.value.code == "BAD_JOB_ID"
    assert A.read_job(bad) is None


def test_ordinary_job_ids_still_resolve_inside_the_jobs_directory():
    for ok in ("job-20260920-abc123", "a", "J_1.2", "0123456789abcdef"):
        assert A.job_dir(ok).parent == A.JOBS


def test_an_unauthenticated_call_is_refused_without_naming_the_token_file():
    from argus.service import command as C

    r = TestClient(C.app).get("/registry")
    assert r.status_code == 401 and "token_file" not in r.text and str(C.TOKEN_PATH) not in r.text


def test_an_oversized_body_is_refused_before_authentication_or_parsing():
    from argus.service import command as C

    c = TestClient(C.app)
    big = c.post("/plan", content=b"x" * (C.MAX_REQUEST_BYTES + 1), headers={"content-type": "application/json"})
    assert big.status_code == 413 and big.json()["limit_bytes"] == C.MAX_REQUEST_BYTES
    assert c.post("/plan", content=b"{}", headers={"content-type": "application/json"}).status_code == 401
    assert c.post("/plan", content=b"{}", headers={"content-type": "application/json", "content-length": "banana"}).status_code in (400, 413)


def test_a_failed_bff_audit_write_does_not_raise_and_says_so_on_stderr(tmp_path, monkeypatch, capsys):
    from argus.service import bff as B

    blocker = tmp_path / "not_a_directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(B, "AUDIT_PATH", blocker / "bff_audit.jsonl")
    B.audit({"event": "governed.submit", "job": "j1"})
    assert "bff audit write failed" in capsys.readouterr().err
