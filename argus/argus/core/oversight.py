"""System oversight: what this machine can do, and what it is safe to run here."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATA_ROOTS = [_argus_public_path('anchor', ''), _argus_public_path('anchor', ''), str(ROOT)]

TOOLS = {
    "python": "the engine itself",
    "docker": "the sandboxed compiler oracle and the PB eval harness",
    "git": "provenance, and every reproduction command in Evidence",
    "nvidia-smi": "GPU inference for the detector",
    "node": "the ARGUS interface",
}

PACKAGES = {
    "numpy": "every array path",
    "tifffile": "reading tifxyz meshes",
    "fastapi": "the read-only service behind the interface",
    "torch": "detector inference",
    "zarr": "convenience volume access (ARGUS's own reader does not need it)",
}


def _run(cmd: list[str], timeout: float = 6.0) -> tuple[bool, str]:
    """A fixed argv, no shell, no user input."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout or p.stderr or "").strip()[:200]
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:120])


def host() -> dict:
    """Identity and size of the machine, each field with its source."""
    import multiprocessing

    ram = None
    try:
        import psutil

        ram = round(psutil.virtual_memory().total / 1024 ** 3, 1)
    except Exception:
        pass
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "cpu_logical": multiprocessing.cpu_count(),
        "ram_gib": ram,
        "ram_source": "psutil" if ram is not None else "unavailable (psutil not installed)",
        "cwd": str(ROOT),
    }


def gpus() -> dict:
    """Accelerators, from the vendor tool rather than from a guess."""
    if shutil.which("nvidia-smi") is None:
        return {"present": False, "why": "nvidia-smi is not on PATH",
                "consequence": ("detector inference runs on CPU, which is workable for a "
                                "single surface and not for a sweep")}
    ok, out = _run(["nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,driver_version",
                    "--format=csv,noheader,nounits"])
    if not ok:
        return {"present": False, "why": out}
    cards = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            cards.append({"name": parts[0], "vram_total_mib": _int(parts[1]),
                          "vram_used_mib": _int(parts[2]), "driver": parts[3]})
    return {"present": bool(cards), "cards": cards, "source": "nvidia-smi"}


def _int(s: str):
    try:
        return int(float(s))
    except ValueError:
        return None


def disks() -> list:
    """Free space where the material lives."""
    out = []
    for r in DATA_ROOTS:
        p = Path(r)
        if not p.exists():
            out.append({"root": r, "present": False})
            continue
        try:
            u = shutil.disk_usage(str(p))
        except OSError as e:
            out.append({"root": r, "present": True, "error": str(e)[:120]})
            continue
        out.append({"root": r, "present": True,
                    "total_gib": round(u.total / 1024 ** 3, 1),
                    "free_gib": round(u.free / 1024 ** 3, 1),
                    "used_pct": round(100 * u.used / max(u.total, 1), 1)})
    return out


def toolchain() -> dict:
    tools = {}
    for name, why in TOOLS.items():
        path = shutil.which(name)
        row = {"present": path is not None, "path": path, "unlocks": why}
        if path:
            flag = "--version" if name != "nvidia-smi" else "--version"
            ok, out = _run([name, flag])
            row["version"] = out.splitlines()[0] if ok and out else None
        tools[name] = row

    pkgs = {}
    import importlib.util

    for name, why in PACKAGES.items():
        spec = importlib.util.find_spec(name)
        pkgs[name] = {"present": spec is not None, "unlocks": why}
    return {"tools": tools, "packages": pkgs}


def boundary() -> dict:
    """The execution posture, stated rather than assumed."""
    svc = ROOT / "argus" / "service" / "app.py"
    src = svc.read_text(encoding="utf-8") if svc.is_file() else ""
    return {
        "service_read_only": "WRITE_METHODS" in src and "refuse_writes" in src,
        "no_write_routes": "@app.post" not in src and "@app.put" not in src,
        "sealed_files_refused": "423" in src,
        "ui_can_start_work": False,
        "note": ("the interface and its service are observational; every scientific action "
                 "is an operator command, and the reproduction command is shown as text "
                 "rather than as a button for exactly that reason"),
    }


