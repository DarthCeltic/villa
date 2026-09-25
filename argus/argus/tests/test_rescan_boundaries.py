"""Second-pass security regressions: each test reproduces something the first round of fixes left open, then shows it closed."""
import asyncio
import concurrent.futures
import gzip
import io
import json
import os
import re
import threading
import time
import types

import numpy as np
import pytest
from fastapi.testclient import TestClient

from argus.core import actions as A
from argus.core import ledger_v2 as LM
from argus.core import paths as P
from argus.core import safe_names as SN
from argus.service import request_guard as RG



def test_a_chunked_body_over_the_cap_is_refused_before_authentication_reads_it():
    from argus.service import command as C

    def chunks():
        for _ in range(48):
            yield b"x" * 65536

    r = TestClient(C.app).post("/plan", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["limit_bytes"] == C.MAX_REQUEST_BYTES


def test_the_body_limit_stops_reading_at_the_cap_instead_of_draining_the_sender():
    delivered = []
    sent = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break

    async def receive():
        delivered.append(1)
        return {"type": "http.request", "body": b"x" * 1000, "more_body": True}

    async def send(message):
        sent.append(message)

    asyncio.run(RG.BodyLimit(app, 10_000)({"type": "http", "headers": []}, receive, send))
    assert len(delivered) <= 11 and sent[0]["status"] == 413


@pytest.mark.parametrize("bad", ["localhost:evil", "[::1].evil.example", "127.0.0.1:99999x", "127.0.0.1:80:80", "", "localhost@evil.example", "local host", "[::1"])
def test_a_host_header_that_does_not_parse_is_never_allowed(bad):
    assert RG.host_ok(bad) is False


@pytest.mark.parametrize("good", ["127.0.0.1", "127.0.0.1:8787", "LOCALHOST:8787", "[::1]", "[::1]:8787", "observe:8787"])
def test_the_hosts_the_stack_really_sends_are_still_allowed(good):
    assert RG.host_ok(good) is True


def test_the_job_control_app_refuses_a_rebound_host_and_a_cross_site_request():
    from argus.service import jobs_api as J

    c = TestClient(J.app)
    assert c.get("/api/jobs/actions", headers={"host": "evil.example"}).status_code == 421
    assert c.get("/api/jobs/actions", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert c.get("/api/jobs/actions", headers={"host": "127.0.0.1:8790"}).status_code == 200


def test_every_file_the_observe_service_streams_carries_the_active_content_headers():
    text = open(os.path.join(os.path.dirname(__file__), "..", "service", "app.py"), encoding="utf-8").read()
    calls = re.findall(r"FileResponse\((?:[^()]|\([^()]*\))*\)", text)
    assert len(calls) >= 3
    assert all("file_headers(" in c for c in calls), [c for c in calls if "file_headers(" not in c]


def test_an_aborted_websocket_handshake_does_not_leak_a_connection_slot():
    from argus.service import app as A_

    class Aborting:
        headers = {"origin": sorted(A_.WS_ALLOWED_ORIGINS)[0]}

        async def close(self, code=1000):
            pass

        async def accept(self):
            raise OSError("client went away mid-handshake")

    before = A_._WS_OPEN
    for _ in range(A_.WS_MAX_CONNECTIONS + 4):
        with pytest.raises(OSError):
            asyncio.run(A_.ws_observatory(Aborting()))
    assert A_._WS_OPEN == before


def test_nginx_refuses_a_cross_site_write_before_reading_its_body():
    conf = open(os.path.join(os.path.dirname(__file__), "..", "..", "docker", "nginx.conf"), encoding="utf-8").read()
    assert 'map "$http_sec_fetch_site:$request_method" $argus_cross_site_write' in conf
    assert re.search(r"if \(\$argus_cross_site_write\) \{\s*return 403;", conf)



@pytest.fixture()
def roots(tmp_path, monkeypatch):
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setattr(P, "serve_roots", lambda: [inside])
    return inside


def test_the_shared_confinement_helper_names_the_first_path_outside_the_roots(roots, tmp_path):
    assert P.outside_served_roots(a=str(roots / "x"), b=None, c="") is None
    assert P.outside_served_roots(a=str(roots / "x"), b=str(tmp_path / "elsewhere")) == "b"
    assert P.outside_served_roots(a="\\\\host\\share\\f") == "a"
    assert P.outside_served_roots(a="//host/share/f") == "a"
    assert P.outside_served_roots(url_labels=("a", "b"), a="s3://bucket/volume.zarr/0", b="https://example.org/x") is None
    assert P.outside_served_roots(a=str(roots / ".." / "elsewhere")) == "a"


def test_a_path_valued_option_cannot_escape_the_roots_through_the_adapter(roots, tmp_path):
    from argus.core import villa_provider_adapter as VA

    outside = tmp_path / "secret.json"
    outside.write_text("{}")
    with VA.confined_to_served_roots():
        with pytest.raises(VA.ProviderRefusal, match="outside ARGUS's declared roots"):
            VA._path(str(outside), "fiber_json", must_exist=True)
        assert VA._path(str(roots), "input_path", must_exist=True) == roots.resolve()
    assert VA._path(str(outside), "fiber_json", must_exist=True) == outside.resolve()


def test_the_adapter_never_stats_a_unc_path_and_does_not_echo_a_missing_path(roots, monkeypatch):
    from argus.core import villa_provider_adapter as VA

    stats = []
    real = os.stat
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: stats.append(str(p)) or real(p, *a, **k))
    with pytest.raises(VA.ProviderRefusal, match="network"):
        VA._path("\\\\nx-host\\share\\f", "input_path", must_exist=True)
    assert not [s for s in stats if "nx-host" in s]
    with pytest.raises(VA.ProviderRefusal) as err:
        VA._path(str(roots / "not-there"), "input_path", must_exist=True)
    assert "not-there" not in str(err.value)


def test_the_read_only_plan_routes_refuse_paths_outside_the_roots(roots, tmp_path):
    from argus.service.app import app

    outside = tmp_path / "outside"
    outside.mkdir()
    c = TestClient(app)
    r = c.get("/api/providers/surface-preflight/plan", params={
        "surface": str(outside), "volume": "s3://vesuvius/x.zarr", "output": str(roots / "o.json"),
        "physical_scroll": "PHercTest", "volume_id": "v", "acquisition_id": "a"})
    assert r.status_code == 200 and r.json()["state"] == "REFUSED" and "surface" in r.json()["why"]
    r = c.get("/api/providers/hecate/plan", params={
        "scroll": "PHerc0175A", "acquisition_id": "a", "input_render": str(outside / "r.npy"), "spacing_um": 9.6,
        "source_script": str(roots / "h.py"), "checkpoint": str(roots / "c.pt"), "python_executable": str(roots / "p")})
    assert r.json()["state"] == "REFUSED" and "input_render" in r.json()["why"]
    r = c.get("/api/providers/lasagna/plan", params={
        "scroll": "PHerc0175A", "acquisition_id": "a", "input_zarr": "s3://o/v.zarr/0",
        "checkpoint": str(outside / "m.pt"), "output_manifest": str(roots / "p.json")})
    assert r.json()["state"] == "REFUSED" and "checkpoint" in r.json()["why"]
    r = c.get("/api/providers/blender/plan", params={"mesh_path": str(outside / "m.obj"), "mode": "interactive"})
    assert r.status_code == 200 and r.json()["plan"]["ready"] is False and "declared roots" in r.json()["plan"]["problems"][0]
    r = c.get("/api/profile/plan", params={"scroll": "PHerc0139", "receipt_path": "\\\\host\\share\\r.json"})
    assert r.status_code == 400 and r.json()["error"] == "REMOTE_PATH_REFUSED"


def test_a_unc_packet_reference_is_refused_before_the_disk_is_touched(monkeypatch):
    from argus.core import interpretation_actions as IA

    stats = []
    real = os.stat
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: stats.append(str(p)) or real(p, *a, **k))
    with pytest.raises(A.Refused) as err:
        IA.resolve_packet("PHerc0175A", "\\\\nx-host\\share\\packet")
    assert err.value.code == "OUTSIDE_PACKETS_ROOT" and not [s for s in stats if "nx-host" in s]


