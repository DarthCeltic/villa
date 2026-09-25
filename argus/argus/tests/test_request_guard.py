"""Host and cross-site guards on the observe service, file headers, home redaction, and the WebSocket connection cap."""
import pathlib

import pytest
from fastapi.testclient import TestClient

from argus.core import readiness as R
from argus.service import request_guard as G


def test_host_names_are_parsed_with_ports_and_ipv6_brackets():
    assert G.host_name("127.0.0.1:8792") == "127.0.0.1"
    assert G.host_name("LOCALHOST") == "localhost"
    assert G.host_name("[::1]:8787") == "::1"
    assert G.host_name("evil.example:80") == "evil.example"


def test_only_our_hosts_pass_and_an_environment_entry_extends_the_list_exactly():
    allowed = G.allowed_hosts({})
    assert G.host_ok("127.0.0.1:18787", allowed) and G.host_ok("localhost:5173", allowed) and G.host_ok("observe:8787", allowed)
    for bad in ("rebind.evil.example", "evil.example:8792", "127.0.0.1.evil.example", "localhost.evil.example:8787", "192.0.2.5"):
        assert not G.host_ok(bad, allowed), bad
    assert G.host_ok(None, allowed)
    extended = G.allowed_hosts({G.HOSTS_ENV: " mypc , Lab.Local "})
    assert G.host_ok("mypc:8792", extended) and G.host_ok("lab.local", extended) and not G.host_ok("other", extended)


def test_only_own_page_typed_address_or_non_browser_may_call():
    assert G.fetch_site_ok(None) and G.fetch_site_ok("same-origin") and G.fetch_site_ok("none")
    for bad in ("cross-site", "same-site", "Cross-Site", ""):
        assert not G.fetch_site_ok(bad), bad


def test_home_directory_is_redacted_in_every_spelling(monkeypatch):
    home = pathlib.Path.home()
    assert G.redact_home("%s\\.venv\\Scripts\\python.exe" % str(home)).startswith("~")
    assert G.redact_home(home.as_posix() + "/x").startswith("~")
    assert home.name not in G.redact_home("run: %s" % home) and G.redact_home("") == "" and G.redact_home(None) is None


def test_active_content_is_downloaded_and_every_file_is_marked_nosniff_and_sandboxed():
    h = G.file_headers("report.html", "text/html")
    assert h["Content-Disposition"].startswith("attachment") and h["X-Content-Type-Options"] == "nosniff" and "sandbox" in h["Content-Security-Policy"]
    assert "Content-Disposition" in G.file_headers("logo.svg", "image/svg+xml") and "Content-Disposition" in G.file_headers("x.JS")
    png = G.file_headers("plane.png", "image/png")
    assert "Content-Disposition" not in png and png["X-Content-Type-Options"] == "nosniff"
    assert '"' not in G.file_headers('a"b.html', "text/html")["Content-Disposition"].split("filename=")[1].strip('"')


@pytest.fixture()
def client():
    from argus.service import app as A
    return A, TestClient(A.app)


def test_the_service_refuses_a_rebound_host_and_a_cross_site_request_but_not_its_own_calls(client):
    _, c = client
    assert c.get("/api/live").status_code == 200
    assert c.get("/api/live", headers={"host": "127.0.0.1:18787"}).status_code == 200
    rebound = c.get("/api/live", headers={"host": "rebind.evil.example:8792"})
    assert rebound.status_code == 421 and rebound.json()["error"] == "MISDIRECTED"
    for site in ("cross-site", "same-site"):
        blocked = c.get("/api/live", headers={"sec-fetch-site": site})
        assert blocked.status_code == 403 and blocked.json()["error"] == "CROSS_SITE"
    assert c.get("/api/live", headers={"sec-fetch-site": "same-origin"}).status_code == 200
    assert c.get("/api/live", headers={"sec-fetch-site": "none"}).status_code == 200


def test_an_environment_host_is_honoured_by_the_running_guard(client, monkeypatch):
    A, c = client
    monkeypatch.setattr(A, "_ALLOWED_HOSTS", G.allowed_hosts({G.HOSTS_ENV: "mypc"}))
    assert c.get("/api/live", headers={"host": "mypc:8792"}).status_code == 200


def test_health_does_not_name_the_operators_home_directory(client):
    _, c = client
    text = c.get("/api/health").text
    home = str(pathlib.Path.home())
    assert home not in text and home.replace("\\", "\\\\") not in text and home.replace("\\", "/") not in text


def test_file_route_marks_active_content_as_a_download(client, tmp_path, monkeypatch):
    A, c = client
    served = tmp_path / "artifacts"
    served.mkdir()
    (served / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (served / "data.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(A, "SERVE_ROOTS", [served])
    monkeypatch.setattr(A, "_resolve_served", lambda p: (served / p).resolve())
    html = c.get("/api/file", params={"path": "page.html"})
    assert html.status_code == 200 and html.headers["content-disposition"].startswith("attachment") and html.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in c.get("/api/file", params={"path": "data.json"}).headers


def test_websocket_connections_are_capped_and_the_slot_is_released(client, monkeypatch):
    A, c = client
    monkeypatch.setattr(A, "WS_MAX_CONNECTIONS", 1)
    monkeypatch.setattr(A, "WS_ALLOWED_ORIGINS", frozenset({"http://127.0.0.1:5173"}))
    monkeypatch.setattr(A, "WS_POLL_S", 0.05)
    origin = {"origin": "http://127.0.0.1:5173"}
    with c.websocket_connect("/ws/observatory", headers=origin) as first:
        first.receive_text()
        with pytest.raises(Exception):
            with c.websocket_connect("/ws/observatory", headers=origin) as second:
                second.receive_text()
    assert A._WS_OPEN == 0


def test_readiness_sentences_never_carry_paths_and_liveness_never_carries_a_pid():
    assert "<path>" in R.scrub("did not run: Command '[D:\\Program Files\\NVIDIA\\nvidia-smi.exe]' timed out at /home/bob/x")
    assert "bob" not in R.scrub("see /home/bob/x and //server/share/y") and "server" not in R.scrub("//server/share/y")
    assert "pid" not in R.liveness()
    rec = R.compute(primed=True, pools=[], measured={"disks": [], "free_ram_gib": 16.0,
                    "gpu": {"status": "UNKNOWN", "devices": [], "why": "nvidia-smi did not run: D:\\Users\\bob\\x.exe timed out", "fix": "see /home/bob"}},
                    provider_lifecycle=None)
    assert "bob" not in str(rec)
