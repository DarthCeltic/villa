"""Sabotage tests for the browser's one door."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from argus.service import bff

ALLOWED = "http://127.0.0.1:5173"
HOSTILE = "https://evil.example"
ACCESS_KEY = "TEST-UI-ACCESS-KEY"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    tok = tmp_path / "command_token"
    tok.write_text("SUPER-SECRET-TOKEN-VALUE-do-not-leak", encoding="utf-8")
    monkeypatch.setattr(bff, "TOKEN_PATH", tok)
    access = tmp_path / "ui_access_key"
    access.write_text(ACCESS_KEY, encoding="utf-8")
    monkeypatch.setattr(bff, "UI_ACCESS_KEY_PATH", access)
    monkeypatch.setattr(bff, "AUDIT_PATH", tmp_path / "audit.jsonl")
    bff._SESSIONS.clear()
    return TestClient(bff.app)


def _session(client):
    r = client.post("/ui/session", headers={"Origin": ALLOWED,
                                              "X-Argus-Access-Key": ACCESS_KEY})
    assert r.status_code == 200
    return r.json()["csrf"]


def _headers(csrf, key="idem-key-0001"):
    return {"Origin": ALLOWED, "X-Argus-Csrf": csrf, "X-Idempotency-Key": key,
            "Content-Type": "application/json"}



def test_the_session_response_does_not_contain_the_token(client):
    r = client.post("/ui/session", headers={"Origin": ALLOWED,
                                              "X-Argus-Access-Key": ACCESS_KEY})
    assert "SUPER-SECRET" not in r.text


def test_no_route_returns_the_token(client):
    for path in ("/ui/health", "/ui/operations"):
        r = client.get(path, headers={"Origin": ALLOWED})
        assert "SUPER-SECRET" not in r.text, path


def test_the_token_is_never_in_an_error_body(client, monkeypatch):
    """Error paths are exactly where an unreviewed field reaches a browser."""
    def boom(path, payload=None, method="POST"):
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail={
          "echo": "Bearer <SUPER-SECRET-TOKEN-VALUE-do-not-leak>"})
    csrf = _session(client)
    monkeypatch.setattr(bff, "_call_command", boom)
    r = client.post("/ui/act/preflight", headers=_headers(csrf),
                    json={"params": {"action": "score"}})
    assert "SUPER-SECRET" not in r.text


def test_scrub_removes_the_token_at_any_depth(client):
    nested = {"a": [{"b": {"c": "leading SUPER-SECRET-TOKEN-VALUE-do-not-leak trailing"}}]}
    assert "SUPER-SECRET" not in json.dumps(bff.scrub(nested))


def test_scrub_redacts_secret_keys_by_name(client):
    out = bff.scrub({"authorization": "Bearer x", "token": "y", "harmless": "z"})
    assert out["authorization"] == "[redacted]" and out["token"] == "[redacted]"
    assert out["harmless"] == "z"


def test_the_audit_file_never_records_the_token(client, tmp_path):
    bff.audit({"note": "SUPER-SECRET-TOKEN-VALUE-do-not-leak", "token": "x"})
    assert "SUPER-SECRET" not in bff.AUDIT_PATH.read_text(encoding="utf-8")



def test_a_session_requires_the_separate_operator_access_key(client):
    r = client.post("/ui/session", headers={"Origin": ALLOWED})
    assert r.status_code == 401
    assert "argus_sid" not in r.headers.get("set-cookie", "")


def test_a_wrong_operator_access_key_cannot_open_a_session(client):
    r = client.post("/ui/session", headers={
        "Origin": ALLOWED, "X-Argus-Access-Key": "wrong-key"})
    assert r.status_code == 401
    assert "TEST-UI-ACCESS-KEY" not in r.text


@pytest.mark.parametrize("presented", [b"caf\xe9", b"\xff", b"\xe4\xb8\xad\xe6\x96\x87", b"TEST-UI-ACCESS-KEY\xe9"])
def test_a_non_ascii_operator_access_key_is_refused_with_401_not_a_server_error(client, presented):
    """A header value is attacker-chosen text; hmac.compare_digest raises TypeError on a non-ASCII str, which used to surface as HTTP 500."""
    r = client.post("/ui/session", headers={"Origin": ALLOWED, "X-Argus-Access-Key": presented})
    assert r.status_code == 401, r.text
    assert "TEST-UI-ACCESS-KEY" not in r.text


def test_a_non_ascii_csrf_header_is_refused_not_a_server_error(client):
    csrf = _session(client)
    headers = _headers(csrf)
    headers["X-Argus-Csrf"] = b"caf\xe9"
    r = client.post("/ui/act/preflight", headers=headers, json={"params": {}})
    assert r.status_code == 403, r.text


def test_a_missing_operator_access_key_file_fails_closed(client, monkeypatch, tmp_path):
    monkeypatch.setattr(bff, "UI_ACCESS_KEY_PATH", tmp_path / "missing-key")
    r = client.post("/ui/session", headers={
        "Origin": ALLOWED, "X-Argus-Access-Key": ACCESS_KEY})
    assert r.status_code == 503
    assert "TEST-UI-ACCESS-KEY" not in r.text

def test_a_hostile_origin_is_refused(client):
    r = client.post("/ui/session", headers={"Origin": HOSTILE,
                                              "X-Argus-Access-Key": ACCESS_KEY})
    assert r.status_code == 403


def test_a_missing_origin_is_refused_rather_than_assumed_local(client):
    r = client.post("/ui/session")
    assert r.status_code == 403


@pytest.mark.parametrize("origin", [
    "https://evil.example/http://127.0.0.1:5173",
    "http://127.0.0.1:5173.evil.example",
    "http://127.0.0.1:51730",
    "http://127.0.0.1:5173/",
])
def test_an_origin_that_merely_contains_an_allowed_one_is_refused(client, origin):
    """A prefix or substring test would accept every one of these."""
    r = client.post("/ui/session", headers={"Origin": origin,
                                              "X-Argus-Access-Key": ACCESS_KEY})
    assert r.status_code == 403, origin



def test_a_bare_page_load_cannot_act(client):
    """Reaching the page is not authorisation."""
    r = client.post("/ui/act/preflight", headers={"Origin": ALLOWED,
                                                  "X-Idempotency-Key": "k-12345678"},
                    json={"params": {"action": "score"}})
    assert r.status_code == 401


def test_a_session_without_the_csrf_header_cannot_act(client):
    _session(client)
    r = client.post("/ui/act/preflight",
                    headers={"Origin": ALLOWED, "X-Idempotency-Key": "k-12345678"},
                    json={"params": {"action": "score"}})
    assert r.status_code == 403


def test_a_wrong_csrf_value_cannot_act(client):
    _session(client)
    r = client.post("/ui/act/preflight", headers=_headers("not-the-right-value"),
                    json={"params": {"action": "score"}})
    assert r.status_code == 403


def test_an_expired_session_cannot_act(client, monkeypatch):
    csrf = _session(client)
    for s in bff._SESSIONS.values():
        s["expires"] = 0
    r = client.post("/ui/act/preflight", headers=_headers(csrf),
                    json={"params": {"action": "score"}})
    assert r.status_code == 401


def test_the_session_cookie_is_httponly_and_samesite_strict(client):
    r = client.post("/ui/session", headers={"Origin": ALLOWED,
                                              "X-Argus-Access-Key": ACCESS_KEY})
    cookie = r.headers.get("set-cookie", "")
    assert "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower().replace(" ", "")



def test_an_unknown_action_refuses(client):
    csrf = _session(client)
    r = client.post("/ui/act/definitely.not.an.action", headers=_headers(csrf),
                    json={"params": {}})
    assert r.status_code == 400
    assert "not carried" in r.text


def test_an_action_the_command_service_allows_but_this_transport_does_not_refuses(client):
    """SECOND ALLOWLIST, NARROWER THAN THE SERVICE'S."""
    from argus.core import action_registry as AR
    assert "pipeline.start" in AR.REGISTRY
    assert "pipeline.start" not in bff.OPERATIONS
    csrf = _session(client)
    r = client.post("/ui/act/pipeline.start", headers=_headers(csrf), json={"params": {}})
    assert r.status_code == 400


