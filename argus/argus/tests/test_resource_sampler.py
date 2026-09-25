"""The resource sampler: peaks over samples, unmeasured readings stay null, and a floor is crossed only when a sample was actually below it."""
import importlib.util
import pytest
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("resource_sampler", ROOT / "scripts" / "resource_sampler.py")
RS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RS)


def _s(t, cpu=None, ram=None, vram=None, free=None, containers=()):
    return {"t": t, "cpu_pct": cpu, "ram_used_gib": ram, "ram_pct": None, "vram_used_mib": vram, "vram_total_mib": 8192 if vram is not None else None,
            "gpu_util_pct": None, "free_gib": free or {}, "containers": list(containers)}


def test_peaks_minimums_and_floor_crossings():
    samples = [_s(0, 10, 8.0, 900, {"D:/": 30.0, "E:/": 50.0}),
               _s(5, 80, 14.5, 5200, {"D:/": 21.0, "E:/": 45.0}),
               _s(10, 20, 9.0, 1000, {"D:/": 25.0, "E:/": 41.0})]
    r = RS.summarize(samples, {"D:/": 20.0, "E:/": 40.0})
    assert r["cpu_pct"]["peak"] == 80 and r["ram_used_gib"]["peak"] == 14.5 and r["vram_used_mib"]["peak"] == 5200 and r["vram_total_mib"] == 8192
    assert r["disk"]["D:/"]["min_free_gib"] == 21.0 and r["disk"]["E:/"]["min_free_gib"] == 41.0 and r["any_floor_crossed"] is False
    assert r["seconds"] == 10.0 and r["samples"] == 3


def test_a_sample_below_the_floor_is_reported_as_crossed_even_if_it_recovers():
    samples = [_s(0, free={"D:/": 30.0}), _s(5, free={"D:/": 19.9}), _s(10, free={"D:/": 30.0})]
    r = RS.summarize(samples, {"D:/": 20.0})
    assert r["disk"]["D:/"]["floor_crossed"] is True and r["any_floor_crossed"] is True and r["disk"]["D:/"]["end_free_gib"] == 30.0


def test_unmeasured_readings_are_null_with_their_sample_count_never_zero():
    r = RS.summarize([_s(0), _s(5, cpu=30), _s(10)])
    assert r["cpu_pct"] == {"peak": 30, "samples": 1}
    assert r["vram_used_mib"] == {"peak": None, "samples": 0} and r["vram_total_mib"] is None


def test_container_peaks_are_per_container():
    samples = [_s(0, containers=[{"name": "observe", "cpu_pct": 5.0, "mem_mib": 300.0}, {"name": "ui", "cpu_pct": 1.0, "mem_mib": 40.0}]),
               _s(5, containers=[{"name": "observe", "cpu_pct": 90.0, "mem_mib": 812.5}])]
    r = RS.summarize(samples)
    assert r["containers"]["observe"]["cpu_pct"]["peak"] == 90.0 and r["containers"]["observe"]["mem_mib"]["peak"] == 812.5
    assert r["containers"]["ui"]["mem_mib"]["samples"] == 1


def test_docker_stats_text_is_parsed_and_junk_is_not():
    assert RS._pct("12.34%") == 12.34 and RS._pct("--") is None
    assert RS._mem_mib("512MiB / 7.5GiB") == 512.0 and RS._mem_mib("1.5GiB / 7.5GiB") == 1536.0 and RS._mem_mib("garbage") is None


def test_the_command_line_writes_samples_and_peaks(tmp_path, monkeypatch):
    monkeypatch.setattr(RS, "sample", lambda drives: _s(RS.time.time(), cpu=7, free={d: 100.0 for d in drives}))
    assert RS.main(["--out", str(tmp_path), "--duration", "0.2", "--interval", "0.05", "--drive", "D:/", "--floor", "D:/=20"]) == 0
    doc = json.loads((tmp_path / "RESOURCE_PEAKS.json").read_text(encoding="utf-8"))
    assert doc["schema"] == "argus-resource-peaks-v1" and doc["samples"] >= 2 and doc["cpu_pct"]["peak"] == 7 and doc["any_floor_crossed"] is False
    assert len((tmp_path / "samples.jsonl").read_text(encoding="utf-8").splitlines()) == doc["samples"]


def test_an_existing_samples_file_is_never_overwritten_and_a_zero_interval_is_clamped(tmp_path, monkeypatch):
    monkeypatch.setattr(RS, "sample", lambda drives: _s(RS.time.time(), cpu=1, free={d: 100.0 for d in drives}))
    sleeps = []
    monkeypatch.setattr(RS.time, "sleep", lambda s: sleeps.append(s))
    assert RS.main(["--out", str(tmp_path), "--duration", "0.05", "--interval", "0", "--drive", "D:/"]) == 0
    assert sleeps and min(sleeps) >= RS.MIN_INTERVAL_S
    with pytest.raises(SystemExit):
        RS.main(["--out", str(tmp_path), "--duration", "0.05", "--drive", "D:/"])           # the first run's evidence is kept
    with pytest.raises(SystemExit):
        RS.main(["--out", str(tmp_path / "b"), "--duration", "1", "--floor", "D:/=lots"])
