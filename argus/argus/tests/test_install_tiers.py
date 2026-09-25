"""Install tiers, component health, estimator, spill detection, and safe eviction plans."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import copy
import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from argus.core import install_tiers as IT

GIB = 1024 ** 3
FORBIDDEN = ("ceres", "flang", "compile")


class FakeRunner:
    """Answers by probe kind and target; anything unlisted is MISSING."""

    def __init__(self, ready=(), degraded=(), digests=None, raises=(), unknown=()):
        self.ready, self.degraded = set(ready), set(degraded)
        self.digests, self.raises, self.unknown = digests or {}, set(raises), set(unknown)
        self.calls = []

    def __call__(self, kind, spec):
        self.calls.append((kind, spec))
        key = IT._target(spec)
        key = key[0] if isinstance(key, list) else key
        if key in self.raises:
            raise RuntimeError("boom")
        if key in self.unknown:
            return {"ok": None, "detail": "no answer", "digest": None}
        if key in self.degraded:
            return {"ok": True, "detail": "present but broken", "digest": None, "degraded": True}
        if key in self.ready:
            return {"ok": True, "detail": "found", "digest": self.digests.get(key)}
        return {"ok": False, "detail": "not found", "digest": None}


ALL_KEYS = ["argus", "torch", IT._VC3D_IMAGE, "VC3D.exe", "lasagna3d", "vc_spiral", "blender"]


def _all_ready_runner():
    return FakeRunner(ready=ALL_KEYS + ["http://worker.example/health"])


def _remote_with_url():
    c = next(c for c in IT.COMPONENTS if c.id == "remote_gpu_worker")
    return dataclasses.replace(c, check={"kind": "http_health", "url": "http://worker.example/health"})


def _components_with_remote():
    return tuple(_remote_with_url() if c.id == "remote_gpu_worker" else c for c in IT.COMPONENTS)


def _row(res, cid):
    return next(r for r in res["components"] if r["id"] == cid)



def test_tiers_are_ordered_and_have_plain_sentences():
    assert list(IT.TIERS) == ["core", "geometry", "advanced", "remote"]
    for t in IT.TIERS.values():
        assert t["label"] and t["plain"].endswith(".")
        assert t["requires_source_build"] is False


def test_developer_tier_hidden_by_default():
    assert "developer" not in IT.TIERS
    assert "developer" not in IT.visible_tiers()
    dev = IT.visible_tiers(developer_mode=True)
    assert list(dev)[-1] == "developer" and dev["developer"]["requires_source_build"] is True


def test_developer_components_not_in_default_components():
    assert all(c.tier != "developer" for c in IT.COMPONENTS)
    assert IT.DEVELOPER_COMPONENTS and all(c.tier == "developer" for c in IT.DEVELOPER_COMPONENTS)


def test_every_component_has_check_and_install_plan():
    for c in IT.COMPONENTS + IT.DEVELOPER_COMPONENTS:
        assert c.kind in IT.KINDS and c.pin_status in IT.PIN_STATUSES
        assert c.check["kind"] in IT._TARGET_KEYS
        assert c.install_plan and c.repair_plan
        assert set(c.pin) == {"tag", "digest", "version"}
        assert c.tier in IT.TIERS or c.tier == "developer"


def test_component_ids_unique_and_expected_present():
    ids = [c.id for c in IT.COMPONENTS]
    assert len(ids) == len(set(ids))
    assert {"argus_backend", "model_runtime_torch", "vc3d_docker", "vc3d_binary", "lasagna",
            "spiral_fitting", "blender", "remote_gpu_worker"} <= set(ids)


def test_vc3d_docker_is_tag_only_with_digest_unresolved():
    c = next(c for c in IT.COMPONENTS if c.id == "vc3d_docker")
    assert c.check == {"kind": "docker_image",
                       "reference": "ghcr.io/scrollprize/villa/volume-cartographer:edge"}
    assert c.pin_status == "TAG_ONLY_DIGEST_NOT_RESOLVED" and c.pin["digest"] is None
    assert c.pin["tag"] == "edge"


def test_vc3d_binary_flags_cli_tools_unverified():
    c = next(c for c in IT.COMPONENTS if c.id == "vc3d_binary")
    assert c.facts["cli_tools_verified"] is False
    assert c.check["names"] == ["VC3D.exe", "VC3D"]
    assert c.pin_status == IT.TAG_ONLY


def test_gpu_components_flagged_and_linux_note():
    for cid in ("lasagna", "spiral_fitting"):
        c = next(c for c in IT.COMPONENTS if c.id == cid)
        assert c.needs_gpu is True and "Linux" in c.facts["platform_note"]


def test_unknown_facts_are_none_not_invented():
    for cid in ("blender", "remote_gpu_worker"):
        c = next(c for c in IT.COMPONENTS if c.id == cid)
        assert c.pin == {"tag": None, "digest": None, "version": None}
    assert next(c for c in IT.COMPONENTS if c.id == "vc3d_docker").approx_disk_gib is None



def test_tag_only_ready_is_not_scientific():
    r = IT.check_all(_all_ready_runner(), components=_components_with_remote())
    row = _row(r, "vc3d_docker")
    assert row["state"] == "READY" and row["scientific_use_allowed"] is False
    assert row["pin_status"] == IT.TAG_ONLY
    assert "mutable" in row["detail"]


def test_observed_digest_is_reported_but_pin_stays_tag_only():
    runner = FakeRunner(ready=[IT._VC3D_IMAGE], digests={IT._VC3D_IMAGE: "sha256:" + "a" * 64})
    row = IT.check_component(next(c for c in IT.COMPONENTS if c.id == "vc3d_docker"), runner)
    assert row["observed_digest"] == "sha256:" + "a" * 64
    assert row["pin_status"] == IT.TAG_ONLY and row["scientific_use_allowed"] is False


def test_version_pinned_ready_is_scientific():
    row = IT.check_component(next(c for c in IT.COMPONENTS if c.id == "lasagna"),
                             FakeRunner(ready=["lasagna3d"]))
    assert row["state"] == "READY" and row["scientific_use_allowed"] is True


def test_digest_pin_needs_matching_digest():
    base = next(c for c in IT.COMPONENTS if c.id == "vc3d_docker")
    pinned = dataclasses.replace(base, pin_status=IT.PINNED_BY_DIGEST,
                                 pin={"tag": "edge", "digest": "sha256:aaa", "version": None})
    ok = IT.check_component(pinned, FakeRunner(ready=[IT._VC3D_IMAGE],
                                               digests={IT._VC3D_IMAGE: "sha256:aaa"}))
    assert ok["state"] == "READY" and ok["scientific_use_allowed"] is True
    bad = IT.check_component(pinned, FakeRunner(ready=[IT._VC3D_IMAGE],
                                                digests={IT._VC3D_IMAGE: "sha256:bbb"}))
    assert bad["state"] == "DEGRADED" and bad["scientific_use_allowed"] is False
    unseen = IT.check_component(pinned, FakeRunner(ready=[IT._VC3D_IMAGE]))
    assert unseen["state"] == "READY" and unseen["scientific_use_allowed"] is False


def test_check_all_all_ready():
    runner = _all_ready_runner()
    r = IT.check_all(runner, components=_components_with_remote())
    assert {t["state"] for t in r["tiers"].values()} == {"READY"}
    assert all(row["state"] == "READY" for row in r["components"])
    assert r["tiers"]["geometry"]["scientific_use_allowed"] is False
    assert r["tiers"]["core"]["scientific_use_allowed"] is True
    assert r["developer_mode"] is False


def test_check_all_missing_docker_image_binary_still_satisfies_group():
    runner = FakeRunner(ready=["argus", "lasagna3d", "VC3D.exe"])
    r = IT.check_all(runner)
    assert _row(r, "vc3d_docker")["state"] == "MISSING"
    assert r["tiers"]["geometry"]["state"] == "READY"


def test_check_all_missing_both_vc3d_routes_blocks_geometry():
    r = IT.check_all(FakeRunner(ready=["argus", "lasagna3d"]))
    g = r["tiers"]["geometry"]
    assert g["state"] == "DEGRADED" and g["ready"] is False
    assert set(g["blocking"]) == {"vc3d_docker", "vc3d_binary"}


def test_check_all_degraded_component():
    r = IT.check_all(FakeRunner(ready=["argus"], degraded=["lasagna3d"]))
    assert _row(r, "lasagna")["state"] == "DEGRADED"
    assert _row(r, "lasagna")["scientific_use_allowed"] is False
    assert r["tiers"]["geometry"]["state"] == "DEGRADED"


def test_optional_component_missing_does_not_block_tier():
    r = IT.check_all(FakeRunner(ready=["argus"]))
    assert _row(r, "model_runtime_torch")["state"] == "MISSING"
    assert r["tiers"]["core"]["state"] == "READY"


def test_probe_exception_and_no_answer_are_unknown_not_ready():
    r = IT.check_all(FakeRunner(raises=["argus"], unknown=["blender"]))
    assert _row(r, "argus_backend")["state"] == "UNKNOWN"
    assert _row(r, "blender")["state"] == "UNKNOWN"
    assert r["tiers"]["core"]["state"] == "UNKNOWN" and not r["tiers"]["core"]["ready"]


def test_unconfigured_remote_worker_is_unknown_without_probing():
    runner = FakeRunner()
    row = IT.check_component(next(c for c in IT.COMPONENTS if c.id == "remote_gpu_worker"), runner)
    assert row["state"] == "UNKNOWN" and runner.calls == []


def test_cli_tools_flag_surfaces_in_result():
    r = IT.check_all(FakeRunner(ready=["VC3D.exe"]))
    row = _row(r, "vc3d_binary")
    assert row["cli_tools_verified"] is False and "not verified" in row["detail"]


def test_normal_output_never_mentions_ceres_flang_compile():
    for runner in (_all_ready_runner(), FakeRunner(), FakeRunner(degraded=ALL_KEYS)):
        res = IT.check_all(runner)
        text = json.dumps([res, IT.repair_plan(res), IT.visible_tiers(), IT.TIERS]).lower()
        for word in FORBIDDEN:
            assert word not in text, word


def test_developer_mode_adds_developer_tier_and_may_mention_toolchains():
    res = IT.check_all(FakeRunner(), developer_mode=True)
    assert "developer" in res["tiers"]
    assert {r["id"] for r in res["components"] if r["tier"] == "developer"} == {
        c.id for c in IT.DEVELOPER_COMPONENTS}
    text = json.dumps([res, IT.repair_plan(res, developer_mode=True)]).lower()
    assert "ceres" in text and "flang" in text and "compile" in text


def test_check_all_only_runs_injected_runner():
    runner = _all_ready_runner()
    IT.check_all(runner)
    assert len(runner.calls) == len(IT.COMPONENTS) - 1



def test_repair_plan_orders_by_tier_and_needs_approval():
    res = IT.check_all(FakeRunner(ready=["argus"]))
    plan = IT.repair_plan(res)
    comps = [s["component"] for s in plan["steps"] if s["component"]]
    tiers = [s["tier"] for s in plan["steps"] if s["tier"]]
    assert tiers == sorted(tiers, key=list(IT.TIERS).index)
    assert "vc3d_docker" in comps and "argus_backend" not in comps
    pull = next(s for s in plan["steps"] if s["command"] and s["command"].startswith("docker pull"))
    assert pull["needs_approval"] is True and pull["downloads"] is True
    assert plan["needs_approval"] is True and plan["dry_run"] is True
    assert plan["steps"][-1]["needs_approval"] is False and plan["steps"][-1]["component"] is None


def test_repair_plan_disk_sum_and_refusal_floor():
    base = {c.id: c for c in IT.COMPONENTS}
    sized = tuple(dataclasses.replace(base[i], approx_disk_gib=g) for i, g in
                  (("argus_backend", 1.0), ("model_runtime_torch", 4.0), ("vc3d_docker", 15.0)))
    res = IT.check_all(FakeRunner(), components=sized)
    plan = IT.repair_plan(res, components=sized)
    assert plan["known_disk_gib"] == 20.0 and plan["unknown_size_components"] == []
    assert plan["approx_disk_gib"] == 20.0
    assert plan["refuses_if_free_disk_below_gib"] == 25.0


def test_repair_plan_unknown_sizes_use_named_planning_budget():
    res = IT.check_all(FakeRunner())
    plan = IT.repair_plan(res)
    n = len(plan["unknown_size_components"])
    assert n >= 2
    assert plan["approx_disk_gib"] == n * IT.UNKNOWN_SIZE_PLANNING_GIB + plan["known_disk_gib"]
    assert plan["refuses_if_free_disk_below_gib"] > plan["approx_disk_gib"]


def test_repair_plan_nothing_to_repair():
    res = IT.check_all(_all_ready_runner(), components=_components_with_remote())
    plan = IT.repair_plan(res, components=_components_with_remote())
    assert plan["steps"] == [] and plan["needs_approval"] is False
    assert plan["approx_disk_gib"] == 0 and plan["refuses_if_free_disk_below_gib"] == 0


def test_repair_plan_source_build_only_in_developer_mode():
    res = IT.check_all(FakeRunner(ready=["argus", "lasagna3d"], ), developer_mode=True)
    res["components"] = [dict(r, state="DEGRADED") if r["id"] == "vc3d_docker" else r
                         for r in res["components"]]
    normal = json.dumps(IT.repair_plan(res)).lower()
    dev = json.dumps(IT.repair_plan(res, developer_mode=True)).lower()
    assert not any(w in normal for w in FORBIDDEN)
    assert "compile" in dev and "ceres" in dev
    assert all(s["component"] != "dev_ceres_flang" for s in IT.repair_plan(res)["steps"])


def test_repair_plan_reports_skipped_unknown():
    res = IT.check_all(FakeRunner(ready=["argus"]))
    assert "remote_gpu_worker" in IT.repair_plan(res)["skipped_unknown"]


def test_repair_plan_does_not_mutate_check_result():
    res = IT.check_all(FakeRunner())
    before = copy.deepcopy(res)
    IT.repair_plan(res)
    assert res == before



def test_vram_estimate_monotonic_in_batch_and_patch():
    b = [IT.vram_estimate_gib(96, n) for n in (1, 2, 4, 8)]
    assert b == sorted(b) and len(set(b)) == 4
    p = [IT.vram_estimate_gib(n, 2) for n in (32, 64, 96, 128)]
    assert p == sorted(p) and len(set(p)) == 4


def test_vram_estimate_formula_and_shapes():
    got = IT.vram_estimate_gib((64, 64, 64), 2, channels=1, bytes_per_voxel=4,
                               activation_multiplier=10, model_gib=0.5)
    assert got == pytest.approx(64 ** 3 * 2 * 4 * 10 / GIB + 0.5)
    assert IT.vram_estimate_gib(64, 2, activation_multiplier=10, model_gib=0.5) == pytest.approx(got)
    assert IT.vram_estimate_gib(64, 2, model_gib=0) < IT.vram_estimate_gib(64, 2, model_gib=1)


def test_vram_estimate_rejects_bad_input():
    for bad in ((0, 1), (64, 0), (-1, 1)):
        with pytest.raises(ValueError):
            IT.vram_estimate_gib(*bad)


def test_choose_patch_and_batch_picks_largest_feasible():
    cands = [(64, 1), (64, 4), (96, 2), (128, 4), (192, 8)]
    got = IT.choose_patch_and_batch(8.0, cands)
    assert got is not None and got["estimated_gib"] <= 8.0 * 0.85
    fits = [c for c in cands if IT.vram_estimate_gib(*c) <= 8.0 * 0.85]
    best = max(fits, key=lambda c: (c[0] ** 3 * c[1], c[0], c[1]))
    assert (got["patch_size"], got["batch_size"]) == best
    assert IT.choose_patch_and_batch(8.0, list(reversed(cands))) == got


def test_choose_patch_and_batch_none_when_nothing_fits_and_headroom_matters():
    assert IT.choose_patch_and_batch(1.0, [(192, 8), (128, 4)]) is None
    est = IT.vram_estimate_gib(96, 2)
    assert IT.choose_patch_and_batch(est, [(96, 2)]) is None
    assert IT.choose_patch_and_batch(est, [(96, 2)], headroom=1.0)["patch_size"] == 96
    with pytest.raises(ValueError):
        IT.choose_patch_and_batch(8, [(64, 1)], headroom=0)


def test_choose_patch_and_batch_bigger_budget_never_worse():
    cands = [(64, 1), (64, 2), (96, 2), (128, 2), (128, 4)]
    sizes = []
    for budget in (2, 4, 8, 16, 32):
        got = IT.choose_patch_and_batch(budget, cands)
        sizes.append(0 if got is None else got["patch_size"] ** 3 * got["batch_size"])
    assert sizes == sorted(sizes)


def test_resource_tier_boundaries():
    assert IT.resource_tier(6.0, 16) == "consumer_6gb"
    assert IT.resource_tier(5.4, 16) == "remote_recommended"
    assert IT.resource_tier(12, 32) == "mid_12gb"
    assert IT.resource_tier(12, 31) == "consumer_6gb"
    assert IT.resource_tier(23.99, 64) == "workstation_24gb"
    assert IT.resource_tier(24, 63) == "mid_12gb"
    assert IT.resource_tier(6, 8) == "remote_recommended"
    assert IT.resource_tier(None, 64) == "remote_recommended"
    assert IT.resource_tier(24, None) == "workstation_24gb"


def test_oversight_record_adapters():
    gpus = {"present": True, "cards": [{"vram_total_mib": 8192}, {"vram_total_mib": 12288}]}
    assert IT.vram_gib_from_gpus(gpus) == 12.0
    assert IT.vram_gib_from_gpus({"present": False}) is None
    disks = [{"root": "Tdrive", "present": True, "free_gib": 200.0},
             {"root": "Cdrive", "present": True, "free_gib": 100.0},
             {"root": "/x", "present": False}]
    assert IT.free_gib_by_drive(disks) == {"C": 100.0, "T": 200.0}
    assert IT.free_gib_by_drive([]) == {"C": None, "T": None}



def _trace(n=10, **over):
    rows = []
    for i in range(n):
        row = {"t": float(i), "gpu_used_mib": 4000.0, "gpu_total_mib": 8192.0,
               "torch_allocated_mib": 3500.0, "host_ram_used_mib": 8000.0, "step_s": 1.0}
        row.update({k: (v(i) if callable(v) else v) for k, v in over.items()})
        rows.append(row)
    return rows


def test_spill_negative_on_healthy_trace():
    out = IT.detect_vram_spill(_trace())
    assert out["spill_suspected"] is False and out["evidence"] == []


def test_spill_empty_samples():
    assert IT.detect_vram_spill([])["spill_suspected"] is False


def test_spill_positive_when_torch_exceeds_total():
    out = IT.detect_vram_spill(_trace(torch_allocated_mib=lambda i: 3000 + 700 * i))
    assert out["spill_suspected"] is True
    assert out["evidence"][0]["rule"] == "torch_allocated_exceeds_gpu_total"
    assert out["evidence"][0]["torch_allocated_mib"] > 8192


def test_spill_positive_on_host_ram_growth_with_gpu_pinned():
    tr = _trace(gpu_used_mib=8150.0, torch_allocated_mib=lambda i: 4800 + 100 * i,
                host_ram_used_mib=lambda i: 8000 + 600 * i)
    out = IT.detect_vram_spill(tr)
    assert out["spill_suspected"] is True
    assert [e["rule"] for e in out["evidence"]] == ["host_ram_rising_while_gpu_pinned"]


def test_spill_host_ram_growth_without_gpu_pressure_is_not_spill():
    tr = _trace(torch_allocated_mib=lambda i: 3000 + 100 * i,
                host_ram_used_mib=lambda i: 8000 + 900 * i)
    assert IT.detect_vram_spill(tr)["spill_suspected"] is False


def test_spill_pinned_but_allocations_flat_is_not_spill():
    tr = _trace(gpu_used_mib=8150.0, host_ram_used_mib=lambda i: 8000 + 900 * i)
    assert IT.detect_vram_spill(tr)["spill_suspected"] is False


def test_spill_host_rule_notes_missing_torch_data():
    tr = _trace(gpu_used_mib=8150.0, torch_allocated_mib=None,
                host_ram_used_mib=lambda i: 8000 + 900 * i)
    out = IT.detect_vram_spill(tr)
    assert out["spill_suspected"] is False and out["notes"]


def test_spill_step_slowdown_only_counts_at_full_vram():
    slow = lambda i: 1.0 if i < 5 else 4.0
    at_full = IT.detect_vram_spill(_trace(gpu_used_mib=8150.0, step_s=slow))
    assert any(e["rule"] == "step_time_slowdown_at_full_vram" for e in at_full["evidence"])
    free = IT.detect_vram_spill(_trace(step_s=slow))
    assert free["spill_suspected"] is False and free["notes"]


def test_spill_sorts_samples_and_does_not_mutate():
    tr = list(reversed(_trace(torch_allocated_mib=lambda i: 3000 + 700 * i)))
    before = copy.deepcopy(tr)
    assert IT.detect_vram_spill(tr)["spill_suspected"] is True
    assert tr == before


def test_enforce_cap_plan_is_text_only():
    plan = IT.enforce_cap_plan(8.0)
    assert plan["torch_per_process_memory_fraction"] == 0.9 and plan["cap_gib"] == 7.2
    assert "set_per_process_memory_fraction(0.9" in plan["code"] and plan["note"]
    assert "torch" not in sys.modules or sys.modules["torch"] is not None
    with pytest.raises(ValueError):
        IT.enforce_cap_plan(8.0, cap_fraction=1.5)
    with pytest.raises(ValueError):
        IT.enforce_cap_plan(0)



def _entry(key, size_gib, day, **kw):
    e = {"key": key, "bytes": int(size_gib * GIB), "last_used_utc": "2026-09-%02dT00:00:00Z" % day,
         "pinned": False, "in_use": False, "held_by_run": None}
    e.update(kw)
    return e


def test_eviction_lru_order_and_stops_when_under_budget():
    entries = [_entry("new", 10, 15), _entry("old", 10, 1), _entry("mid", 10, 8)]
    plan = IT.eviction_plan(entries, budget_gib=20, protect=[])
    assert [e["key"] for e in plan["evict"]] == ["old"]
    assert plan["bytes_after_plan"] == 20 * GIB and plan["budget_reachable"] is True
    plan2 = IT.eviction_plan(entries, budget_gib=5, protect=[])
    assert [e["key"] for e in plan2["evict"]] == ["old", "mid", "new"]
    assert plan2["dry_run"] is True


def test_eviction_never_touches_protected_kinds():
    entries = [_entry("pin", 10, 1, pinned=True), _entry("use", 10, 2, in_use=True),
               _entry("held", 10, 3, held_by_run="run-7"), _entry("keep", 10, 4),
               _entry("free", 10, 20)]
    plan = IT.eviction_plan(entries, budget_gib=15, protect=["keep"])
    assert [e["key"] for e in plan["evict"]] == ["free"]
    reasons = {p["key"]: p["reasons"] for p in plan["protected"]}
    assert reasons == {"pin": ["pinned"], "use": ["in_use"], "held": ["held_by_run"],
                       "keep": ["protected"]}


def test_eviction_reports_shortfall_when_protected_alone_exceeds_budget():
    entries = [_entry("pin", 30, 1, pinned=True), _entry("free", 5, 2)]
    plan = IT.eviction_plan(entries, budget_gib=20)
    assert plan["shortfall_bytes"] == 10 * GIB and plan["budget_reachable"] is False
    assert all(e["key"] != "pin" for e in plan["evict"])
    assert "protected" in plan["note"] and plan["reclaimable_bytes"] == 5 * GIB


def test_eviction_within_budget_evicts_nothing():
    plan = IT.eviction_plan([_entry("a", 1, 1)], budget_gib=10)
    assert plan["evict"] == [] and plan["shortfall_bytes"] == 0 and plan["bytes_to_free"] == 0


def test_eviction_reclaimable_counts_only_unprotected():
    entries = [_entry("pin", 4, 1, pinned=True), _entry("a", 3, 2), _entry("b", 2, 3)]
    assert IT.eviction_plan(entries, budget_gib=100)["reclaimable_bytes"] == 5 * GIB


def test_eviction_never_mutates_input():
    entries = [_entry("a", 10, 1), _entry("b", 10, 2, pinned=True)]
    before = copy.deepcopy(entries)
    protect = ["a"]
    IT.eviction_plan(entries, budget_gib=1, protect=protect)
    assert entries == before and protect == ["a"]


def test_eviction_unknown_timestamp_goes_last_and_epoch_accepted():
    entries = [_entry("nots", 10, 1, last_used_utc=None), _entry("epoch", 10, 5, last_used_utc=1.0),
               _entry("bad", 10, 5, last_used_utc="yesterday")]
    plan = IT.eviction_plan(entries, budget_gib=0)
    assert [e["key"] for e in plan["evict"]][0] == "epoch"
    assert {e["key"]: e["last_used_known"] for e in plan["evict"]} == {
        "epoch": True, "nots": False, "bad": False}


def test_eviction_deletes_nothing_on_disk(tmp_path):
    f = tmp_path / "chunk.bin"
    f.write_bytes(b"x" * 10)
    IT.eviction_plan([{"key": str(f), "bytes": 10, "last_used_utc": "2026-09-01T00:00:00Z",
                       "pinned": False, "in_use": False, "held_by_run": None}], budget_gib=0)
    assert f.exists()


def test_eviction_rejects_negative_bytes():
    with pytest.raises(ValueError):
        IT.eviction_plan([_entry("a", 1, 1, bytes=-5)], budget_gib=1)


def test_low_disk_guard_floors_and_planned_writes():
    assert IT.low_disk_guard(60, 120)["allowed"] is True
    assert IT.low_disk_guard(50, 100)["allowed"] is True
    c = IT.low_disk_guard(30, 500, planned_write_gib_c=11)
    assert c["allowed"] is False and c["reasons"][0].startswith("C:")
    t = IT.low_disk_guard(500, 30, planned_write_gib_t=11)
    assert t["allowed"] is False and t["reasons"][0].startswith("T:")
    both = IT.low_disk_guard(10, 10)
    assert len(both["reasons"]) == 2 and both["floors_gib"] == {"C": 20, "T": 20}
    assert IT.low_disk_guard(40, 90, floor_c=30, floor_t=40)["allowed"] is True


def test_low_disk_guard_unknown_free_refuses_and_negative_write_raises():
    g = IT.low_disk_guard(None, 500)
    assert g["allowed"] is False and "unknown" in g["reasons"][0]
    with pytest.raises(ValueError):
        IT.low_disk_guard(100, 200, planned_write_gib_c=-1)


def test_low_ram_guard_at_90():
    assert IT.low_ram_guard(89.9)["allowed"] is True
    assert IT.low_ram_guard(90)["allowed"] is True
    assert IT.low_ram_guard(90.1)["allowed"] is False
    assert IT.low_ram_guard(75, stop_above=70)["allowed"] is False
    assert IT.low_ram_guard(None)["allowed"] is False



def _probe_runner(plat, proc):
    def runner(kind, spec):
        if kind == "platform":
            return {"ok": True, "detail": plat, "digest": None}
        if kind == "proc_version":
            return {"ok": bool(proc), "detail": proc, "digest": None}
        return {"ok": None, "detail": "", "digest": None}
    return runner


def test_wsl_status_detects_wsl2():
    out = IT.wsl_status(_probe_runner(
        "Linux-5.15.153.1-microsoft-standard-WSL2-x86_64",
        "Linux version 5.15.153.1-microsoft-standard-WSL2 (root@x) (gcc)"))
    assert out["is_wsl"] is True and out["wsl2"] is True and out["host_os"] == "linux"
    assert "cap" in out["note"]


def test_wsl_status_plain_linux_and_windows():
    lin = IT.wsl_status(_probe_runner("Linux-6.5.0-generic-x86_64", "Linux version 6.5.0-generic"))
    assert lin["is_wsl"] is False and lin["host_os"] == "linux"
    win = IT.wsl_status(_probe_runner("Windows-10-10.0.19045-SP0", ""))
    assert win["is_wsl"] is False and win["host_os"] == "windows" and "cap" in win["note"]


def test_wsl_status_survives_broken_runner():
    def boom(kind, spec):
        raise OSError("no")
    out = IT.wsl_status(boom)
    assert out["is_wsl"] is False and out["host_os"] == "unknown"


def test_default_runner_read_only_kinds(tmp_path):
    f = tmp_path / "present.txt"
    f.write_text("x")
    assert IT.default_runner("file_exists", {"path": str(f)})["ok"] is True
    assert IT.default_runner("file_exists", {"path": str(tmp_path / "no")})["ok"] is False
    assert IT.default_runner("python_import", {"module": "json"})["ok"] is True
    assert IT.default_runner("python_import", {"module": "no_such_module_xyz"})["ok"] is False
    assert IT.default_runner("binary_on_path", {"names": ["no-such-binary-xyz"]})["ok"] is False
    assert IT.default_runner("nonsense", {})["ok"] is None
    assert IT.default_runner("http_health", {"url": "ftp://x"})["ok"] is False


def test_module_import_needs_no_heavy_dependencies():
    src = Path(IT.__file__).read_text(encoding="utf-8")
    top = src.split("Runner = ")[0]
    for mod in ("torch", "psutil", "subprocess", "numpy"):
        assert "import " + mod not in top
