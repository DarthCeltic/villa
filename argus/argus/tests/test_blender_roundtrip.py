"""A governed Blender round trip with coordinate/parity receipts."""
from __future__ import annotations

import json
import subprocess
import time

import numpy as np
import pytest

from argus.core import actions as A
from argus.core import action_registry as AR
from argus.core import blender_adapter as BA
from argus.core import blender_roundtrip as BR

import pathlib
FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"
GRID_OBJ = FIXTURE_DIR / "blender_grid_surface.obj"

BLENDER_AVAILABLE = BA.resolve_executable() is not None
requires_blender = pytest.mark.skipif(
    not BLENDER_AVAILABLE, reason="no Blender executable resolvable on this machine "
                                  "(ARGUS_BLENDER / PATH)")


def _write_obj(path, vertices, faces):
    lines = ["# synthetic test perturbation"]
    for v in vertices:
        lines.append("v %.9f %.9f %.9f" % tuple(v))
    for f in faces:
        lines.append("f " + " ".join(str(i + 1) for i in f))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _grid(n=5, spacing=1.0):
    """An n x n flat grid: same shape family as the real fixture, cheap to hand-perturb."""
    verts = [(i * spacing, j * spacing, 0.0) for j in range(n) for i in range(n)]
    faces = []
    for j in range(n - 1):
        for i in range(n - 1):
            a, b, c, d = j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i
            faces.append((a, b, c, d))
    return np.array(verts, dtype=np.float64), faces



def test_parse_obj_recovers_vertices_faces_and_boundary(tmp_path):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    doc = BR.parse_obj(src)
    assert len(doc["vertices"]) == 25
    assert len(doc["faces"]) == 16
    assert len(doc["boundary"]) == 16
    assert doc["vertices"].shape == (25, 3)


def test_parse_obj_refuses_missing_file(tmp_path):
    with pytest.raises(BR.RoundtripRefusal, match="does not exist"):
        BR.parse_obj(tmp_path / "nope.obj")


def test_parse_obj_refuses_non_obj_suffix(tmp_path):
    p = tmp_path / "mesh.ply"
    p.write_text("ply\n", encoding="utf-8")
    with pytest.raises(BR.RoundtripRefusal, match=r"\.obj"):
        BR.parse_obj(p)


def test_parse_obj_refuses_out_of_range_face_index(tmp_path):
    p = tmp_path / "bad.obj"
    p.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 4\n", encoding="utf-8")
    with pytest.raises(BR.RoundtripRefusal, match="outside"):
        BR.parse_obj(p)