def test_a_forced_refresh_is_rechecked_under_the_lock_so_a_stampede_rebuilds_once():
    from argus.service import receipts as R

    builds = []

    def build(**kw):
        builds.append(1)
        time.sleep(0.15)
        return {"generated_at": time.time(), "n": len(builds)}

    cache = R.Cache(build, 300.0, "generated_at")
    cache.value = {"generated_at": time.time() - 3600, "n": 0}
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: cache.get(force=True, min_force_age_s=R.Cache.FORCE_MIN_AGE_S), range(8)))
    assert len(builds) == 1


def _store(tmp_path, dtype="|u1", shape=(64, 64, 64), chunks=(32, 32, 32)):
    root = tmp_path / "s.zarr"
    (root / "0").mkdir(parents=True)
    (root / ".zattrs").write_text(json.dumps({"multiscales": [{"datasets": [{"path": "0"}]}]}))
    (root / "0" / ".zarray").write_text(json.dumps({"zarr_format": 2, "shape": list(shape), "chunks": list(chunks), "dtype": dtype,
                                                    "compressor": None, "fill_value": 0, "filters": None, "order": "C"}))
    return root


def test_the_brick_ceiling_counts_the_source_voxel_width(tmp_path):
    from argus.core import volume_brick as VB

    ok = VB.plan(_store(tmp_path, "|u1", (256, 256, 256), (64, 64, 64)), "0", origin=[0, 0, 0], edge_or_shape=128, budget_bytes=VB.HARD_MAX_BYTES)
    assert ok["source_bytes"] == 128 ** 3
    wide = _store(tmp_path / "w", "<f8", (512, 512, 512), (64, 64, 64))
    with pytest.raises(VB.BrickRefused) as err:
        VB.plan(wide, "0", origin=[0, 0, 0], edge_or_shape=512, budget_bytes=VB.HARD_MAX_BYTES)
    assert err.value.code == "BRICK_TOO_LARGE" and "source bytes" in err.value.why
    assert VB.plan(wide, "0", origin=[0, 0, 0], edge_or_shape=128, budget_bytes=VB.HARD_MAX_BYTES)["source_bytes"] == 128 ** 3 * 8


