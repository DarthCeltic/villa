"""Explicit liveness and readiness for the observe service."""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import time

STARTED_MONOTONIC = time.monotonic()
WARMING_BOUND_S = 120.0
GIB = 1 << 30
MIN_WRITE_GIB = 2.0
PROFILE_ENV = "ARGUS_PROFILE"

OK, DEGRADED, UNKNOWN, FAIL = "OK", "DEGRADED", "UNKNOWN", "FAIL"


_PATHISH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![:\w])//|/(?:home|Users|root|opt|usr|var|tmp)/)[^\s'\"\]\)>,;]*")


def scrub(text):
    """Readiness is shown to whoever can reach the port: no absolute paths, user names or interpreter locations in its sentences."""
    return _PATHISH.sub("<path>", text) if isinstance(text, str) else text


def liveness() -> dict:
    return {"live": True, "service": "argus", "uptime_s": round(time.monotonic() - STARTED_MONOTONIC, 1)}


def _check(name: str, state: str, why: str, next_action: str | None = None, **facts) -> dict:
    return {"check": name, "state": state, "why": scrub(why), "next": scrub(next_action), **facts}


def active_profile(measured: dict | None = None) -> dict:
    """The profile in force: an explicit ARGUS_PROFILE that names a declared profile, else the one the measurements support, else none."""
    from argus.cli import hardware as HW

    named = (os.environ.get(PROFILE_ENV) or "").strip()
    if named:
        if named in HW.PROFILES:
            return {"id": named, "source": "ARGUS_PROFILE", "settings": HW.PROFILES[named]["settings"], "needs": HW.PROFILES[named]["needs"]}
        return {"id": None, "source": "ARGUS_PROFILE", "why": "ARGUS_PROFILE=%r is not one of %s" % (named, ", ".join(sorted(HW.PROFILES)))}
    sel = HW.select(measured)
    if sel.get("selected"):
        p = HW.PROFILES[sel["selected"]]
        return {"id": p["id"], "source": "measured", "settings": p["settings"], "needs": p["needs"], "warnings": sel.get("warnings", [])}
    return {"id": None, "source": "measured", "why": sel.get("detail"), "rejected": sel.get("rejected", [])}


def _artifact_root() -> pathlib.Path | None:
    try:
        from argus.core import paths

        return pathlib.Path(paths.artifact_write_root())
    except Exception:
        return None


