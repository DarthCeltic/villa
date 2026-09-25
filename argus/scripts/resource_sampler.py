"""Record CPU, RAM, VRAM, disk and container usage while something else runs, and write the peaks."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

GIB = 1 << 30
MIN_INTERVAL_S = 0.5


def _cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _pct(text):
    try:
        return float(str(text).strip().rstrip("%"))
    except ValueError:
        return None


def _mem_mib(text):
    """'512.3MiB / 7.5GiB' -> 512.3 (the used side, in MiB)."""
    try:
        used = str(text).split("/")[0].strip()
        for unit, mul in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024.0), ("B", 1 / (1 << 20))):
            if used.endswith(unit):
                return float(used[: -len(unit)]) * mul
    except ValueError:
        pass
    return None


def containers():
    out = _cmd(["docker", "stats", "--no-stream", "--format", "{{json .}}"], 20)
    rows = []
    for line in (out or "").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        rows.append({"name": d.get("Name"), "cpu_pct": _pct(d.get("CPUPerc")), "mem_mib": _mem_mib(d.get("MemUsage"))})
    return rows


def sample(drives) -> dict:
    s = {"t": time.time(), "cpu_pct": None, "ram_used_gib": None, "ram_pct": None, "vram_used_mib": None, "vram_total_mib": None, "gpu_util_pct": None,
         "free_gib": {}, "containers": []}
    try:
        import psutil

        s["cpu_pct"] = psutil.cpu_percent(interval=0.5)
        vm = psutil.virtual_memory()
        s["ram_used_gib"], s["ram_pct"] = round(vm.used / GIB, 2), vm.percent
    except Exception:
        pass
    out = _cmd(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"])
    if out:
        try:
            u, t, g = [float(x) for x in out.strip().splitlines()[0].split(",")]
            s["vram_used_mib"], s["vram_total_mib"], s["gpu_util_pct"] = u, t, g
        except (ValueError, IndexError):
            pass
    for d in drives:
        try:
            s["free_gib"][d] = round(shutil.disk_usage(d).free / GIB, 2)
        except OSError:
            s["free_gib"][d] = None
    s["containers"] = containers()
    return s


def _peak(values):
    vals = [v for v in values if v is not None]
    return {"peak": max(vals) if vals else None, "samples": len(vals)}


def summarize(samples, floors=None) -> dict:
    """Peaks and minimums over the samples."""
    floors = floors or {}
    drives = sorted({d for s in samples for d in (s.get("free_gib") or {})})
    free = {}
    for d in drives:
        vals = [(s["free_gib"] or {}).get(d) for s in samples]
        vals = [v for v in vals if v is not None]
        free[d] = {"min_free_gib": min(vals) if vals else None, "start_free_gib": vals[0] if vals else None, "end_free_gib": vals[-1] if vals else None,
                   "floor_gib": floors.get(d), "floor_crossed": bool(vals and floors.get(d) is not None and min(vals) < floors[d]), "samples": len(vals)}
    names = sorted({c["name"] for s in samples for c in (s.get("containers") or []) if c.get("name")})
    cont = {}
    for n in names:
        rows = [c for s in samples for c in (s.get("containers") or []) if c.get("name") == n]
        cont[n] = {"cpu_pct": _peak(c["cpu_pct"] for c in rows), "mem_mib": _peak(c["mem_mib"] for c in rows)}
    vram_total = [s["vram_total_mib"] for s in samples if s.get("vram_total_mib")]
    return {"schema": "argus-resource-peaks-v1", "samples": len(samples),
            "seconds": round(samples[-1]["t"] - samples[0]["t"], 1) if len(samples) > 1 else 0.0,
            "cpu_pct": _peak(s.get("cpu_pct") for s in samples), "ram_used_gib": _peak(s.get("ram_used_gib") for s in samples),
            "ram_pct": _peak(s.get("ram_pct") for s in samples), "vram_used_mib": _peak(s.get("vram_used_mib") for s in samples),
            "vram_total_mib": vram_total[0] if vram_total else None, "gpu_util_pct": _peak(s.get("gpu_util_pct") for s in samples),
            "disk": free, "containers": cont, "any_floor_crossed": any(v["floor_crossed"] for v in free.values()),
            "note": "null means the reading could not be taken, not zero; 'samples' counts how many readings each peak rests on"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=0, help="seconds to sample (0 with --stop-file: until the file appears)")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--drive", action="append", default=[])
    ap.add_argument("--floor", action="append", default=[], help="DRIVE=GIB, e.g. D:/=20")
    ap.add_argument("--stop-file")
    a = ap.parse_args(argv)
    if a.duration <= 0 and not a.stop_file:
        ap.error("give --duration or --stop-file")
    a.interval = max(MIN_INTERVAL_S, a.interval)
    floors = {}
    for f in a.floor:
        d, _, g = f.rpartition("=")
        try:
            floors[d] = float(g)
        except ValueError:
            ap.error("--floor must look like DRIVE=GIB, e.g. D:/=20 (got %r)" % f)
    drives = a.drive or sorted(floors) or [_argus_public_path('anchor', '')]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    samples = []
    end = time.time() + a.duration if a.duration > 0 else None
    if (out / "samples.jsonl").exists():
        ap.error("%s already exists; refusing to overwrite earlier evidence (choose a new --out)" % (out / "samples.jsonl"))
    with (out / "samples.jsonl").open("x", encoding="utf-8") as fh:
        try:
            while True:
                s = sample(drives)
                samples.append(s)
                fh.write(json.dumps(s) + "\n")
                fh.flush()
                if (end and time.time() >= end) or (a.stop_file and Path(a.stop_file).exists()):
                    break
                time.sleep(max(0.0, a.interval))
        except KeyboardInterrupt:
            pass
    summary = summarize(samples, floors)
    (out / "RESOURCE_PEAKS.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("samples", "seconds", "any_floor_crossed")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