def test_the_transport_carries_identity_bound_acquisition_and_provider_plans(client):
    """The browser can reach the two end-to-end execution doors only through their explicit nested schemas; the command service still owns the second validation and all gates."""
    assert {"acquire.execute", "provider.invoke"} <= set(bff.OPERATIONS)
    assert bff.OPERATIONS["acquire.execute"]["params"]["official_identity"] is dict
    assert bff.OPERATIONS["provider.invoke"]["params"]["source_binding"] is dict



def test_structured_intent_catalog_comes_from_the_command_service(client, monkeypatch):
    seen = []

    def fake(path, payload=None, method="POST"):
        seen.append((path, payload, method))
        return {"intents": [{"intent": "preflight", "action": "preflight",
                              "example": "run preflight"}], "n": 1}

    monkeypatch.setattr(bff, "_call_command", fake)
    r = client.get("/ui/nl/intents")
    assert r.status_code == 200 and r.json()["n"] == 1
    assert seen == [("/nl/intents", None, "GET")]


def test_structured_intent_preview_needs_the_deliberate_session(client):
    r = client.post("/ui/nl/preview", headers={
        "Origin": ALLOWED, "X-Argus-Csrf": "none", "X-Idempotency-Key": "intent-key-1"},
        json={"text": "run preflight"})
    assert r.status_code == 401