def compute(*, primed: bool | None, pools: list[dict], measured: dict | None = None, provider_lifecycle: dict | None = None,
            gpu_memory: dict | None = None, artifact_root: pathlib.Path | None = None, now: float | None = None) -> dict:
    """Assemble the readiness record from measurements handed in, so every branch is testable without a machine to match."""
    from argus.cli import hardware as HW

    m = measured if measured is not None else HW.probe()
    prof = active_profile(m)
    needs = prof.get("needs") or {}
    checks = []

    age = (now if now is not None else time.monotonic()) - STARTED_MONOTONIC
    if primed:
        checks.append(_check("caches", OK, "the routes every page asks for on load have been read once"))
    elif age > WARMING_BOUND_S:
        checks.append(_check("caches", DEGRADED, "start-up warming did not finish within %d s; pages will do their own work and may be slow" % WARMING_BOUND_S,
                             "open System > Runtime to see which route is slow"))
    else:
        checks.append(_check("caches", "WARMING", "start-up warming is still reading the first routes (%.0f s in, bound %d s)" % (age, WARMING_BOUND_S),
                             "wait; the first page load is slow until this finishes"))

    for pool in pools:
        if pool["level"] == "SATURATED":
            checks.append(_check("workers." + pool["name"], FAIL, "%d workers and %d waiting slots are all taken; new heavy requests are being refused" % (pool["max_workers"], pool["max_pending"]),
                                 "wait a few seconds and retry; if it persists raise ARGUS_HEAVY_WORKERS", pool=pool))
        else:
            checks.append(_check("workers." + pool["name"], OK, "%d running, %d waiting, cap %d workers" % (pool["running"], pool["queued"], pool["max_workers"]), pool=pool))

    disks = m.get("disks") or []
    root = artifact_root if artifact_root is not None else _artifact_root()
    free = None
    if root is not None:
        try:
            free = round(shutil.disk_usage(root if root.exists() else root.anchor or str(root)).free / GIB, 1)
        except OSError:
            free = None
    if free is None:
        checks.append(_check("storage", UNKNOWN, "free space where receipts are written could not be read", "check that the artifacts directory exists and is mounted"))
    elif free < MIN_WRITE_GIB:
        checks.append(_check("storage", FAIL, "%.1f GiB free where receipts are written (need at least %.0f)" % (free, MIN_WRITE_GIB), "free disk space before starting any job", free_gib=free))
    elif needs.get("free_disk_gib") and free < needs["free_disk_gib"]:
        checks.append(_check("storage", DEGRADED, "%.1f GiB free; the %s profile wants %.0f GiB before it will start acquisition or GPU stages" % (free, prof["id"], needs["free_disk_gib"]),
                             "free disk space or point ARGUS at a larger drive", free_gib=free))
    else:
        checks.append(_check("storage", OK, "%.1f GiB free where receipts are written" % free, free_gib=free, volumes=len(disks)))

    ram = m.get("free_ram_gib")
    if ram is None:
        checks.append(_check("memory", UNKNOWN, "free RAM could not be read: %s" % m.get("free_ram_why_unknown"), None))
    elif needs.get("free_ram_gib") and ram < needs["free_ram_gib"]:
        checks.append(_check("memory", DEGRADED, "%.1f GiB RAM free; the %s profile wants %.0f GiB" % (ram, prof["id"], needs["free_ram_gib"]), "close other programs before running a stage", free_gib=ram))
    else:
        checks.append(_check("memory", OK, "%.1f GiB RAM free" % ram, free_gib=ram))

    gpu = m.get("gpu") or {}
    gm = gpu_memory or {}
    if gpu.get("status") == "PRESENT":
        dev = (gpu.get("devices") or [{}])[0]
        need_v = needs.get("vram_mb") or 0
        if gm.get("status") == "MEASURED" and need_v and gm["free_mb"] < need_v:
            checks.append(_check("gpu", DEGRADED, "%s: %d MiB VRAM free of %d; the %s profile wants %d free. GPU stages will refuse until it is freed" % (dev.get("name"), gm["free_mb"], gm["total_mb"], prof["id"], need_v),
                                 "close other GPU programs (browsers and video players use VRAM)", vram_free_mb=gm["free_mb"]))
        elif gm.get("status") != "MEASURED" and need_v:
            checks.append(_check("gpu", UNKNOWN, "%s is present but free VRAM could not be read" % dev.get("name"), "run nvidia-smi by hand and read its error"))
        else:
            checks.append(_check("gpu", OK, "%s, %s MiB VRAM free" % (dev.get("name"), gm.get("free_mb", "unknown")), vram_free_mb=gm.get("free_mb")))
    elif gpu.get("status") == "ABSENT":
        checks.append(_check("gpu", OK, "no NVIDIA GPU: browsing, review, evidence and CPU stages work; GPU stages will say why they cannot run", None))
    else:
        checks.append(_check("gpu", UNKNOWN, gpu.get("why") or "the GPU could not be queried", gpu.get("fix")))

    if provider_lifecycle is None:
        checks.append(_check("providers", UNKNOWN, "provider lifecycle was not read", None))
    else:
        checks.append(_check("providers", provider_lifecycle.get("state", UNKNOWN),
                             "%s sources, %d staged update(s), %d held; policy %s" % (provider_lifecycle.get("sources", "?"), len(provider_lifecycle.get("staged", [])),
                                                                                      provider_lifecycle.get("held", 0), provider_lifecycle.get("policy", "?"))
                             if provider_lifecycle.get("state") != UNKNOWN else provider_lifecycle.get("why", "unknown"),
                             provider_lifecycle.get("next"), lifecycle=provider_lifecycle))

    failed = [c for c in checks if c["state"] == FAIL]
    warming = [c for c in checks if c["state"] == "WARMING"]
    ready = not failed and not warming
    order = {FAIL: 4, "WARMING": 3, DEGRADED: 2, UNKNOWN: 1, OK: 0}
    overall = max((c["state"] for c in checks), key=lambda s: order.get(s, 0))
    nxt = next((c["next"] for c in failed + warming + [c for c in checks if c["state"] == DEGRADED] if c.get("next")), None)
    return {"schema": "argus-readiness-v1", "ready": ready, "state": overall, "profile": {k: prof.get(k) for k in ("id", "source", "settings", "why")},
            "checks": checks, "next": nxt, "uptime_s": round(age, 1),
            "rule": "ready means no check failed and start-up warming finished; a check that could not be measured says UNKNOWN and is never a pass"}