def test_interior_only_edit_passes(tmp_path):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    edited = verts.copy()
    interior = [i for i in range(25) if 1 <= i % 5 <= 3 and 1 <= i // 5 <= 3]
    edited[interior, 2] += 2.5
    out = tmp_path / "out.obj"
    _write_obj(out, edited, faces)

    result = BR.verify_roundtrip(src, out)
    assert result["verdict"] == "PASS"
    assert result["problems"] == []
    assert result["max_boundary_displacement"] == 0.0
    assert result["max_interior_displacement"] == pytest.approx(2.5)


def test_boundary_move_is_refused(tmp_path):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    edited = verts.copy()
    edited[0, 0] += 1.0
    out = tmp_path / "out.obj"
    _write_obj(out, edited, faces)

    result = BR.verify_roundtrip(src, out)
    assert result["verdict"] == "REFUSE"
    assert any("COORDINATE_SCALE_DRIFT" in p for p in result["problems"])
    assert result["max_boundary_displacement"] == pytest.approx(1.0)


def test_uniform_scale_of_whole_mesh_is_refused(tmp_path):
    """The signature failure this module exists to catch: a unit-conversion-style bug."""
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    edited = verts * 1.01
    out = tmp_path / "out.obj"
    _write_obj(out, edited, faces)

    result = BR.verify_roundtrip(src, out)
    assert result["verdict"] == "REFUSE"
    assert result["scale_drift_ratio"] == pytest.approx(1.01, rel=1e-6)


def test_numeric_noise_below_tolerance_passes(tmp_path):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    rng = np.random.default_rng(1234)
    edited = verts + rng.uniform(-1e-7, 1e-7, size=verts.shape)
    out = tmp_path / "out.obj"
    _write_obj(out, edited, faces)

    result = BR.verify_roundtrip(src, out)
    assert result["verdict"] == "PASS"


def test_topology_change_is_refused(tmp_path):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    edited_faces = list(faces)
    a, b, c, d = edited_faces.pop(0)
    edited_faces.append((a, b, c))
    edited_faces.append((a, c, d))
    out = tmp_path / "out.obj"
    _write_obj(out, verts, edited_faces)

    result = BR.verify_roundtrip(src, out)
    assert result["verdict"] == "REFUSE"
    assert any("TOPOLOGY_CHANGED" in p for p in result["problems"])
    assert result["max_boundary_displacement"] is None


def test_vertex_count_change_is_refused(tmp_path):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    out = tmp_path / "out.obj"
    extra = np.vstack([verts, [[99.0, 99.0, 99.0]]])
    _write_obj(out, extra, faces)

    result = BR.verify_roundtrip(src, out)
    assert result["verdict"] == "REFUSE"
    assert result["edited_vertex_count"] == 26


def test_tolerance_scales_with_mesh_extent(tmp_path):
    """A tiny mesh and a huge mesh should not share one absolute tolerance."""
    small_verts, faces = _grid(5, spacing=0.01)
    big_verts, _ = _grid(5, spacing=1000.0)
    src_small, src_big = tmp_path / "small.obj", tmp_path / "big.obj"
    _write_obj(src_small, small_verts, faces)
    _write_obj(src_big, big_verts, faces)

    small_result = BR.verify_roundtrip(src_small, src_small)
    big_result = BR.verify_roundtrip(src_big, src_big)
    assert small_result["tolerance_used"] < big_result["tolerance_used"]
    assert small_result["verdict"] == "PASS"
    assert big_result["verdict"] == "PASS"



def test_receipt_records_hashes_and_verdict(tmp_path):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    out = tmp_path / "out.obj"
    _write_obj(out, verts, faces)

    result = BR.verify_roundtrip(src, out)
    receipt = BR.build_receipt(src, out, result, edit_kind="test")
    assert receipt["schema"] == "argus-blender-roundtrip-receipt-v1"
    assert receipt["verdict"] == "PASS"
    assert len(receipt["source_sha256"]) == 64
    assert len(receipt["edited_sha256"]) == 64
    assert receipt["source_sha256"] == receipt["edited_sha256"]
    assert receipt["coordinate_diff"]["max_boundary_displacement"] == 0.0

    written = BR.write_receipt(receipt, tmp_path / "receipt.json")
    assert written.is_file()
    assert json.loads(written.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_plan_launch_never_executes_anything(tmp_path, monkeypatch):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)

    def boom(*a, **kw):
        raise AssertionError("plan_launch must never start a process")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)

    plan = BR.plan_launch(src, "headless_test_sculpt",
                          output_path=tmp_path / "o.obj", report_path=tmp_path / "r.json")
    assert plan["read_only"] is True
    assert plan["ready"] in (True, False)


def test_plan_launch_refuses_missing_mesh(tmp_path):
    plan = BR.plan_launch(tmp_path / "missing.obj", "interactive")
    assert plan["ready"] is False
    assert any("does not exist" in p for p in plan["problems"])


def test_plan_launch_refuses_unknown_mode(tmp_path):
    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    plan = BR.plan_launch(src, "delete_everything")
    assert plan["ready"] is False
    assert any("unknown mode" in p for p in plan["problems"])