def test_structured_intent_preview_and_confirm_forward_only_the_closed_envelope(
        client, monkeypatch):
    calls = []

    def fake(path, payload=None, method="POST"):
        calls.append((path, payload, method))
        if path == "/nl/preview":
            return {"status": "PREVIEW", "resolved": True, "intent": "preflight",
                    "action": "preflight", "confirmation_required": False,
                    "preview_sha256": "a" * 64, "idempotency_key": payload["idempotency_key"]}
        return {"status": "SUCCEEDED", "resolved": True, "intent": "preflight",
                "action": "preflight", "job_id": "job-intent-1"}

    monkeypatch.setattr(bff, "_call_command", fake)
    csrf = _session(client)
    headers = _headers(csrf, "intent-same-key")
    preview = client.post("/ui/nl/preview", headers=headers, json={"text": "run preflight"})
    assert preview.status_code == 200 and preview.json()["action"] == "preflight"
    confirmed = client.post("/ui/nl/confirm", headers=headers,
                            json={"text": "run preflight", "confirm_sha256": "a" * 64})
    assert confirmed.status_code == 200 and confirmed.json()["job_id"] == "job-intent-1"
    assert [c[0] for c in calls] == ["/nl/preview", "/nl/confirm"]
    for _, payload, _ in calls:
        assert payload["actor"] == "human:ui-intent"
        assert payload["idempotency_key"] == "intent-same-key"
        assert "action" not in payload and "params" not in payload

    audit_text = bff.AUDIT_PATH.read_text(encoding="utf-8")
    assert "run preflight" not in audit_text
    assert "text_sha256" in audit_text and "job-intent-1" in audit_text


@pytest.mark.parametrize("body", [
    {}, {"text": ""}, {"text": 3}, {"text": "run preflight", "action": "pipeline.start"},
    {"text": "run preflight", "confirm_sha256": "short"},
])
def test_structured_intent_rejects_malformed_or_smuggled_fields(client, body):
    csrf = _session(client)
    r = client.post("/ui/nl/preview", headers=_headers(csrf, "intent-bad-key"), json=body)
    assert r.status_code == 400


def test_the_transport_carries_the_blender_roundtrip_actions(client):
    """blender.launch and blender.verify_roundtrip go through the same generic /ui/act/{action_id} and /ui/plan/{action_id} doors as every other governed action -- no bespoke route was added for..."""
    assert {"blender.launch", "blender.verify_roundtrip"} <= set(bff.OPERATIONS)
    assert bff.OPERATIONS["blender.launch"]["params"]["mesh_path"] is str
    assert bff.OPERATIONS["blender.verify_roundtrip"]["params"]["boundary_rel_tolerance"] is float


def test_blender_verify_roundtrip_can_be_planned_and_submitted_through_the_transport(client, monkeypatch):
    csrf = _session(client)
    calls = []

    def fake_command(path, payload=None, method="POST"):
        calls.append((path, payload, method))
        if path == "/plan":
            return {"ready": True, "plan_sha256": "c" * 64}
        return {"status": "SUCCEEDED", "job_id": "job_test", "result": {"verdict": "PASS"}}

    monkeypatch.setattr(bff, "_call_command", fake_command)
    params = {"source_mesh_path": "E:/mesh/src.obj", "edited_mesh_path": "E:/mesh/edited.obj",
              "receipt_path": "E:/mesh/receipt.json"}
    planned = client.post("/ui/plan/blender.verify_roundtrip",
                          headers=_headers(csrf, "blender-verify-plan-key"), json={"params": params})
    assert planned.status_code == 200
    assert planned.json()["read_only"] is True

    submitted = client.post("/ui/act/blender.verify_roundtrip",
                            headers=_headers(csrf, "blender-verify-act-key"),
                            json={"params": {**params, "approved_plan_sha256": "c" * 64}})
    assert submitted.status_code == 200
    assert submitted.json()["result"]["result"]["verdict"] == "PASS"
    assert calls[0][0] == "/plan" and calls[0][1]["dry_run"] is True
    assert [c[0] for c in calls[1:]] == ["/plan", "/submit"]