def test_the_display_mapping_is_unchanged_by_working_in_slabs():
    from argus.core import volume_brick as VB
    from argus.core.planes import CLIP_MAX

    rng = np.random.default_rng(3)
    raw = rng.integers(0, 2 * CLIP_MAX, size=(37, 41, 43)).astype(np.uint16)
    old = np.ascontiguousarray((np.clip(raw.astype(np.float32), 0, CLIP_MAX) * (255.0 / CLIP_MAX)).astype(np.uint8))
    assert np.array_equal(VB.display_bytes(raw), old)
    tiny = types.SimpleNamespace()
    assert VB._DISPLAY_SLAB_BYTES > 0 and tiny is not None


def test_a_shape_that_wrapped_int64_to_zero_can_no_longer_plan_an_impossible_brick(tmp_path):
    from argus.core import volume_brick as VB

    huge = _store(tmp_path, "|u1", (2 ** 22, 2 ** 22, 2 ** 22), (64, 64, 64))
    with pytest.raises(VB.BrickRefused):
        VB.plan(huge, "0", origin=[0, 0, 0], edge_or_shape=[2 ** 22, 2 ** 22, 2 ** 22], budget_bytes=2 ** 62)


@pytest.mark.parametrize("shape,chunks", [([64, 64, 64], [0, 32, 32]), ([64, 64, 64], [-1, 32, 32]), ([64, 64, 64], [32, 32]), ([64, 64, 64.5], [32, 32, 32]), ([True, 64, 64], [32, 32, 32])])
def test_a_store_that_declares_unusable_chunks_is_not_openable(tmp_path, shape, chunks):
    from argus.core import zarr_volume as ZV

    root = _store(tmp_path)
    (root / "0" / ".zarray").write_text(json.dumps({"shape": shape, "chunks": chunks, "dtype": "|u1"}))
    level = ZV.probe_store(root)["levels"][0]
    assert level["openable"] is False and ".zarray" in level["why_not_openable"]
    with pytest.raises(ZV.ZarrVolumeRefusal):
        ZV.missing_chunks_in_region(root, "0", 0, 8, 0, 8, 0, 8)