class _Completed:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _stub_launch(monkeypatch, codes):
    """Feed BR.launch a scripted sequence of Blender exit codes; return the call/sleep logs."""
    calls, sleeps = [], []
    monkeypatch.setattr(BR, "plan_launch", lambda *a, **kw: {"ready": True, "argv": ["blender", "-b"]})
    monkeypatch.setattr(BR.time, "sleep", sleeps.append)

    def fake_run(argv, **kw):
        calls.append(argv)
        return _Completed(codes[len(calls) - 1], stderr="boom")
    monkeypatch.setattr(BR.subprocess, "run", fake_run)
    return calls, sleeps


def test_a_transient_commit_limit_exit_is_retried_then_succeeds(monkeypatch, tmp_path):
    calls, sleeps = _stub_launch(monkeypatch, [0xC000012D, 0])
    result = BR.launch(tmp_path / "m.obj", "headless_roundtrip")
    assert result["status"] == "RAN" and len(calls) == 2 and len(sleeps) == 1


def test_a_persistent_commit_limit_exit_is_reported_as_a_memory_shortage(monkeypatch, tmp_path):
    calls, _ = _stub_launch(monkeypatch, [0xC000012D] * 3)
    with pytest.raises(BR.RoundtripRefusal) as err:
        BR.launch(tmp_path / "m.obj", "headless_roundtrip")
    assert len(calls) == 3
    assert "0xC000012D" in str(err.value) and "not a fault in the mesh" in str(err.value)


def test_a_signed_negative_commit_limit_exit_is_recognised(monkeypatch, tmp_path):
    calls, _ = _stub_launch(monkeypatch, [0xC000012D - 2**32, 0])
    assert BR.launch(tmp_path / "m.obj", "headless_roundtrip")["status"] == "RAN"
    assert len(calls) == 2


def test_any_other_blender_failure_is_never_retried(monkeypatch, tmp_path):
    calls, sleeps = _stub_launch(monkeypatch, [1, 0])
    with pytest.raises(BR.RoundtripRefusal) as err:
        BR.launch(tmp_path / "m.obj", "headless_roundtrip")
    assert len(calls) == 1 and not sleeps and "exited 1" in str(err.value)



@requires_blender
def test_real_blender_headless_sculpt_edit_round_trips_and_passes(tmp_path):
    out = tmp_path / "sculpt.obj"
    report = tmp_path / "sculpt_report.json"
    result = BR.launch(GRID_OBJ, "headless_test_sculpt", output_path=out, report_path=report)
    assert result["status"] == "RAN"
    assert result["returncode"] == 0
    assert out.is_file()
    assert report.is_file()
    assert result["report"]["edit_kind"] == "sculpt"

    verify = BR.verify_roundtrip(GRID_OBJ, out)
    assert verify["verdict"] == "PASS", verify["problems"]
    assert verify["max_boundary_displacement"] == 0.0
    assert verify["max_interior_displacement"] > 0.01


@requires_blender
def test_real_blender_headless_drift_edit_is_refused(tmp_path):
    out = tmp_path / "drift.obj"
    report = tmp_path / "drift_report.json"
    result = BR.launch(GRID_OBJ, "headless_test_drift", output_path=out, report_path=report)
    assert result["status"] == "RAN"
    assert out.is_file()

    verify = BR.verify_roundtrip(GRID_OBJ, out)
    assert verify["verdict"] == "REFUSE"
    assert any("COORDINATE_SCALE_DRIFT" in p for p in verify["problems"])
    assert verify["scale_drift_ratio"] == pytest.approx(1.002, rel=1e-6)


@requires_blender
def test_real_blender_conservative_roundtrip_mode_still_works(tmp_path):
    """The pre-existing weld/normals mode (blender_adapter) is untouched by this work."""
    out = tmp_path / "conservative.obj"
    report = tmp_path / "conservative_report.json"
    result = BR.launch(GRID_OBJ, "headless_roundtrip", output_path=out, report_path=report)
    assert result["status"] == "RAN"
    assert out.is_file()
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert doc["schema"] == "argus-blender-mesh-inspection-v1"


