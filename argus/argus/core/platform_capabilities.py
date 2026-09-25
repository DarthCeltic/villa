"""Explicit platform selection for CUDA, Apple MPS and CPU."""
from __future__ import annotations

import importlib


class PlatformCapabilityRefusal(ValueError):
    pass


MPS_LANE_STATUS = "STAGED"


def probe_torch_capabilities(torch_module=None) -> dict:
    """Ask torch what this machine can do, and run the required 3-D operator on the accelerator instead of assuming it exists."""
    torch = torch_module
    if torch is None:
        try:
            torch = importlib.import_module("torch")
        except ImportError:
            return {"torch_available": False, "cuda_available": False, "mps_available": False, "mps_max_pool3d": False, "observed_by": "torch missing"}
    caps = {"torch_available": True, "observed_by": "torch probe", "torch_version": getattr(torch, "__version__", None)}
    caps["cuda_available"] = bool(torch.cuda.is_available())
    mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
    caps["mps_available"] = bool(mps_backend is not None and mps_backend.is_available())
    caps["mps_max_pool3d"] = False
    if caps["mps_available"]:
        try:
            x = torch.zeros((1, 1, 4, 4, 4), device="mps")
            torch.nn.functional.max_pool3d(x, 2)
            caps["mps_max_pool3d"] = True
        except Exception as exc:
            caps["mps_max_pool3d_error"] = str(exc)[:200]
    return caps


def select_backend(capabilities: dict, *, require_3d: bool = True,
                   allow_cpu: bool = True) -> dict:
    cuda = bool(capabilities.get("cuda_available"))
    mps = bool(capabilities.get("mps_available"))
    mps_max_pool = bool(capabilities.get("mps_max_pool3d"))
    if cuda:
        return {"backend": "cuda", "dtype": "float32", "fallback": False,
                "reason": "the supplied capabilities report CUDA"}
    if mps:
        if require_3d and not mps_max_pool:
            if not allow_cpu:
                raise PlatformCapabilityRefusal("MPS lacks max_pool3d for required 3-D inference")
            return {"backend": "cpu", "dtype": "float32", "fallback": True,
                    "from": "mps", "reason": "MPS rejected: max_pool3d unavailable; CPU explicitly selected"}
        return {"backend": "mps", "dtype": "float32", "fallback": False,
                "lane_status": MPS_LANE_STATUS, "provider_execution_validated": False,
                "reason": "the supplied capabilities report MPS with the required operators; the MPS lane is STAGED and no provider has been executed on it"}
    if allow_cpu:
        return {"backend": "cpu", "dtype": "float32", "fallback": False,
                "reason": "CPU explicitly selected; no accelerator available"}
    raise PlatformCapabilityRefusal("no requested platform satisfies the operation contract")


def validate_centered_depth_window(*, start: int, length: int, total: int) -> dict:
    if length <= 0 or total < length or start < 0 or start + length > total:
        raise PlatformCapabilityRefusal("depth window is outside the volume")
    expected = (total - length) // 2
    centered = start == expected
    return {"start": start, "length": length, "total": total,
            "expected_start": expected, "centered": centered,
            "status": "PASS" if centered else "REFUSE"}