def test_a_region_that_needs_millions_of_chunk_lookups_is_refused(tmp_path):
    from argus.core import zarr_volume as ZV

    root = _store(tmp_path, shape=(4096, 4096, 4096), chunks=(1, 1, 1))
    with pytest.raises(ZV.ZarrVolumeRefusal, match="limit"):
        ZV.missing_chunks_in_region(root, "0", 0, 256, 0, 256, 0, 256)


@pytest.mark.parametrize("bad", ["abc\n", "0\n", "CON", "nul.txt", "x.", "a\x00b", "COM1", "lpt9.log"])
def test_a_name_with_a_newline_a_trailing_dot_or_a_device_name_is_not_a_plain_name(bad):
    assert not SN.is_safe_name(bad) and not SN.is_safe_level(bad)
    with pytest.raises(A.Refused):
        A.job_dir(bad)


def test_ordinary_names_are_still_safe():
    for ok in ("PHerc0175A", "s0", "0", "job_1", "a.b-c"):
        assert SN.is_safe_name(ok)
    assert SN.is_safe_level("0") and SN.is_safe_level("s1")


def test_volume_meta_with_a_hostile_scroll_is_a_client_error_not_a_crash(tmp_path, monkeypatch):
    from argus.core import scroll_dataset_metadata as SDM
    from argus.service.app import app

    from argus.service import app as APP

    store = _store(tmp_path)
    monkeypatch.setattr(APP, "VOLUME_SERVE_ROOTS", [tmp_path])
    r = TestClient(app).get("/api/volume_meta", params={"store": str(store), "scroll": "../x"})
    assert r.status_code == 400, r.text
    real = SDM.identity_preflight_against_probe
    monkeypatch.setattr(SDM, "identity_preflight_against_probe", lambda scroll, probe: real("PHerc0175A", probe))
    assert TestClient(app).get("/api/volume_meta", params={"store": str(store), "scroll": "PHerc0175A"}).status_code == 200


def test_acquisition_refuses_url_metacharacters_a_flat_shape_and_a_full_disk(monkeypatch):
    from argus.core import volume_acquisition as VA

    zarray = {"shape": [10, 10, 10], "chunks": [5, 5, 5], "dtype": "|u1"}
    url = "https://dl.ash2txt.org/full-scrolls/Scroll1/PHercParis4.volpkg/volumes_zarr_standardized/54keV_7.91um_Scroll1A.zarr"
    ok = VA.plan(url=url, array_path="0", roi="0,5,0,5,0,5", phase="A1", zarray_meta=zarray)
    assert ok["keys"]
    for bad_path in ("0?x=1", "0#f", "%2e%2e", "0 1", "a\nb"):
        with pytest.raises(VA.AcquisitionRefusal):
            VA.plan(url=url, array_path=bad_path, roi="0,5,0,5,0,5", phase="A1", zarray_meta=zarray)
    for bad_meta in ({"shape": [10], "chunks": [5], "dtype": "|u1"}, {"shape": [10, 10], "chunks": [5, 5], "dtype": "|u1"}):
        with pytest.raises(VA.AcquisitionRefusal):
            VA.plan(url=url, array_path="0", roi="0,5,0,5,0,5", phase="A1", zarray_meta=bad_meta)
    for bad_url in (url.replace("full-scrolls/", "full-scrolls/../"), url.replace("Scroll1A", "Scroll1A%2e")):
        with pytest.raises(VA.AcquisitionRefusal):
            VA.plan(url=bad_url, array_path="0", roi="0,5,0,5,0,5", phase="A1", zarray_meta=zarray)
    assert VA.FREE_SPACE_RESERVE_BYTES >= 1 << 30


def test_an_update_record_cannot_be_named_by_a_path(tmp_path, monkeypatch):
    from argus.core import provider_updates as PU
    from argus.core import update_store as US

    monkeypatch.setattr(US, "root", lambda: tmp_path)
    for source_id, revision in (("../evil", "abcdef1"), ("villa-main", "../../../evil"), ("villa-main", "a/b"), ("villa-main", "a\\b"), ("villa-main", "abc\n")):
        with pytest.raises(ValueError):
            US._update_path(source_id, revision)
    assert US._update_path("villa-main", "sha256:abc123").parent == tmp_path / "records" / "villa-main"
    with pytest.raises(PU.UpdateRefused) as err:
        PU._load_update("villa-main", "../../../evil")
    assert err.value.code == "BAD_REVISION"