@requires_blender
def test_real_blender_interactive_mode_actually_spawns_and_is_killable():
    """Proves the real executable path resolves and can be invoked, without a hung GUI window."""
    result = BR.launch(GRID_OBJ, "interactive")
    assert result["status"] == "LAUNCHED"
    pid = result["pid"]
    try:
        time.sleep(2.0)
        check = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                               capture_output=True, text=True, timeout=15)
        assert str(pid) in check.stdout, "the spawned Blender process was not observed running"
    finally:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=15)


@requires_blender
def test_launch_refuses_to_overwrite_the_source_mesh(tmp_path):
    src = tmp_path / "src.obj"
    verts, faces = _grid(5)
    _write_obj(src, verts, faces)
    with pytest.raises(BR.RoundtripRefusal, match="must not overwrite"):
        BR.launch(src, "headless_test_sculpt", output_path=src, report_path=tmp_path / "r.json")



def _isolate_leases(monkeypatch, tmp_path):
    monkeypatch.setattr(AR.A, "LEASES", tmp_path / "leases")


def test_blender_launch_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ARGUS_BLENDER_EXECUTION_ENABLED", raising=False)
    spec = A.ActionSpec(action="blender.launch", actor="human:test", request_id="r1",
                        idempotency_key="i1",
                        params={"mesh_path": str(GRID_OBJ), "mode": "headless_test_sculpt"})
    with pytest.raises(A.Refused) as exc:
        AR.do_blender_launch(spec)
    assert exc.value.code == "BLENDER_DISABLED"


def test_blender_verify_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ARGUS_BLENDER_EXECUTION_ENABLED", raising=False)
    spec = A.ActionSpec(action="blender.verify_roundtrip", actor="human:test", request_id="r1",
                        idempotency_key="i1",
                        params={"source_mesh_path": str(GRID_OBJ),
                               "edited_mesh_path": str(GRID_OBJ),
                               "receipt_path": str(tmp_path / "r.json")})
    with pytest.raises(A.Refused) as exc:
        AR.do_blender_verify(spec)
    assert exc.value.code == "BLENDER_DISABLED"


def test_blender_launch_plan_names_missing_params():
    plan = AR.plan_blender_launch({})
    assert plan["ready"] is False
    assert "mesh_path" in plan["missing"]
    assert "mode" in plan["missing"]