def test_blender_launch_rejects_an_unknown_mode_parameter_type(client):
    csrf = _session(client)
    r = client.post("/ui/act/blender.launch", headers=_headers(csrf, "blender-launch-key"),
                    json={"params": {"mesh_path": "E:/mesh/src.obj", "mode": "interactive",
                                      "timeout_s": True}})
    assert r.status_code == 400 and "wrong type" in r.text.lower()


def test_nested_provider_parameters_are_type_checked(client):
    csrf = _session(client)
    r = client.post("/ui/act/provider.invoke", headers=_headers(csrf, "provider-key"),
                    json={"params": {"capability_id": "vc_flatten", "input_path": "in",
                                      "output_path": "out", "source_binding": "not-an-object",
                                      "authorization_id": "auth", "launch_packet": {},
                                      "approved_plan_sha256": "a" * 64}})
    assert r.status_code == 400 and "wrong type" in r.text.lower()


def test_provider_plan_can_be_previewed_before_the_governed_submit(client, monkeypatch):
    csrf = _session(client)
    calls = []

    def fake_command(path, payload=None, method="POST"):
        calls.append((path, payload, method))
        return {"schema": "argus-provider-invocation-plan-v1", "ready": True,
                "plan_sha256": "b" * 64}

    monkeypatch.setattr(bff, "_call_command", fake_command)
    params = {"capability_id": "vc_flatten", "input_path": "in", "output_path": "out",
              "source_binding": {"physical_scroll": "PHercTest", "volume_id": "v1",
                                  "acquisition_id": "a1"}, "options": {},
              "authorization_id": "auth", "launch_packet": {"runner_rel": "r"}}
    r = client.post("/ui/plan/provider.invoke", headers=_headers(csrf, "provider-plan-key"),
                    json={"params": params})
    assert r.status_code == 200
    assert r.json()["read_only"] is True
    assert r.json()["plan_hash"]
    assert calls and calls[0][0] == "/plan" and calls[0][1]["dry_run"] is True


def test_numeric_execution_parameters_do_not_accept_boolean_values(client):
    csrf = _session(client)
    r = client.post("/ui/act/acquire.execute", headers=_headers(csrf, "acquire-key"),
                    json={"params": {"url": "https://example.invalid/vol.zarr",
                                      "scroll": "PHercTest", "volume_id": "v1", "phase": "A0",
                                      "official_identity": {}, "authorization_id": "auth",
                                      "launch_packet": {}, "approved_plan_sha256": "a" * 64,
                                      "byte_ceiling": True}})
    assert r.status_code == 400 and "wrong type" in r.text.lower()


def test_an_unknown_parameter_refuses_rather_than_being_ignored(client):
    csrf = _session(client)
    r = client.post("/ui/act/preflight", headers=_headers(csrf),
                    json={"params": {"action": "score", "sneaky": "x"}})
    assert r.status_code == 400 and "unknown" in r.text.lower()


def test_a_missing_required_parameter_refuses(client):
    csrf = _session(client)
    r = client.post("/ui/act/preflight", headers=_headers(csrf), json={"params": {}})
    assert r.status_code == 400


def test_a_wrong_parameter_type_refuses(client):
    csrf = _session(client)
    r = client.post("/ui/act/preflight", headers=_headers(csrf),
                    json={"params": {"action": 12345}})
    assert r.status_code == 400


def test_a_body_that_is_not_json_refuses(client):
    csrf = _session(client)
    r = client.post("/ui/act/preflight", headers=_headers(csrf), content=b"not json")
    assert r.status_code == 400


def test_an_oversized_body_refuses(client):
    csrf = _session(client)
    big = json.dumps({"params": {"action": "x" * 200000}})
    r = client.post("/ui/act/preflight", headers=_headers(csrf), content=big.encode())
    assert r.status_code in (400, 413)