@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STATE", tmp_path / "state")
    monkeypatch.setattr(A, "LEASES", tmp_path / "state" / "leases")
    monkeypatch.setattr(A, "JOBS", tmp_path / "state" / "jobs")
    monkeypatch.setattr(A, "AUDIT", tmp_path / "state" / "audit.jsonl")
    monkeypatch.setattr(A, "IDEMPOTENCY", tmp_path / "state" / "idempotency.json")
    monkeypatch.setattr(LM, "v2_path", lambda: tmp_path / "state" / "ledger_v2.jsonl")
    return tmp_path / "state"


def test_the_unsealed_audit_keeps_its_chain_under_concurrent_writers(state):
    def writer(n):
        for i in range(15):
            A.audit({"event": "t", "w": n, "i": i})

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join(120) for t in threads]
    chain = A.verify_audit_chain()
    assert chain["status"] == "INTACT" and len(A.AUDIT.read_text().splitlines()) == 90


def test_a_dry_run_and_the_real_run_with_one_key_are_different_requests(state, monkeypatch):
    from argus.core import action_registry as R

    ran = []
    name = next(iter(R.REGISTRY))
    plan_fn = lambda params: {"leases": [], "changes": []}
    monkeypatch.setitem(R.REGISTRY, name, (plan_fn, lambda spec: ran.append(spec.action) or {"status": "OK"}))
    spec = {"action": name, "actor": "human:test", "request_id": "r1", "idempotency_key": "k1", "params": {}}
    assert R.submit(dict(spec, dry_run=True))["status"] == "DRY_RUN"
    real = R.submit(dict(spec))
    assert real["status"] == "SUCCEEDED" and ran == [name]
    assert R.submit(dict(spec))["status"] == "DUPLICATE" and ran == [name]


def test_a_submit_that_cannot_be_recorded_frees_its_key_and_says_the_action_did_not_run(state, monkeypatch):
    from argus.core import action_registry as R

    ran = []
    name = next(iter(R.REGISTRY))
    monkeypatch.setitem(R.REGISTRY, name, (lambda params: {"leases": []}, lambda spec: ran.append(1) or {"status": "OK"}))
    spec = {"action": name, "actor": "human:test", "request_id": "r1", "idempotency_key": "k-audit", "params": {}}
    real_audit = A.audit
    monkeypatch.setattr(A, "audit", lambda event: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        R.submit(dict(spec))
    assert not ran
    monkeypatch.setattr(A, "audit", real_audit)
    assert R.submit(dict(spec))["status"] == "SUCCEEDED" and ran == [1]


def test_a_claim_whose_process_died_before_writing_the_job_is_dropped_by_reconcile(state):
    assert A.claim_idempotency("orphan", "job_ghost", "fp") is None
    assert A.claim_idempotency("orphan", "job_x", "fp")["job_id"] == "job_ghost"
    A.reconcile_jobs(min_age_s=0, now=time.time() + 5)
    assert A.claim_idempotency("orphan", "job_new", "fp") is None


def test_reconcile_skips_a_damaged_job_record_and_still_reconciles_the_rest(state):
    (A.JOBS / "job_bad").mkdir(parents=True)
    (A.JOBS / "job_bad" / "job.json").write_text('{"job_id": "job_bad", "state": "RUNNING", "plan": 5, "started_utc": 7}')
    (A.JOBS / "job_list").mkdir()
    (A.JOBS / "job_list" / "job.json").write_text("[1, 2]")
    A.write_job({"job_id": "job_ok", "state": "RUNNING", "started_utc": "2020-01-01T00:00:00Z", "plan": {"leases": []}, "action": "x"})
    changed = A.reconcile_jobs()
    assert "job_ok" in changed and A.read_job("job_ok")["state"] == "INTERRUPTED"


def test_a_job_that_finished_meanwhile_is_not_overwritten_by_reconcile(state, monkeypatch):
    A.write_job({"job_id": "job_race", "state": "RUNNING", "started_utc": "2020-01-01T00:00:00Z", "plan": {"leases": []}, "action": "x"})
    real = A.lease_holder_alive

    def finish_first(kind):
        return real(kind)

    original_lock = A._lease_lock

    @__import__("contextlib").contextmanager
    def lock_then_finish(kind, **kw):
        if kind == "jobs":
            A._write_job_unlocked({"job_id": "job_race", "state": "SUCCEEDED", "started_utc": "2020-01-01T00:00:00Z", "plan": {"leases": []}, "action": "x"})
        with original_lock(kind, **kw):
            yield

    monkeypatch.setattr(A, "_lease_lock", lock_then_finish)
    assert A.reconcile_jobs() == [] and A.read_job("job_race")["state"] == "SUCCEEDED"


def test_a_corrupt_idempotency_map_is_set_aside_not_silently_forgotten(state):
    A.STATE.mkdir(parents=True)
    A.IDEMPOTENCY.write_text("{not json")
    assert A.claim_idempotency("k", "job_1", "fp") is None
    assert list(A.STATE.glob("idempotency.json.corrupt-*"))
    assert json.loads(A.IDEMPOTENCY.read_text())["k"]["job_id"] == "job_1"


def test_concurrent_writers_of_one_job_record_never_fail_or_leave_it_torn(state):
    A.write_job({"job_id": "job_w", "state": "RUNNING"})
    errors = []

    def write(n):
        try:
            for i in range(25):
                A.write_job({"job_id": "job_w", "state": "RUNNING", "n": n, "i": i})
        except Exception as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join(120) for t in threads]
    assert errors == [] and A.read_job("job_w")["state"] == "RUNNING"
    assert not list((A.JOBS / "job_w").glob("*.tmp"))


