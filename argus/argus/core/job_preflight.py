"""Refuse a job before it spends anything, when the thing it would spend it on is not there."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import dataclasses
import hashlib
import os
import pathlib
import shutil
import subprocess

CONTRACT = "argus-job-preflight-v1"

FLOORS_PATH = pathlib.Path(__file__).with_name("resource_floors.json")

MIN_SUPPORT_FRACTION = 0.005


class PreflightRefusal(RuntimeError):
    """Raised rather than returned."""


@dataclasses.dataclass(frozen=True)
class JobSpec:
    name: str
    input_path: str
    roi: tuple | None
    output_path: str
    needs_vram_mb: int | None = None
    needs_ram_mb: int | None = None
    needs_disk_mb: int | None = None


def floors() -> dict:
    import json
    if FLOORS_PATH.is_file():
        try:
            return json.loads(FLOORS_PATH.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"measured": False,
            "why": "no measured floors are recorded. Run a job under measurement and write "
                   "resource_floors.json before relying on resource refusal."}


def support_probe(input_path: str, roi=None, *, max_voxels: int = 64_000_000) -> dict:
    """How much of the region carries real data."""
    try:
        import numpy as np
        import zarr
    except ImportError as exc:
        return {"probed": False, "why": "array libraries unavailable: %s" % exc}

    try:
        a = zarr.open(str(input_path), mode="r")
        if hasattr(a, "array_keys") and not hasattr(a, "shape"):
            keys = sorted(a.array_keys())
            return {"probed": False,
                    "why": "path is a GROUP with arrays %s, not an array. Pick the level "
                           "explicitly: a loader that resolves a level for you gives a run on "
                           "different data than the receipt names." % keys[:6]}
        if roi:
            z0, z1, y0, y1, x0, x1 = roi
            n = (z1 - z0) * (y1 - y0) * (x1 - x0)
            if n > max_voxels:
                step = max(1, int((n / max_voxels) ** (1 / 3)))
                sub = np.asarray(a[z0:z1:step, y0:y1:step, x0:x1:step])
                sampled = True
            else:
                sub = np.asarray(a[z0:z1, y0:y1, x0:x1])
                sampled = False
        else:
            sub, sampled = np.asarray(a[::8, ::8, ::8]), True

        nz = float((sub != 0).mean())
        return {
          "probed": True, "sampled": sampled,
          "shape": list(getattr(a, "shape", [])),
          "roi": list(roi) if roi else None,
          "voxels_examined": int(sub.size),
          "nonzero_fraction": nz,
          "value_min": int(sub.min()), "value_max": int(sub.max()),
          "has_support": nz >= MIN_SUPPORT_FRACTION,
          "min_support_fraction": MIN_SUPPORT_FRACTION,
        }
    except Exception as exc:
        return {"probed": False, "why": "%s: %s" % (type(exc).__name__, exc)}


def resource_forecast(spec: JobSpec) -> dict:
    """What is free right now, against floors that were measured."""
    f = floors()
    out = {"floors_are_measured": bool(f.get("measured")), "floors": f}

    try:
        import psutil
        vm = __import__("psutil").virtual_memory()
        out["ram_free_mb"] = int(vm.available / (1 << 20))
        out["ram_total_mb"] = int(vm.total / (1 << 20))
    except Exception:
        try:
            r = subprocess.run(
              ["powershell", "-NoProfile", "-Command",
               "$o=Get-CimInstance Win32_OperatingSystem;"
               "[int]($o.FreePhysicalMemory/1KB);[int]($o.TotalVisibleMemorySize/1KB)"],
              capture_output=True, text=True, timeout=120)
            vals = [int(x) for x in (r.stdout or "").split() if x.isdigit()]
            if len(vals) >= 2:
                out["ram_free_mb"], out["ram_total_mb"] = vals[0], vals[1]
        except (OSError, subprocess.SubprocessError, ValueError):
            out["ram_free_mb"] = None

    try:
        du = shutil.disk_usage(pathlib.Path(spec.output_path).anchor or _argus_public_path('anchor', ''))
        out["disk_free_mb"] = int(du.free / (1 << 20))
    except OSError:
        out["disk_free_mb"] = None

    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True,
                           text=True, timeout=120)
        used, total = [int(x.strip()) for x in r.stdout.strip().splitlines()[0].split(",")]
        out["vram_used_mb"], out["vram_total_mb"] = used, total
        out["vram_free_mb"] = total - used
    except Exception:
        out["vram_free_mb"] = None

    return out


def input_identity(input_path: str) -> dict:
    """What, exactly, is being read."""
    p = pathlib.Path(input_path)
    meta = p / "zarr.json"
    if not meta.is_file():
        meta = p / ".zarray"
    h = None
    if meta.is_file():
        h = hashlib.sha256(meta.read_bytes()).hexdigest()
    return {
      "path": str(p),
      "exists": p.exists(),
      "metadata_file": str(meta) if meta.is_file() else None,
      "metadata_sha256": h,
      "why_the_metadata_and_not_the_data": "hashing a multi-gigabyte array to identify it costs "
                                           "more than the job. The array metadata pins shape, "
                                           "dtype and chunking, which is what a later reader "
                                           "needs to know they have the same thing.",
    }


def check(spec: JobSpec) -> dict:
    problems = []

    ident = input_identity(spec.input_path)
    if not ident["exists"]:
        problems.append("input does not exist: %s" % spec.input_path)

    sup = support_probe(spec.input_path, spec.roi)
    if sup.get("probed") and not sup.get("has_support"):
        problems.append(
          "EMPTY INPUT: the requested region is %.4f%% non-zero, below the %.2f%% floor. A job "
          "that runs on this produces a clean, plausible, worthless artifact that looks exactly "
          "like a real one."
          % (sup["nonzero_fraction"] * 100, MIN_SUPPORT_FRACTION * 100))
    elif not sup.get("probed"):
        problems.append("support could not be probed: %s" % sup.get("why"))

    fc = resource_forecast(spec)
    f = fc.get("floors", {})
    if f.get("measured"):
        for key, have, need in (("ram_free_mb", fc.get("ram_free_mb"), f.get("ram_floor_mb")),
                                ("disk_free_mb", fc.get("disk_free_mb"), f.get("disk_floor_mb")),
                                ("vram_free_mb", fc.get("vram_free_mb"),
                                 f.get("vram_floor_mb"))):
            if have is not None and need and have < need:
                problems.append(
                  "%s is %d MB, below the measured floor of %d MB." % (key, have, need))

    return {
      "contract": CONTRACT, "job": spec.name,
      "ok": not problems, "problems": problems,
      "input_identity": ident, "support": sup, "resources": fc,
      "what_this_answers": "would this job be MEANINGFUL if it succeeded -- which is a different "
                           "question from whether it will run, and the one nobody asks.",
    }


def require(spec: JobSpec) -> dict:
    r = check(spec)
    if not r["ok"]:
        raise PreflightRefusal("%s refused:\n  - %s" % (spec.name, "\n  - ".join(r["problems"])))
    return r


def selftest() -> bool:
    """The empty-region refusal is the whole point; it must raise, not warn."""
    import tempfile
    ok = []
    try:
        import numpy as _np
        import zarr as _z
    except ImportError as exc:
        print("  SKIP array libraries unavailable: %s" % exc)
        return True

    d = tempfile.mkdtemp()
    empty = str(pathlib.Path(d) / "empty.zarr")
    a = _z.open(empty, mode="w", shape=(16, 16, 16), dtype="uint8")
    a[:] = 0
    full = str(pathlib.Path(d) / "full.zarr")
    b = _z.open(full, mode="w", shape=(16, 16, 16), dtype="uint8")
    b[:] = 7

    roi = (0, 16, 0, 16, 0, 16)
    ok.append(("an all-zero region has no support",
               support_probe(empty, roi)["has_support"] is False))
    ok.append(("a populated region has support",
               support_probe(full, roi)["has_support"] is True))

    spec = JobSpec(name="t", input_path=empty, roi=roi, output_path=d)
    raised = False
    try:
        require(spec)
    except PreflightRefusal as exc:
        raised = "EMPTY INPUT" in str(exc)
    ok.append(("require() RAISES on an empty region", raised))

    grp = str(pathlib.Path(d) / "pyr.zarr")
    g = _z.open_group(grp, mode="w")
    g.create_array("5", shape=(4, 4, 4), dtype="uint8")
    s = support_probe(grp)
    ok.append(("a pyramid group is refused rather than silently resolved",
               s["probed"] is False and "GROUP" in s.get("why", "")))

    f = floors()
    ok.append(("shipped floors are measured and say what they were measured from",
               bool(f.get("measured")) and bool(f.get("measured_from"))))
    for name, good in ok:
        print("  %-4s %s" % ("ok" if good else "FAIL", name))
    print("selftest: %d/%d passed" % (sum(1 for _, g2 in ok if g2), len(ok)))
    return all(g2 for _, g2 in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ARGUS job preflight")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    import json as _j
    print(_j.dumps(floors(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
