"""What this machine actually is, and which profile is therefore honest for it."""
from __future__ import annotations

import ctypes
import os
import pathlib
import platform
import shutil
import subprocess

from argus.core import process_hardening

UNKNOWN = "UNKNOWN"

GIB = 1 << 30

FLOOR_FREE_DISK_GIB = 25.0
FLOOR_FREE_RAM_GIB = 8.0

TENSOR_CORE_CC = 8.0



def which_on_path(name: str):
    return process_hardening.which_on_path(name)


def gpu_probe(timeout: float = 20.0) -> dict:
    """The driver's own device table."""
    exe = which_on_path("nvidia-smi")
    if not exe:
        return {"status": UNKNOWN, "devices": [],
                "why": "nvidia-smi is not on PATH. That means the NVIDIA driver's query tool "
                       "is absent -- which is also true of a working AMD, Intel or Apple "
                       "machine, so it is NOT evidence that there is no GPU.",
                "fix": "install the vendor driver, or pass --profile explicitly if you know "
                       "what this machine has"}
    try:
        r = subprocess.run(
            [exe, "--query-gpu=name,memory.total,compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": UNKNOWN, "devices": [],
                "why": "nvidia-smi exists but did not run: %s" % type(exc).__name__,
                "fix": "run nvidia-smi by hand and read its error"}
    if r.returncode != 0:
        return {"status": UNKNOWN, "devices": [],
                "why": "nvidia-smi exited %d: %s" % (r.returncode,
                                                     (r.stderr or "").strip()[:200]),
                "fix": "run nvidia-smi by hand and read its error"}
    devices = []
    for line in (r.stdout or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            vram_mb = float(parts[1])
        except ValueError:
            vram_mb = None
        cc = None
        if len(parts) > 2 and parts[2] not in ("", "[N/A]", "[Not Supported]"):
            try:
                cc = float(parts[2])
            except ValueError:
                cc = None
        devices.append({"name": parts[0], "vram_mb": vram_mb, "compute_capability": cc,
                        "compute_capability_why_unknown": (
                            None if cc is not None else
                            "this driver's nvidia-smi does not report compute_cap")})
    if not devices:
        return {"status": "ABSENT", "devices": [],
                "why": "nvidia-smi ran and listed no devices. This one IS a measurement.",
                "fix": None}
    return {"status": "PRESENT", "devices": devices, "why": None, "fix": None}


def gpu_memory(timeout: float = 5.0) -> dict:
    """Used and free VRAM right now, per the driver."""
    exe = which_on_path("nvidia-smi")
    if not exe:
        return {"status": UNKNOWN, "why": "nvidia-smi is not on PATH"}
    try:
        r = subprocess.run([exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=timeout)
        used, total = [int(x.strip()) for x in r.stdout.strip().splitlines()[0].split(",")]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return {"status": UNKNOWN, "why": "nvidia-smi did not give a usable answer: %s" % type(exc).__name__}
    return {"status": "MEASURED", "used_mb": used, "total_mb": total, "free_mb": total - used}


def free_ram_gib() -> tuple:
    """(gib, why_unknown)."""
    system = platform.system()
    if system == "Windows":
        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = _MS()
        st.dwLength = ctypes.sizeof(_MS)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None, "GlobalMemoryStatusEx failed"
        return round(st.ullAvailPhys / GIB, 1), None
    meminfo = pathlib.Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemAvailable:"):
                return round(float(line.split()[1]) / (1 << 20), 1), None
        return None, "/proc/meminfo has no MemAvailable line"
    return None, "no supported way to read available memory on %s" % system


def disk_free() -> list:
    """Free space per volume that ARGUS actually uses."""
    from argus.core import paths
    anchors = {}
    candidates = [pathlib.Path(r.path) for r in paths.describe()]
    candidates.append(pathlib.Path.cwd())
    for p in candidates:
        anchor = pathlib.Path(p).anchor or str(p)
        if anchor and anchor not in anchors:
            anchors[anchor] = None
    out = []
    for anchor in sorted(anchors):
        try:
            u = shutil.disk_usage(anchor)
            out.append({"volume": anchor, "free_gib": round(u.free / GIB, 1),
                        "total_gib": round(u.total / GIB, 1), "why_unknown": None})
        except OSError as exc:
            out.append({"volume": anchor, "free_gib": None, "total_gib": None,
                        "why_unknown": "%s: %s" % (type(exc).__name__, exc)})
    return out


def probe() -> dict:
    """Everything measured, in one record."""
    ram, ram_why = free_ram_gib()
    return {"platform": "%s %s" % (platform.system(), platform.release()),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "cpu_count_why_unknown": None if os.cpu_count() else "os.cpu_count() returned None",
            "free_ram_gib": ram, "free_ram_why_unknown": ram_why,
            "disks": disk_free(),
            "gpu": gpu_probe()}



PROFILES = {
  "cpu_only": {
    "id": "cpu_only",
    "needs": {"vram_mb": 0, "free_ram_gib": 4.0, "free_disk_gib": 5.0},
    "supports": ["setup", "doctor", "demo"],
    "refuses": ["run:ink_inference"],
    "why_refuses": "ink inference on CPU is not slow, it is impractical: the surface stacks "
                   "are billions of voxels. Offering it would be offering a run that never "
                   "finishes, which is worse than saying no.",
    "settings": {"batch_size": 1, "amp": False},
    "first_class": True,
    "note": "a complete profile for setup, diagnosis and the synthetic demonstration."},
  "gpu_6gb": {
    "id": "gpu_6gb",
    "needs": {"vram_mb": 5000, "free_ram_gib": 8.0, "free_disk_gib": FLOOR_FREE_DISK_GIB},
    "supports": ["setup", "doctor", "demo", "run"],
    "refuses": [],
    "why_refuses": None,
    "settings": {"batch_size": 1, "tile": 64, "amp": False},
    "first_class": True,
    "note": "FULLY SUPPORTED. Smaller batches and tiles, identical results. The amp setting "
            "is False by default here because a 6 GB card is frequently pre-Ampere, and the "
            "fp16 trap produces an empty ink map that reads as a confident negative."},
  "gpu_12gb": {
    "id": "gpu_12gb",
    "needs": {"vram_mb": 11000, "free_ram_gib": 8.0, "free_disk_gib": FLOOR_FREE_DISK_GIB},
    "supports": ["setup", "doctor", "demo", "run"],
    "refuses": [],
    "why_refuses": None,
    "settings": {"batch_size": 4, "tile": 128, "amp": True},
    "first_class": True,
    "note": "larger batches; amp is safe only where compute capability is 8.0 or above, "
            "which select() checks rather than assumes."},
}


def select(measured: dict | None = None) -> dict:
    """Choose the profile the MEASUREMENTS support, or say plainly that none is supported."""
    m = measured or probe()
    gpu = m.get("gpu") or {}
    devices = gpu.get("devices") or []
    best = None
    for d in devices:
        if d.get("vram_mb") and (best is None or d["vram_mb"] > best["vram_mb"]):
            best = d
    vram = best.get("vram_mb") if best else 0
    ram = m.get("free_ram_gib")
    disks = [d["free_gib"] for d in (m.get("disks") or []) if d.get("free_gib") is not None]
    free_disk = max(disks) if disks else None

    blockers, chosen = [], None
    for key in ("gpu_12gb", "gpu_6gb", "cpu_only"):
        p = PROFILES[key]
        need = p["needs"]
        why = []
        if need["vram_mb"]:
            if gpu.get("status") != "PRESENT":
                why.append("GPU %s (%s)" % (gpu.get("status"), gpu.get("why")))
            elif not vram:
                why.append("VRAM not reported by the driver")
            elif vram < need["vram_mb"]:
                why.append("%d MB VRAM < %d MB required" % (vram, need["vram_mb"]))
        if ram is None:
            if need["free_ram_gib"] > 0:
                why.append("free RAM UNKNOWN (%s)" % m.get("free_ram_why_unknown"))
        elif ram < need["free_ram_gib"]:
            why.append("%.1f GiB free RAM < %.1f GiB required" % (ram, need["free_ram_gib"]))
        if free_disk is None:
            why.append("free disk UNKNOWN")
        elif free_disk < need["free_disk_gib"]:
            why.append("%.1f GiB free disk < %.1f GiB required"
                       % (free_disk, need["free_disk_gib"]))
        if why:
            blockers.append({"profile": key, "unmet": why})
        elif chosen is None:
            chosen = key

    out = {"measured": m, "selected": chosen, "rejected": blockers,
           "amp_override": None, "warnings": []}
    if chosen is None:
        out["selected"] = None
        out["detail"] = ("no declared profile's floors are met by measured values. This is "
                         "reported rather than papered over: selecting a profile the machine "
                         "cannot honour moves the failure to the middle of a run.")
        return out
    out["profile"] = PROFILES[chosen]
    out["detail"] = PROFILES[chosen]["note"]
    if best is not None and PROFILES[chosen]["settings"].get("amp"):
        cc = best.get("compute_capability")
        if cc is None:
            out["amp_override"] = False
            out["warnings"].append(
                "compute capability UNKNOWN (%s), so automatic mixed precision is turned OFF. "
                "Unknown is not safe: on a pre-Ampere card fp16 autocast yields NaN, NaN "
                "casts to 0 in the uint8 write, and the ink map comes out empty while looking "
                "like a confident negative."
                % best.get("compute_capability_why_unknown"))
        elif cc < TENSOR_CORE_CC:
            out["amp_override"] = False
            out["warnings"].append(
                "compute capability %.1f is pre-Ampere and has no tensor cores; mixed "
                "precision is turned OFF." % cc)
    return out
