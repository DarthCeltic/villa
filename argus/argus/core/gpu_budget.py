"""A consumer card's GPU memory budget, enforced rather than assumed."""
from __future__ import annotations

import os

DEFAULT_RESERVE_GIB = 0.5


def card_total_gib(torch=None) -> float | None:
    if torch is not None:
        try:
            return torch.cuda.get_device_properties(0).total_memory / 2**30
        except Exception:
            return None
    try:
        import json
        from pathlib import Path
        floors = json.loads((Path(__file__).with_name("resource_floors.json")).read_text(encoding="utf-8"))
        return floors["totals"]["card_total_vram_mb"] / 1024.0
    except Exception:
        return None


def cap_gib(total_gib: float | None, free_gib: float | None = None) -> float | None:
    """ARGUS_VRAM_CAP_GIB if set, else the card minus a reserve; never above what is free now."""
    if total_gib is None:
        return None
    env = os.environ.get("ARGUS_VRAM_CAP_GIB")
    try:
        cap = float(env) if env else total_gib - DEFAULT_RESERVE_GIB
    except ValueError:
        cap = total_gib - DEFAULT_RESERVE_GIB
    if free_gib is not None:
        cap = min(cap, max(0.25, free_gib - 0.25))
    return round(max(0.25, min(cap, total_gib)), 3)


def merge_alloc_conf(existing: str | None, fraction: float) -> str:
    """PYTORCH_CUDA_ALLOC_CONF with a per-process fraction; a stricter one already present wins."""
    parts = [p for p in (existing or "").split(",") if p.strip()]
    kept, prior = [], None
    for p in parts:
        k, _, v = p.partition(":")
        if k.strip() == "per_process_memory_fraction":
            try:
                prior = float(v)
            except ValueError:
                prior = None
        else:
            kept.append(p.strip())
    frac = min(fraction, prior) if prior is not None else fraction
    return ",".join(kept + ["per_process_memory_fraction:%.4f" % frac])


def child_env(env=None, *, total_gib: float | None = None) -> dict:
    """A copy of `env` whose torch child processes cannot allocate past the ARGUS cap."""
    out = dict(os.environ if env is None else env)
    total = total_gib if total_gib is not None else card_total_gib()
    cap = cap_gib(total)
    if total and cap:
        out["PYTORCH_CUDA_ALLOC_CONF"] = merge_alloc_conf(out.get("PYTORCH_CUDA_ALLOC_CONF"), cap / total)
    return out


def enforce_in_process(torch=None, device: int = 0) -> dict:
    """Cap this process's CUDA allocator before an ARGUS GPU path allocates."""
    if torch is None:
        import torch
    if not torch.cuda.is_available():
        return {"applied": False, "why": "CUDA is not available in this process"}
    free_b, total_b = torch.cuda.mem_get_info(device)
    total, free = total_b / 2**30, free_b / 2**30
    cap = cap_gib(total, free)
    torch.cuda.set_per_process_memory_fraction(cap / total, device=device)
    return {"applied": True, "device": torch.cuda.get_device_name(device), "total_gib": round(total, 3),
            "free_at_start_gib": round(free, 3), "cap_gib": cap, "fraction": round(cap / total, 4),
            "rule": "allocations past the cap raise OutOfMemoryError instead of spilling into system RAM"}


def doctor_row() -> dict:
    """What `argus doctor` reports about the cap (no torch import, no CUDA context)."""
    conf = os.environ.get("PYTORCH_CUDA_ALLOC_CONF") or ""
    total = card_total_gib()
    return {
        "card_total_gib": total, "default_cap_gib": cap_gib(total),
        "env_cap_present": "per_process_memory_fraction" in conf,
        "argus_gpu_paths_capped": True,
        "driver_policy": "UNKNOWN: ARGUS cannot read the driver's CUDA Sysmem Fallback Policy. Set it in "
                         "NVIDIA Control Panel > Manage 3D settings > Global > CUDA - Sysmem Fallback Policy "
                         "= Prefer No Sysmem Fallback, so every program on this machine fails with an OOM "
                         "instead of silently spilling into system RAM.",
    }


def selftest() -> bool:
    assert merge_alloc_conf(None, 0.9) == "per_process_memory_fraction:0.9000"
    assert merge_alloc_conf("expandable_segments:True", 0.9) == "expandable_segments:True,per_process_memory_fraction:0.9000"
    assert merge_alloc_conf("per_process_memory_fraction:0.5", 0.9).endswith(":0.5000")
    assert cap_gib(6.0) == 5.5 and cap_gib(6.0, free_gib=4.0) == 3.75 and cap_gib(None) is None
    env = child_env({"PATH": "x"}, total_gib=6.0)
    assert env["PYTORCH_CUDA_ALLOC_CONF"] == "per_process_memory_fraction:0.9167" and env["PATH"] == "x"

    class _Cuda:
        applied = None
        def is_available(self): return True
        def mem_get_info(self, d): return (5 * 2**30, 6 * 2**30)
        def set_per_process_memory_fraction(self, f, device=0): _Cuda.applied = f
        def get_device_name(self, d): return "fixture 6 GB"

    class _Torch:
        cuda = _Cuda()
    r = enforce_in_process(_Torch)
    assert r["applied"] and r["cap_gib"] == 4.75 and abs(_Cuda.applied - 4.75 / 6) < 1e-9
    return True