def test_a_lease_file_that_is_not_a_lease_holds_nothing(state):
    A.LEASES.mkdir(parents=True)
    (A.LEASES / "gpu.lease").write_text("[1, 2]")
    assert A.read_lease("gpu") is None
    (A.LEASES / "gpu.lease").write_text('{"expires_at": "soon"}')
    assert A.read_lease("gpu") is None


def test_a_holder_is_unknown_not_a_crash_when_psutil_is_missing_or_the_time_is_not_a_number(monkeypatch):
    import sys as _sys

    held = {"pid": os.getpid(), "process_created": 1.0, "token": "t"}
    monkeypatch.setitem(_sys.modules, "psutil", None)
    assert LM._Lock._holder_state(held) == "UNKNOWN"
    monkeypatch.undo()
    for bad in (float("nan"), float("inf"), True, "1"):
        assert LM._Lock._holder_state({"pid": os.getpid(), "process_created": bad}) == "UNKNOWN"
    assert LM._Lock._holder_state({"pid": -5, "process_created": 1.0}) == "UNKNOWN"


def test_a_reclaim_guard_or_lock_dated_in_the_future_does_not_wedge_the_ledger(tmp_path, monkeypatch):
    v2 = tmp_path / "ledger_v2.jsonl"
    monkeypatch.setattr(LM, "LOCK_WAIT_S", 0.2)
    lock_path = LM._lock_path(v2)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"")
    guard = lock_path.with_name(lock_path.name + ".reclaim")
    guard.write_bytes(b"")
    future = time.time() + 3600
    os.utime(lock_path, (future, future))
    os.utime(guard, (future, future))
    with LM._Lock(v2):
        pass


def test_the_secret_on_disk_is_restricted_even_when_it_already_existed(tmp_path, monkeypatch):
    from argus.service import command as C

    path = tmp_path / "tok"
    path.write_text("a" * 43)
    calls = []
    monkeypatch.setattr(C, "_restrict_to_owner", lambda p: calls.append(str(p)) or True)
    monkeypatch.setattr(C, "_RESTRICTED", set())
    assert C._ensure_secret(path) == "a" * 43 and calls == [str(path)]
    C._ensure_secret(path)
    assert calls == [str(path)]


def test_a_new_secret_is_restricted_before_it_is_written(tmp_path, monkeypatch):
    from argus.service import command as C

    order = []
    real_restrict = C._restrict_to_owner

    def spy(p):
        order.append(("restrict", os.path.getsize(p) if os.path.exists(p) else None))

    monkeypatch.setattr(C, "_restrict_to_owner", spy)
    monkeypatch.setattr(C, "_RESTRICTED", set())
    value = C._ensure_secret(tmp_path / "new-token")
    assert len(value) >= C.SECRET_MIN_CHARS and order and order[0] == ("restrict", 0)
    assert real_restrict is not None