def test_blender_verify_passes_and_writes_receipt_via_governed_action(monkeypatch, tmp_path):
    """A no-drift round trip, run through the exact door the UI uses."""
    _isolate_leases(monkeypatch, tmp_path)
    monkeypatch.setenv("ARGUS_BLENDER_EXECUTION_ENABLED", "1")
    edited = tmp_path / "edited.obj"
    edited.write_text(GRID_OBJ.read_text(encoding="utf-8"), encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    params = {"source_mesh_path": str(GRID_OBJ), "edited_mesh_path": str(edited),
             "receipt_path": str(receipt_path), "edit_kind": "test-passthrough"}
    params["approved_plan_sha256"] = AR.plan_blender_verify(params)["plan_sha256"]
    spec = A.ActionSpec(action="blender.verify_roundtrip", actor="human:test", request_id="r1",
                        idempotency_key="i1", params=params)
    result = AR.do_blender_verify(spec)
    assert result["status"] == "OK"
    assert result["verdict"] == "PASS"
    assert receipt_path.is_file()
    assert not (tmp_path / "leases" / "data.lease").exists()


def test_blender_verify_refuses_drift_but_still_writes_the_receipt(monkeypatch, tmp_path):
    _isolate_leases(monkeypatch, tmp_path)
    monkeypatch.setenv("ARGUS_BLENDER_EXECUTION_ENABLED", "1")
    verts, faces = _grid(7)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    drifted = tmp_path / "drifted.obj"
    _write_obj(drifted, verts * 1.05, faces)
    receipt_path = tmp_path / "receipt.json"
    params = {"source_mesh_path": str(src), "edited_mesh_path": str(drifted),
             "receipt_path": str(receipt_path), "edit_kind": "test-drift"}
    params["approved_plan_sha256"] = AR.plan_blender_verify(params)["plan_sha256"]
    spec = A.ActionSpec(action="blender.verify_roundtrip", actor="human:test", request_id="r2",
                        idempotency_key="i2", params=params)
    with pytest.raises(A.Refused) as exc:
        AR.do_blender_verify(spec)
    assert exc.value.code == "COORDINATE_SCALE_DRIFT"
    assert receipt_path.is_file()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["verdict"] == "REFUSE"
    assert not (tmp_path / "leases" / "data.lease").exists()


def test_submit_end_to_end_through_the_one_door(monkeypatch, tmp_path):
    """The exact call shape the UI's GovernedAction component sends."""
    monkeypatch.setattr(AR.A, "STATE", tmp_path / "state")
    monkeypatch.setattr(AR.A, "JOBS", tmp_path / "state" / "jobs")
    monkeypatch.setattr(AR.A, "LEASES", tmp_path / "state" / "leases")
    monkeypatch.setattr(AR.A, "AUDIT", tmp_path / "state" / "audit.jsonl")
    monkeypatch.setattr(AR.A, "IDEMPOTENCY", tmp_path / "state" / "idempotency.json")
    monkeypatch.setenv("ARGUS_BLENDER_EXECUTION_ENABLED", "1")

    edited = tmp_path / "edited.obj"
    edited.write_text(GRID_OBJ.read_text(encoding="utf-8"), encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    base_params = {"source_mesh_path": str(GRID_OBJ), "edited_mesh_path": str(edited),
                   "receipt_path": str(receipt_path)}
    approved = AR.plan_blender_verify(base_params)["plan_sha256"]
    spec_obj = {
        "action": "blender.verify_roundtrip", "actor": "human:test", "request_id": "req-1",
        "idempotency_key": "idem-1",
        "params": {**base_params, "approved_plan_sha256": approved},
    }
    result = AR.submit(spec_obj)
    assert result["status"] == "SUCCEEDED"
    assert result["result"]["verdict"] == "PASS"

    again = AR.submit(spec_obj)
    assert again["status"] == "DUPLICATE"
    assert again["job_id"] == result["job_id"]


def test_submit_refuses_an_unapproved_plan_hash(monkeypatch, tmp_path):
    monkeypatch.setattr(AR.A, "STATE", tmp_path / "state")
    monkeypatch.setattr(AR.A, "JOBS", tmp_path / "state" / "jobs")
    monkeypatch.setattr(AR.A, "LEASES", tmp_path / "state" / "leases")
    monkeypatch.setattr(AR.A, "AUDIT", tmp_path / "state" / "audit.jsonl")
    monkeypatch.setattr(AR.A, "IDEMPOTENCY", tmp_path / "state" / "idempotency.json")
    monkeypatch.setenv("ARGUS_BLENDER_EXECUTION_ENABLED", "1")
    spec_obj = {
        "action": "blender.verify_roundtrip", "actor": "human:test", "request_id": "req-2",
        "idempotency_key": "idem-2",
        "params": {"source_mesh_path": str(GRID_OBJ), "edited_mesh_path": str(GRID_OBJ),
                   "receipt_path": str(tmp_path / "receipt.json"),
                   "approved_plan_sha256": "0" * 64},
    }
    result = AR.submit(spec_obj)
    assert result["status"] == "REFUSED"
    assert result["result"]["code"] == "PLAN_NOT_APPROVED"
    assert not (tmp_path / "receipt.json").exists()


def test_bff_end_to_end_plan_then_act_roundtrips_for_real(monkeypatch, tmp_path):
    """The exact browser path: /ui/plan then /ui/act, with the command service wired to the real action_registry (not HTTP-mocked) -- this is what actually proves the plan-hash the UI carries forward is..."""
    from fastapi.testclient import TestClient
    from argus.service import bff

    def fake_call_command(path, payload=None, method="POST"):
        if path == "/plan":
            return AR.plan(payload)
        if path == "/submit":
            return AR.submit(payload)
        raise AssertionError(path)

    monkeypatch.setattr(bff, "_call_command", fake_call_command)
    monkeypatch.setattr(bff, "TOKEN_PATH", tmp_path / "command_token")
    (tmp_path / "command_token").write_text("unused-in-this-test", encoding="utf-8")
    monkeypatch.setattr(bff, "UI_ACCESS_KEY_PATH", tmp_path / "ui_access_key")
    (tmp_path / "ui_access_key").write_text("TEST-UI-ACCESS-KEY", encoding="utf-8")
    monkeypatch.setattr(bff, "AUDIT_PATH", tmp_path / "bff_audit.jsonl")
    monkeypatch.setattr(AR.A, "STATE", tmp_path / "state")
    monkeypatch.setattr(AR.A, "JOBS", tmp_path / "state" / "jobs")
    monkeypatch.setattr(AR.A, "LEASES", tmp_path / "state" / "leases")
    monkeypatch.setattr(AR.A, "AUDIT", tmp_path / "state" / "audit.jsonl")
    monkeypatch.setattr(AR.A, "IDEMPOTENCY", tmp_path / "state" / "idempotency.json")
    monkeypatch.setenv("ARGUS_BLENDER_EXECUTION_ENABLED", "1")
    bff._SESSIONS.clear()
    client = TestClient(bff.app)
    allowed = "http://127.0.0.1:5173"
    csrf = client.post("/ui/session", headers={
        "Origin": allowed, "X-Argus-Access-Key": "TEST-UI-ACCESS-KEY"}).json()["csrf"]
    headers = {"Origin": allowed, "X-Argus-Csrf": csrf, "Content-Type": "application/json"}
    edited = tmp_path / "edited.obj"
    edited.write_text(GRID_OBJ.read_text(encoding="utf-8"), encoding="utf-8")
    params = {"source_mesh_path": str(GRID_OBJ), "edited_mesh_path": str(edited),
             "receipt_path": str(tmp_path / "receipt.json")}

    planned = client.post("/ui/plan/blender.verify_roundtrip",
                          headers={**headers, "X-Idempotency-Key": "e2e-plan-key"},
                          json={"params": params})
    assert planned.status_code == 200
    approved = planned.json()["plan"]["plan_sha256"]

    submitted = client.post("/ui/act/blender.verify_roundtrip",
                            headers={**headers, "X-Idempotency-Key": "e2e-act-key"},
                            json={"params": {**params, "approved_plan_sha256": approved}})
    assert submitted.status_code == 200
    body = submitted.json()["result"]
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["verdict"] == "PASS"
    assert (tmp_path / "receipt.json").is_file()


def test_bff_allowlist_carries_the_new_actions():
    from argus.service import bff
    assert {"blender.launch", "blender.verify_roundtrip"} <= set(bff.OPERATIONS)
    assert bff.OPERATIONS["blender.launch"]["params"]["mesh_path"] is str
    assert bff.OPERATIONS["blender.verify_roundtrip"]["required"] == (
        "source_mesh_path", "edited_mesh_path", "receipt_path")



def test_service_exposes_a_read_only_blender_plan_route(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from argus.core import paths as _P
    from argus.service.app import app

    monkeypatch.setattr(_P, "serve_roots", lambda: [tmp_path])

    verts, faces = _grid(5)
    src = tmp_path / "src.obj"
    _write_obj(src, verts, faces)
    with TestClient(app) as client:
        response = client.get("/api/providers/blender/plan", params={
            "mesh_path": str(src), "mode": "headless_test_sculpt",
            "output_path": str(tmp_path / "out.obj"), "report_path": str(tmp_path / "r.json")})
    assert response.status_code == 200
    body = response.json()
    assert body["read_only"] is True
    assert body["requests_made"] == 0
    assert not (tmp_path / "out.obj").exists()