@pytest.mark.parametrize("evil", [
    "preflight; rm -rf /",
    "../../../../etc/passwd",
    "http://evil.example/payload",
    "scripts/fixture_script.py",
    "__import__('os').system('echo pwned')",
])
def test_a_command_path_or_url_in_the_action_id_refuses(client, evil):
    csrf = _session(client)
    r = client.post("/ui/act/" + evil.replace("/", "%2F"), headers=_headers(csrf),
                    json={"params": {}})
    assert r.status_code in (400, 404), evil


def test_there_is_no_passthrough_route():
    """STRUCTURAL."""
    paths = {getattr(r, "path", "") for r in bff.app.routes}
    assert not any("{path" in p or ":path" in p for p in paths), paths
    assert "/cmd" not in paths and "/cmd/{rest}" not in paths


def test_a_malformed_job_id_refuses(client):
    csrf = _session(client)
    r = client.get("/ui/job/../../secrets", headers={"Origin": ALLOWED, "X-Argus-Csrf": csrf})
    assert r.status_code in (400, 404)



def test_an_idempotency_key_is_required(client):
    csrf = _session(client)
    r = client.post("/ui/act/preflight",
                    headers={"Origin": ALLOWED, "X-Argus-Csrf": csrf},
                    json={"params": {"action": "score"}})
    assert r.status_code == 400 and "idempotency" in r.text.lower()


def test_the_same_key_reaches_the_command_service_unchanged(client, monkeypatch):
    """Idempotency is enforced BELOW this transport, by the command service."""
    seen = []

    def fake(path, payload=None, method="POST"):
        if payload:
            seen.append(payload.get("idempotency_key"))
        return {"job_id": "job-1", "state": "done"}

    csrf = _session(client)
    monkeypatch.setattr(bff, "_call_command", fake)
    for _ in range(2):
        client.post("/ui/act/preflight", headers=_headers(csrf, "same-key-abcdefgh"),
                    json={"params": {"action": "score"}})
    submitted = [k for k in seen if k]
    assert submitted and len(set(submitted)) == 1, submitted



def test_a_successful_command_records_one_auditable_entry(client, monkeypatch):
    def fake(path, payload=None, method="POST"):
        return {"job_id": "job-42", "state": "done", "receipt": "artifacts/x/RECEIPT.json"}

    csrf = _session(client)
    monkeypatch.setattr(bff, "_call_command", fake)
    r = client.post("/ui/act/preflight", headers=_headers(csrf),
                    json={"params": {"action": "score"}})
    assert r.status_code == 200
    lines = [json.loads(x) for x in
             bff.AUDIT_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    rec = lines[0]
    for k in ("utc", "actor", "origin", "action", "request_hash", "plan_hash",
              "idempotency_key", "job_id", "terminal_outcome", "receipt"):
        assert k in rec, k
    assert rec["actor"] == "human:ui" and rec["job_id"] == "job-42"


def test_the_plan_hash_changes_when_the_plan_changes(client, monkeypatch):
    """A stale or altered plan must not pass as the one that was approved."""
    plans = [{"cost": 1}, {"cost": 999}]

    def fake(path, payload=None, method="POST"):
        if path == "/plan":
            return plans.pop(0)
        return {"job_id": "j", "state": "done"}

    csrf = _session(client)
    monkeypatch.setattr(bff, "_call_command", fake)
    a = client.post("/ui/act/preflight", headers=_headers(csrf, "k-aaaaaaaa"),
                    json={"params": {"action": "score"}}).json()["plan_hash"]
    b = client.post("/ui/act/preflight", headers=_headers(csrf, "k-bbbbbbbb"),
                    json={"params": {"action": "score"}}).json()["plan_hash"]
    assert a != b



def test_no_carried_operation_can_promote_scientific_admissibility():
    """The transport carries reads and one review-open."""
    forbidden = ("qualify", "promote", "admit", "gate", "seal", "train")
    for name in bff.OPERATIONS:
        assert not any(f in name for f in forbidden), name


def test_model_qualify_is_registered_but_not_reachable_from_a_browser():
    from argus.core import action_registry as AR
    assert "model.qualify" in AR.REGISTRY
    assert "model.qualify" not in bff.OPERATIONS


def test_the_observatory_is_not_this_service():
    """Mutation lives on a separate authority so the read-only service never needs a write route."""
    import argus.service.app as observatory
    assert observatory.app is not bff.app