def test_a_secret_read_survives_a_windows_permission_error_during_a_replace(tmp_path, monkeypatch):
    from argus.service import command as C

    path = tmp_path / "tok"
    path.write_text("b" * 43)
    real = type(path).read_text
    state = {"n": 0}

    def flaky(self, *a, **k):
        state["n"] += 1
        if state["n"] < 3:
            raise PermissionError(13, "in use")
        return real(self, *a, **k)

    monkeypatch.setattr(type(path), "read_text", flaky)
    assert C._read_secret(path) == "b" * 43



def test_the_content_store_wants_the_full_hash_names_objects_by_it_and_leaves_no_bad_copy(tmp_path, monkeypatch):
    from argus.core import content_store as CS

    monkeypatch.setattr(CS, "ROOT", tmp_path / "store")
    src = tmp_path / "f.bin"
    src.write_bytes(b"payload")
    import hashlib
    full = hashlib.sha256(b"payload").hexdigest()
    for prefix in (full[:16], full[:63], full.upper()[:16]):
        with pytest.raises(CS.StoreRefusal):
            CS.put(src, kind=next(iter(CS.KINDS)), expected_sha256=prefix)
    for bad in ("../x", "a" * 63, "A" * 64, ""):
        with pytest.raises(CS.StoreRefusal):
            CS.path_for(bad)

    def tamper(s, d):
        open(d, "wb").write(b"TAMPERED")

    monkeypatch.setattr(CS.shutil, "copy2", tamper)
    with pytest.raises(CS.StoreRefusal):
        CS.put(src, kind=next(iter(CS.KINDS)), expected_sha256=full)
    assert not CS.path_for(full).exists() and CS.has(full) is False


def test_a_small_gzip_that_expands_enormously_is_refused(monkeypatch):
    from argus.core import official_identity as OI

    bomb = gzip.compress(b"\0" * (OI.MAX_DECODED_BYTES + 1024), compresslevel=9)

    class Resp(io.BytesIO):
        status = 200
        headers = {"Content-Encoding": "gzip"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(OI, "assert_metadata_url", lambda url: None)
    monkeypatch.setattr(OI.urllib.request, "urlopen", lambda req, timeout=None: Resp(bomb))
    with pytest.raises(ValueError, match="more than"):
        OI.http_fetch("https://dl.ash2txt.org/x")
    small = gzip.compress(b'{"ok": true}')
    monkeypatch.setattr(OI.urllib.request, "urlopen", lambda req, timeout=None: Resp(small))
    assert OI.http_fetch("https://dl.ash2txt.org/x")[1] == b'{"ok": true}'


def test_an_unmodified_checkout_check_sees_untracked_files():
    for rel in ("upstream_qa_adapters.py", "lasagna_candidate.py"):
        text = open(os.path.join(os.path.dirname(__file__), "..", "core", rel), encoding="utf-8").read()
        assert "--untracked-files=no" not in text and "--untracked-files=all" in text


def test_process_hardening_removes_the_current_directory_from_executable_search(monkeypatch):
    from argus.core import process_hardening as PH

    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    PH.apply()
    assert (os.environ.get("NoDefaultCurrentDirectoryInExePath") == "1") == (os.name == "nt")
    from argus.cli import hardware
    assert hardware.which_on_path is not None and PH.which_on_path("definitely-not-a-program-xyz") is None


def test_every_bare_executable_lookup_goes_through_the_hardened_resolver():
    base = os.path.join(os.path.dirname(__file__), "..")
    for rel in ("core/blender_adapter.py", "cli/cmd_doctor.py"):
        text = open(os.path.join(base, rel), encoding="utf-8").read()
        assert "shutil.which(" not in text, rel


def test_the_image_lock_includes_psutil_which_the_ledger_needs_to_recognise_a_dead_holder():
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    assert re.search(r"^psutil==", open(os.path.join(root, "docker", "requirements.lock.txt"), encoding="utf-8").read(), re.M)
    assert '"psutil>=5.9"' in open(os.path.join(root, "pyproject.toml"), encoding="utf-8").read().split("service = [", 1)[1].split("]", 1)[0]