def recommendations(snapshot: dict) -> list:
    """Derived, never invented."""
    recs = []
    tc = snapshot["toolchain"]
    g = snapshot["gpu"]
    ds = {d["root"]: d for d in snapshot["disks"]}

    if not g.get("present"):
        recs.append({
            "priority": "high",
            "title": "No GPU is visible to this process",
            "because": g.get("why", "nvidia-smi reported nothing"),
            "unblocks": ("detector inference at sweep scale; a single surface is feasible "
                         "on CPU, twelve are not"),
            "action": ("confirm the driver with `nvidia-smi`; without a usable card, run inference on a "
                       "machine that has one"),
        })
    else:
        for c in g.get("cards", []):
            if (c.get("vram_total_mib") or 0) < 8000:
                recs.append({
                    "priority": "medium",
                    "title": "GPU memory is tight for detector inference",
                    "because": "%s reports %s MiB total" % (c["name"], c["vram_total_mib"]),
                    "unblocks": "reading a full segment without tiling the stack",
                    "action": "reduce the patch batch, or read on a larger card",
                })

    if not tc["tools"]["docker"]["present"]:
        recs.append({
            "priority": "medium",
            "title": "Docker is not on PATH",
            "because": "no `docker` executable was found",
            "unblocks": "the sandboxed oracle path and the eval harness",
            "action": "install Docker Desktop, or keep this box for analysis only",
        })
    if not tc["packages"]["torch"]["present"]:
        recs.append({
            "priority": "high",
            "title": "torch is not installed in this interpreter",
            "because": "importlib could not find `torch` under %s" % sys.executable,
            "unblocks": "every detector reading",
            "action": ("install the build that matches the driver reported above; a CPU "
                       "wheel on a CUDA box is a silent 30x slowdown"),
        })

    t = ds.get(_argus_public_path('anchor', ''))
    if t and t.get("present") and (t.get("free_gib") or 0) < 200:
        recs.append({
            "priority": "high",
            "title": "The data drive is nearly full",
            "because": "the data drive reports %s GiB free of %s" % (t.get("free_gib"), t.get("total_gib")),
            "unblocks": "staging any further volume material",
            "action": ("free space deliberately — and check junction targets before any "
                       "bulk delete"),
        })

    if not snapshot["sources"]["any_reachable"]:
        recs.append({
            "priority": "high",
            "title": "No upstream source answered",
            "because": "every declared source probe failed",
            "unblocks": "fetching any material at all",
            "action": "check connectivity before concluding anything about the data",
        })

    recs.append({
        "priority": "info",
        "title": "Detector qualification, not this machine, decides what a reading may claim",
        "because": ("VIGILES refuses a reading at an acquisition that has no declared known-ink "
                    "control before inference begins"),
        "unblocks": "any ink claim on a prize target",
        "action": ("obtain or produce a control at the target acquisition family; more "
                   "compute does not substitute for calibration"),
    })
    return recs


def snapshot(*, live_sources: bool = False) -> dict:
    """Everything above, in one record."""
    from argus.core import sources as S

    inv = S.inventory(live=live_sources)
    reach = [s for s in inv["sources"] if s["probe_result"].get("reachable")]
    snap = {
        "schema": "argus-oversight-v1",
        "generated_at": time.time(),
        "host": host(),
        "gpu": gpus(),
        "disks": disks(),
        "toolchain": toolchain(),
        "boundary": boundary(),
        "sources": {
            "checked": live_sources,
            "any_reachable": bool(reach) if live_sources else True,
            "reachable": [s["id"] for s in reach],
            "held": [s["id"] for s in inv["sources"] if s["local"]["any_present"]],
        },
        "environment_flags": {
            k: bool(os.environ.get(k))
            for k in ("HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "AWS_ACCESS_KEY_ID")
        },
        "note": ("presence of a credential is reported; its value is never read, logged or "
                 "returned"),
    }
    snap["recommendations"] = recommendations(snap)
    return snap
