"""Install tiers, component health, and the resource guards that keep a user off the debugging path."""
from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from argus.core import storage_policy

Runner = Callable[[str, dict], dict]

PINNED_BY_DIGEST = "PINNED_BY_DIGEST"
TAG_ONLY = "TAG_ONLY_DIGEST_NOT_RESOLVED"
VERSION_PINNED = "VERSION_PINNED"
PIN_STATUSES = (PINNED_BY_DIGEST, TAG_ONLY, VERSION_PINNED)

KINDS = ("python_package", "container_image", "binary", "external_app", "remote_service")
STATES = ("READY", "MISSING", "DEGRADED", "UNKNOWN")

UPSTREAM_COMMIT = "739eefd71a677bebb983186ae3d0a6bace669934"

UNKNOWN_SIZE_PLANNING_GIB = 10.0
REPAIR_DISK_MARGIN_FRACTION = 0.2
REPAIR_DISK_MARGIN_MIN_GIB = 5.0



TIERS: dict[str, dict] = {
    "core": {
        "order": 1,
        "label": "Core",
        "plain": ("The ARGUS interface and backend, data streaming, inspection and the "
                  "Python model providers; enough to look at scrolls and run pure-Python work."),
        "requires_source_build": False,
    },
    "geometry": {
        "order": 2,
        "label": "Geometry",
        "plain": ("Surface geometry through the official Volume Cartographer image or a "
                  "packaged binary, plus the Lasagna and spiral components, with an automatic "
                  "health check."),
        "requires_source_build": False,
    },
    "advanced": {
        "order": 3,
        "label": "Advanced",
        "plain": ("Optional extras such as Blender and research providers that most users "
                  "never need."),
        "requires_source_build": False,
    },
    "remote": {
        "order": 4,
        "label": "Remote GPU",
        "plain": ("Run larger jobs on a bigger remote GPU and get a receipt back into this "
                  "local ARGUS."),
        "requires_source_build": False,
    },
}

DEVELOPER_TIER: dict = {
    "order": 99,
    "label": "Developer",
    "plain": ("Building Volume Cartographer from source, including the Ceres and flang "
              "toolchains; only for people changing that code."),
    "requires_source_build": True,
    "developer_mode_only": True,
}


def visible_tiers(*, developer_mode: bool = False) -> dict[str, dict]:
    """The ordered tiers a person may see."""
    out = dict(sorted(TIERS.items(), key=lambda kv: kv[1]["order"]))
    if developer_mode:
        out["developer"] = DEVELOPER_TIER
    return out



@dataclass(frozen=True)
class Component:
    id: str
    tier: str
    label: str
    kind: str
    pin: dict
    pin_status: str
    check: dict
    install_plan: list
    repair_plan: list
    approx_disk_gib: float | None
    needs_gpu: bool
    needs_admin: bool
    required: bool = True
    group: str | None = None
    facts: dict = field(default_factory=dict)


def _step(text: str, command: str | None = None, *, downloads: bool = False,
          installs: bool = False, needs_approval: bool | None = None,
          developer_only: bool = False) -> dict:
    approval = (downloads or installs) if needs_approval is None else needs_approval
    return {"text": text, "command": command, "downloads": downloads, "installs": installs,
            "needs_approval": approval, "developer_only": developer_only}


def _pin(tag: str | None = None, digest: str | None = None, version: str | None = None) -> dict:
    return {"tag": tag, "digest": digest, "version": version}


_VC3D_IMAGE = "ghcr.io/scrollprize/villa/volume-cartographer:edge"

COMPONENTS: tuple[Component, ...] = (
    Component(
        id="argus_backend", tier="core", label="ARGUS backend and interface",
        kind="python_package", pin=_pin(version="0.1.0"), pin_status=VERSION_PINNED,
        check={"kind": "python_import", "module": "argus"},
        install_plan=[_step("Install ARGUS into a Python 3.11+ environment.",
                            "python -m pip install -e .", installs=True)],
        repair_plan=[_step("Reinstall ARGUS into the active Python environment.",
                           "python -m pip install -e . --force-reinstall --no-deps",
                           installs=True)],
        approx_disk_gib=None, needs_gpu=False, needs_admin=False,
        facts={"version_source": ("pyproject.toml [project].version; the distribution name "
                                  "there is still the pre-rename one")}),
    Component(
        id="model_runtime_torch", tier="core", label="Python model runtime (torch)",
        kind="python_package", pin=_pin(version="2.14.0+cu126"), pin_status=VERSION_PINNED,
        check={"kind": "python_import", "module": "torch"},
        install_plan=[_step("Install the pinned torch build that matches the GPU driver.",
                            "python -m pip install torch==2.14.0+cu126 "
                            "--index-url https://download.pytorch.org/whl/cu126",
                            downloads=True, installs=True)],
        repair_plan=[_step("Reinstall the pinned torch build; a CPU wheel on a CUDA machine "
                           "is a silent large slowdown.",
                           "python -m pip install --force-reinstall torch==2.14.0+cu126 "
                           "--index-url https://download.pytorch.org/whl/cu126",
                           downloads=True, installs=True)],
        approx_disk_gib=None, needs_gpu=False, needs_admin=False, required=False,
        facts={"version_source": "runtime/requirements.lock.txt",
               "note": ("optional: pure-Python work runs without it; spiral-fitting needs "
                        "torch<2.13 and therefore its own environment")}),
    Component(
        id="vc3d_docker", tier="geometry", label="VC3D official Docker image",
        kind="container_image", pin=_pin(tag="edge"), pin_status=TAG_ONLY,
        check={"kind": "docker_image", "reference": _VC3D_IMAGE},
        install_plan=[
            _step("Pull the official Volume Cartographer image.", "docker pull " + _VC3D_IMAGE,
                  downloads=True),
            _step("Resolve and record the image digest so the pin stops being a mutable tag.",
                  "docker buildx imagetools inspect " + _VC3D_IMAGE, needs_approval=False)],
        repair_plan=[
            _step("Re-pull the official Volume Cartographer image.", "docker pull " + _VC3D_IMAGE,
                  downloads=True),
            _step("Record the digest of the pulled image.",
                  "docker buildx imagetools inspect " + _VC3D_IMAGE, needs_approval=False),
            _step("Developer mode only: compile Volume Cartographer from source (needs the "
                  "Ceres solver and a Fortran toolchain such as flang).", None,
                  developer_only=True)],
        approx_disk_gib=None, needs_gpu=False, needs_admin=False, group="vc3d",
        facts={"alt_tag": "main",
               "legacy_package": "ghcr.io/scrollprize/volume-cartographer (tags edge, dev-next)",
               "cli_tools_included": True,
               "requires": "a working Docker engine",
               "source": "upstream README recommends the Docker image"}),
    Component(
        id="vc3d_binary", tier="geometry", label="VC3D packaged binary (Windows)",
        kind="binary", pin=_pin(tag="stable"), pin_status=TAG_ONLY,
        check={"kind": "binary_on_path", "names": ["VC3D.exe", "VC3D"]},
        install_plan=[
            _step("Download the win64.zip asset from the rolling `stable` GitHub release and "
                  "extract it to a folder of your choice.", None, downloads=True),
            _step("Put that folder on PATH so ARGUS can find VC3D.", None,
                  needs_approval=True)],
        repair_plan=[
            _step("Download the current win64.zip asset from the `stable` release again and "
                  "replace the extracted folder.", None, downloads=True)],
        approx_disk_gib=None, needs_gpu=False, needs_admin=False, group="vc3d",
        facts={"release_channels": ["latest", "stable"],
               "asset_pattern": "VC3D-<sha>-<date>-win64.zip",
               "other_assets": ["linux-x86_64.AppImage", "macos-arm64.dmg", "macos-arm64.zip",
                                "win64.exe"],
               "cli_tools_verified": False,
               "cli_tools_note": ("not verified whether this packaged binary ships vc_* "
                                  "command-line tools such as vc_render_tifxyz; the Docker "
                                  "image does")}),
    Component(
        id="lasagna", tier="geometry", label="Lasagna fitting component",
        kind="python_package", pin=_pin(version="villa@" + UPSTREAM_COMMIT),
        pin_status=VERSION_PINNED,
        check={"kind": "python_import", "module": "lasagna3d"},
        install_plan=[_step("Install the pinned Lasagna component into its own Linux or WSL "
                            "environment (a packaged installer is not published yet).", None,
                            installs=True)],
        repair_plan=[_step("Reinstall the pinned Lasagna component in its Linux or WSL "
                           "environment.", None, installs=True)],
        approx_disk_gib=None, needs_gpu=True, needs_admin=False,
        facts={"platform_note": "Linux or WSL; GPU required",
               "import_name_source": ("directory name in the pinned checkout, not verified "
                                      "against an installed distribution")}),
    Component(
        id="spiral_fitting", tier="geometry", label="Spiral-fitting component",
        kind="python_package", pin=_pin(version="villa@" + UPSTREAM_COMMIT),
        pin_status=VERSION_PINNED,
        check={"kind": "python_import", "module": "vc_spiral"},
        install_plan=[_step("Install the pinned spiral-fitting component into its own "
                            "environment (Python 3.14+, torch below 2.13).", None,
                            installs=True)],
        repair_plan=[_step("Reinstall the pinned spiral-fitting component in its own "
                           "environment.", None, installs=True)],
        approx_disk_gib=None, needs_gpu=True, needs_admin=False, required=False,
        facts={"platform_note": "Linux or WSL; GPU required; separate environment",
               "requires_python": ">=3.14"}),
    Component(
        id="blender", tier="advanced", label="Blender",
        kind="external_app", pin=_pin(), pin_status=TAG_ONLY,
        check={"kind": "binary_on_path", "names": ["blender", "blender.exe"]},
        install_plan=[_step("Install Blender from blender.org or your package manager.",
                            None, downloads=True, installs=True)],
        repair_plan=[_step("Reinstall Blender and make sure it is on PATH.", None,
                           downloads=True, installs=True)],
        approx_disk_gib=None, needs_gpu=False, needs_admin=False,
        facts={"note": "no version is pinned; used for visual inspection, not measurement"}),
    Component(
        id="remote_gpu_worker", tier="remote", label="Remote GPU worker",
        kind="remote_service", pin=_pin(), pin_status=TAG_ONLY,
        check={"kind": "http_health", "url": None},
        install_plan=[_step("Register a remote GPU worker endpoint in ARGUS settings; inputs "
                            "are sent to it and a receipt is returned to this machine.", None,
                            needs_approval=True)],
        repair_plan=[_step("Re-check the remote worker endpoint and credentials in ARGUS "
                           "settings.", None, needs_approval=True)],
        approx_disk_gib=0.0, needs_gpu=False, needs_admin=False,
        facts={"note": "no endpoint is configured by default"}),
)

DEVELOPER_COMPONENTS: tuple[Component, ...] = (
    Component(
        id="dev_cmake_toolchain", tier="developer", label="CMake and Ninja",
        kind="binary", pin=_pin(), pin_status=TAG_ONLY,
        check={"kind": "binary_on_path", "names": ["cmake", "ninja"]},
        install_plan=[_step("Install CMake and Ninja.", None, downloads=True, installs=True,
                            developer_only=True)],
        repair_plan=[_step("Reinstall CMake and Ninja.", None, downloads=True, installs=True,
                           developer_only=True)],
        approx_disk_gib=None, needs_gpu=False, needs_admin=True),
    Component(
        id="dev_ceres_flang", tier="developer", label="Ceres solver and flang toolchain",
        kind="binary", pin=_pin(), pin_status=TAG_ONLY,
        check={"kind": "binary_on_path", "names": ["flang", "flang-new"]},
        install_plan=[_step("Install the flang compiler and build the Ceres solver "
                            "dependency closure.", None, downloads=True, installs=True,
                            developer_only=True)],
        repair_plan=[_step("Reinstall flang and rebuild the Ceres solver.", None,
                           downloads=True, installs=True, developer_only=True)],
        approx_disk_gib=None, needs_gpu=False, needs_admin=True),
    Component(
        id="dev_vc_source_build", tier="developer", label="Volume Cartographer source build",
        kind="binary", pin=_pin(), pin_status=TAG_ONLY,
        check={"kind": "file_exists", "path": None},
        install_plan=[_step("Compile Volume Cartographer from the pinned checkout with the "
                            "documented CMake preset.", None, installs=True,
                            developer_only=True)],
        repair_plan=[_step("Recompile Volume Cartographer from the pinned checkout.", None,
                           installs=True, developer_only=True)],
        approx_disk_gib=None, needs_gpu=False, needs_admin=False,
        facts={"note": "output path depends on the chosen preset and checkout location"}),
)


def active_components(components: Sequence[Component] | None = None, *,
                      developer_mode: bool = False) -> tuple[Component, ...]:
    base = tuple(COMPONENTS if components is None else components)
    if developer_mode and components is None:
        base = base + DEVELOPER_COMPONENTS
    if not developer_mode:
        base = tuple(c for c in base if c.tier != "developer")
    return base



_TARGET_KEYS = {"python_import": "module", "docker_image": "reference",
                "binary_on_path": "names", "file_exists": "path", "http_health": "url"}


def _target(spec: dict) -> Any:
    key = _TARGET_KEYS.get(spec.get("kind", ""))
    return spec.get(key) if key else None


def _result(c: Component, state: str, detail: str, *, observed_digest: str | None = None,
            scientific: bool = False) -> dict:
    out = {"id": c.id, "tier": c.tier, "label": c.label, "state": state, "detail": detail,
           "pin_status": c.pin_status, "scientific_use_allowed": scientific,
           "required": c.required, "group": c.group, "observed_digest": observed_digest}
    if "cli_tools_verified" in c.facts:
        out["cli_tools_verified"] = c.facts["cli_tools_verified"]
    return out


def check_component(component: Component, runner: Runner) -> dict:
    """Probe one component through the injected runner."""
    spec = component.check
    if not _target(spec):
        return _result(component, "UNKNOWN", "no probe target is configured for this component")
    try:
        res = runner(spec["kind"], dict(spec))
    except Exception as e:
        return _result(component, "UNKNOWN", "probe raised %s" % type(e).__name__)
    if not isinstance(res, dict) or res.get("ok") is None:
        detail = res.get("detail") if isinstance(res, dict) else None
        return _result(component, "UNKNOWN", detail or "the probe gave no answer")

    detail = str(res.get("detail") or "")
    digest = res.get("digest")
    if not res["ok"]:
        return _result(component, "MISSING", detail or "not found", observed_digest=digest)

    state = "READY"
    scientific = True
    notes: list[str] = []
    pinned_digest = component.pin.get("digest")
    if component.pin_status == TAG_ONLY:
        scientific = False
        notes.append("pin is a mutable tag or unset; digest not resolved; not allowed for "
                     "scientific use")
        if digest:
            notes.append("observed digest %s can be recorded as the pin" % digest)
    elif component.pin_status == PINNED_BY_DIGEST:
        if digest is None:
            scientific = False
            notes.append("digest not observed; cannot confirm the pin")
        elif digest != pinned_digest:
            state, scientific = "DEGRADED", False
            notes.append("observed digest %s differs from the pinned digest" % digest)
    if res.get("degraded"):
        state, scientific = "DEGRADED", False
    if component.facts.get("cli_tools_verified") is False:
        notes.append("command-line tools not verified in this packaging")
    full = "; ".join([p for p in [detail] + notes if p])
    return _result(component, state, full, observed_digest=digest, scientific=scientific)


_RANK = {"READY": 3, "DEGRADED": 2, "UNKNOWN": 1, "MISSING": 0}


def _unit_states(comps: Sequence[Component], results: dict[str, dict]) -> tuple[list, list]:
    """Required units: each ungrouped required component, and each group as one unit."""
    units: dict[str, list[dict]] = {}
    for c in comps:
        if not c.required or c.id not in results:
            continue
        units.setdefault(c.group or c.id, []).append(results[c.id])
    states, blocking = [], []
    for rows in units.values():
        best = max(rows, key=lambda r: _RANK[r["state"]])
        states.append(best["state"])
        if best["state"] != "READY":
            blocking.extend(r["id"] for r in rows)
    return states, blocking


def _aggregate(states: list[str]) -> str:
    if not states:
        return "UNKNOWN"
    if all(s == "READY" for s in states):
        return "READY"
    if all(s == "MISSING" for s in states):
        return "MISSING"
    if "DEGRADED" in states or ("MISSING" in states and "READY" in states):
        return "DEGRADED"
    return "UNKNOWN"


def check_all(runner: Runner, *, developer_mode: bool = False,
              components: Sequence[Component] | None = None) -> dict:
    """Health of every component plus a per-tier verdict."""
    comps = active_components(components, developer_mode=developer_mode)
    rows = [check_component(c, runner) for c in comps]
    by_id = {r["id"]: r for r in rows}
    tiers: dict[str, dict] = {}
    for name, meta in visible_tiers(developer_mode=developer_mode).items():
        tier_comps = [c for c in comps if c.tier == name]
        states, blocking = _unit_states(tier_comps, by_id)
        state = _aggregate(states)
        ready_required = [by_id[c.id] for c in tier_comps
                          if c.required and by_id[c.id]["state"] == "READY"]
        tiers[name] = {
            "label": meta["label"], "plain": meta["plain"], "state": state,
            "ready": state == "READY", "blocking": blocking,
            "scientific_use_allowed": (state == "READY"
                                       and all(r["scientific_use_allowed"]
                                               for r in ready_required)),
            "components": [c.id for c in tier_comps],
        }
    return {"schema": "argus-install-health-v1", "developer_mode": developer_mode,
            "components": rows, "tiers": tiers}



def repair_plan(check_result: dict, *, developer_mode: bool = False,
                components: Sequence[Component] | None = None) -> dict:
    """Ordered, plan-only steps for MISSING and DEGRADED components."""
    comps = {c.id: c for c in active_components(components, developer_mode=developer_mode)}
    order = list(visible_tiers(developer_mode=developer_mode))
    rows = [r for r in check_result.get("components", [])
            if r["id"] in comps and r["state"] in ("MISSING", "DEGRADED")]
    rows.sort(key=lambda r: (order.index(r["tier"]) if r["tier"] in order else len(order)))
    skipped_unknown = [r["id"] for r in check_result.get("components", [])
                       if r["id"] in comps and r["state"] == "UNKNOWN"]

    steps: list[dict] = []
    known = 0.0
    unknown_ids: list[str] = []
    for r in rows:
        c = comps[r["id"]]
        source = c.install_plan if r["state"] == "MISSING" else c.repair_plan
        chosen = [s for s in source if developer_mode or not s.get("developer_only")]
        for s in chosen:
            steps.append({"n": len(steps) + 1, "component": c.id, "tier": c.tier,
                          "reason": r["state"], "text": s["text"], "command": s["command"],
                          "downloads": s["downloads"], "installs": s["installs"],
                          "needs_approval": bool(s["needs_approval"]),
                          "needs_admin": c.needs_admin})
        if any(s["downloads"] or s["installs"] for s in chosen):
            if c.approx_disk_gib is None:
                unknown_ids.append(c.id)
            else:
                known += c.approx_disk_gib
    if steps:
        steps.append({"n": len(steps) + 1, "component": None, "tier": None, "reason": None,
                      "text": "Re-run the ARGUS health check to confirm the repair.",
                      "command": None, "downloads": False, "installs": False,
                      "needs_approval": False, "needs_admin": False})
    total = known + UNKNOWN_SIZE_PLANNING_GIB * len(unknown_ids)
    margin = max(REPAIR_DISK_MARGIN_MIN_GIB, REPAIR_DISK_MARGIN_FRACTION * total) if total else 0.0
    return {
        "steps": steps,
        "needs_approval": any(s["needs_approval"] for s in steps),
        "approx_disk_gib": round(total, 2),
        "known_disk_gib": round(known, 2),
        "unknown_size_components": unknown_ids,
        "planning_gib_per_unknown": UNKNOWN_SIZE_PLANNING_GIB,
        "refuses_if_free_disk_below_gib": round(total + margin, 2),
        "skipped_unknown": skipped_unknown,
        "developer_mode": developer_mode,
        "dry_run": True,
    }



DEFAULT_ACTIVATION_MULTIPLIER = 24.0
DEFAULT_MODEL_GIB = 1.0

_GIB = 1024 ** 3


def _voxels(patch_size: int | Sequence[int]) -> int:
    dims = [patch_size] * 3 if isinstance(patch_size, int) else list(patch_size)
    if not dims or any((not isinstance(d, int)) or d <= 0 for d in dims):
        raise ValueError("patch_size must be a positive int or a sequence of positive ints")
    return math.prod(dims)


def vram_estimate_gib(patch_size: int | Sequence[int], batch_size: int, *, channels: int = 1,
                      bytes_per_voxel: int = 4,
                      activation_multiplier: float = DEFAULT_ACTIVATION_MULTIPLIER,
                      model_gib: float = DEFAULT_MODEL_GIB) -> float:
    """activations = voxels * batch * channels * bytes_per_voxel * multiplier; total = that + model."""
    if batch_size <= 0 or channels <= 0 or bytes_per_voxel <= 0:
        raise ValueError("batch_size, channels and bytes_per_voxel must be positive")
    if activation_multiplier < 0 or model_gib < 0:
        raise ValueError("activation_multiplier and model_gib must be non-negative")
    act = _voxels(patch_size) * batch_size * channels * bytes_per_voxel * activation_multiplier
    return act / _GIB + model_gib


def choose_patch_and_batch(vram_budget_gib: float, candidates: Iterable[tuple[Any, int]], *,
                           headroom: float = 0.85, **estimate_kwargs: Any) -> dict | None:
    """Largest (patch, batch) whose estimate fits in headroom * budget, else None."""
    if not 0 < headroom <= 1:
        raise ValueError("headroom must be in (0, 1]")
    usable = vram_budget_gib * headroom
    best = None
    for patch, batch in candidates:
        est = vram_estimate_gib(patch, batch, **estimate_kwargs)
        if est > usable:
            continue
        key = (_voxels(patch) * batch, _voxels(patch), batch)
        if best is None or key > best[0]:
            best = (key, patch, batch, est)
    if best is None:
        return None
    return {"patch_size": best[1], "batch_size": best[2], "estimated_gib": round(best[3], 3),
            "usable_gib": round(usable, 3)}


VRAM_TOLERANCE_GIB = 0.5
_TIER_FLOORS = (("workstation_24gb", 24.0, 64.0), ("mid_12gb", 12.0, 32.0),
                ("consumer_6gb", 6.0, 16.0))


def resource_tier(vram_gib: float | None, host_ram_gib: float | None) -> str:
    """Coarse machine class."""
    if vram_gib is None:
        return "remote_recommended"
    for name, vmin, rmin in _TIER_FLOORS:
        if vram_gib >= vmin - VRAM_TOLERANCE_GIB and (host_ram_gib is None or host_ram_gib >= rmin):
            return name
    return "remote_recommended"


def vram_gib_from_gpus(gpus: dict) -> float | None:
    """Largest card in an `oversight.gpus()` record, in GiB; None when there is no card."""
    mibs = [c.get("vram_total_mib") for c in gpus.get("cards", []) if c.get("vram_total_mib")]
    return round(max(mibs) / 1024, 2) if mibs else None


def free_gib_by_drive(disks: Iterable[dict]) -> dict[str, float | None]:
    """{'C': free, 'T': free} from `oversight.disks()` rows; a missing drive is None."""
    out: dict[str, float | None] = {"C": None, "T": None}
    for d in disks:
        letter = str(d.get("root", ""))[:1].upper()
        if letter in out and d.get("present") and d.get("free_gib") is not None:
            out[letter] = d["free_gib"]
    return out



def enforce_cap_plan(gpu_total_gib: float, cap_fraction: float = 0.9) -> dict:
    """Plan-only text for a torch-level memory cap, so an over-budget job raises OOM instead of silently spilling into system RAM."""
    if gpu_total_gib <= 0 or not 0 < cap_fraction <= 1:
        raise ValueError("gpu_total_gib must be positive and cap_fraction in (0, 1]")
    return {
        "torch_per_process_memory_fraction": cap_fraction,
        "cap_gib": round(gpu_total_gib * cap_fraction, 2),
        "code": "torch.cuda.set_per_process_memory_fraction(%s, device=0)" % cap_fraction,
        "note": ("call once per process before the first CUDA allocation; the allocator then "
                 "raises out-of-memory at the cap rather than letting the driver spill into "
                 "host RAM. The CUDA context and other processes sit outside the cap, so keep "
                 "the fraction below 1."),
        "plan_only": True,
    }


def detect_vram_spill(samples: Sequence[dict], *, near_total_fraction: float = 0.95,
                      host_ram_rise_mib: float = 2048.0, torch_growth_mib: float = 256.0,
                      slowdown_ratio: float = 2.0) -> dict:
    """Look for the fingerprints of driver-side VRAM spill in a time series."""
    ordered = sorted(samples, key=lambda s: s["t"])
    evidence: list[dict] = []
    notes: list[str] = []
    if not ordered:
        return {"spill_suspected": False, "evidence": [], "notes": ["no samples"], "samples": 0}

    over = [s for s in ordered if s.get("torch_allocated_mib") is not None
            and s["torch_allocated_mib"] > s["gpu_total_mib"]]
    if over:
        first = over[0]
        evidence.append({"rule": "torch_allocated_exceeds_gpu_total", "t": first["t"],
                         "torch_allocated_mib": first["torch_allocated_mib"],
                         "gpu_total_mib": first["gpu_total_mib"], "samples": len(over)})

    def near(s: dict) -> bool:
        return s["gpu_used_mib"] >= near_total_fraction * s["gpu_total_mib"]

    pinned = [s for s in ordered if near(s)]
    if len(pinned) >= 2:
        rise = pinned[-1]["host_ram_used_mib"] - pinned[0]["host_ram_used_mib"]
        tvals = [s["torch_allocated_mib"] for s in pinned if s.get("torch_allocated_mib") is not None]
        if len(tvals) < 2:
            notes.append("host-RAM rule not evaluated: torch_allocated_mib not provided while "
                         "the GPU was near total")
        elif rise > host_ram_rise_mib and tvals[-1] - tvals[0] > torch_growth_mib:
            evidence.append({"rule": "host_ram_rising_while_gpu_pinned",
                             "host_ram_rise_mib": rise,
                             "torch_growth_mib": tvals[-1] - tvals[0],
                             "t_start": pinned[0]["t"], "t_end": pinned[-1]["t"]})

    timed = [s for s in ordered if s.get("step_s") is not None]
    if len(timed) >= 4:
        k = min(3, len(timed) // 2)
        base = statistics.median(s["step_s"] for s in timed[:k])
        recent_rows = timed[-k:]
        recent = statistics.median(s["step_s"] for s in recent_rows)
        if base > 0 and recent / base >= slowdown_ratio:
            ratio = round(recent / base, 2)
            if all(near(s) for s in recent_rows):
                evidence.append({"rule": "step_time_slowdown_at_full_vram", "ratio": ratio,
                                 "baseline_step_s": base, "recent_step_s": recent})
            else:
                notes.append("steps slowed %sx but VRAM was not near total; not attributed to "
                             "spill" % ratio)
    return {"spill_suspected": bool(evidence), "evidence": evidence, "notes": notes,
            "samples": len(ordered)}



def _parse_utc(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def eviction_plan(entries: Iterable[dict], *, budget_gib: float,
                  protect: Iterable[str] = ()) -> dict:
    """A dry-run plan to bring a cache under budget."""
    if budget_gib < 0:
        raise ValueError("budget_gib must be non-negative")
    protect_keys = set(protect)
    budget = int(budget_gib * _GIB)
    total = 0
    protected: list[dict] = []
    evictable: list[tuple[float, str, dict]] = []
    for e in entries:
        size = int(e["bytes"])
        if size < 0:
            raise ValueError("entry %r has negative bytes" % (e.get("key"),))
        total += size
        reasons = [name for name, on in (("pinned", e.get("pinned")), ("in_use", e.get("in_use")),
                                         ("held_by_run", e.get("held_by_run")),
                                         ("protected", e["key"] in protect_keys)) if on]
        if reasons:
            protected.append({"key": e["key"], "bytes": size, "reasons": reasons})
            continue
        ts = _parse_utc(e.get("last_used_utc"))
        evictable.append((math.inf if ts is None else ts, str(e["key"]),
                          {"key": e["key"], "bytes": size,
                           "last_used_utc": e.get("last_used_utc"),
                           "last_used_known": ts is not None}))
    evictable.sort(key=lambda t: (t[0], t[1]))

    to_free = max(0, total - budget)
    evict: list[dict] = []
    freed = 0
    for _, _, row in evictable:
        if freed >= to_free:
            break
        evict.append({**row, "reason": "lru"})
        freed += row["bytes"]
    reclaimable = sum(r["bytes"] for _, _, r in evictable)
    protected_bytes = sum(p["bytes"] for p in protected)
    shortfall = max(0, protected_bytes - budget)
    after = total - freed
    if shortfall:
        note = ("protected entries alone exceed the budget by %d bytes; none of them is "
                "listed for eviction" % shortfall)
    elif to_free == 0:
        note = "already within budget; nothing to evict"
    else:
        note = "LRU eviction of unprotected entries reaches the budget"
    return {"dry_run": True, "budget_bytes": budget, "total_bytes": total,
            "bytes_to_free": to_free, "evict": evict, "planned_evict_bytes": freed,
            "bytes_after_plan": after, "reclaimable_bytes": reclaimable,
            "protected": protected, "protected_bytes": protected_bytes,
            "shortfall_bytes": shortfall, "budget_reachable": after <= budget, "note": note}


def low_disk_guard(free_gib_c: float | None, free_gib_t: float | None, *, floor_c: float = storage_policy.C_FLOOR_GIB,
                   floor_t: float = storage_policy.T_FLOOR_GIB, planned_write_gib_c: float = 0,
                   planned_write_gib_t: float = 0) -> dict:
    """Refuse a write plan that would leave either drive below its floor."""
    if planned_write_gib_c < 0 or planned_write_gib_t < 0:
        raise ValueError("planned writes must be non-negative")
    reasons: list[str] = []
    after: dict[str, float | None] = {}
    for label, free, planned, floor in (("C", free_gib_c, planned_write_gib_c, floor_c),
                                        ("T", free_gib_t, planned_write_gib_t, floor_t)):
        if free is None:
            after[label] = None
            reasons.append("%s: free space unknown" % label)
            continue
        after[label] = round(free - planned, 2)
        if free - planned < floor:
            reasons.append("%s: %.1f GiB free after a %.1f GiB write is below the %.1f GiB floor"
                           % (label, free - planned, planned, floor))
    return {"allowed": not reasons, "reasons": reasons, "after_free_gib": after,
            "floors_gib": {"C": floor_c, "T": floor_t}}


def low_ram_guard(percent_used: float | None, *, stop_above: float = 90) -> dict:
    """Stop when host RAM use is above the limit (exactly at it is allowed)."""
    if percent_used is None:
        return {"allowed": False, "reasons": ["RAM use unknown"], "percent_used": None,
                "stop_above": stop_above}
    if percent_used > stop_above:
        return {"allowed": False, "percent_used": percent_used, "stop_above": stop_above,
                "reasons": ["RAM use %.1f%% is above the %.1f%% limit" % (percent_used, stop_above)]}
    return {"allowed": True, "reasons": [], "percent_used": percent_used, "stop_above": stop_above}



def wsl_status(runner: Runner) -> dict:
    """Whether this process runs under WSL, from the injected `platform` and `proc_version` probes."""
    def probe(kind: str) -> str:
        try:
            res = runner(kind, {"kind": kind})
        except Exception:
            return ""
        return str(res.get("detail") or "") if isinstance(res, dict) and res.get("ok") else ""

    plat = probe("platform")
    proc = probe("proc_version")
    lowered = proc.lower()
    is_wsl = "microsoft" in lowered or "wsl" in lowered
    host = ("windows" if plat.lower().startswith("windows") else
            "linux" if plat.lower().startswith("linux") else
            "macos" if plat.lower().startswith(("darwin", "macos")) else "unknown")
    if is_wsl:
        note = ("running under WSL; the Windows GPU driver can spill CUDA allocations into "
                "system RAM instead of raising out-of-memory, so set the torch memory cap")
    elif host == "windows":
        note = ("native Windows; Lasagna and spiral-fitting expect Linux or WSL, and the same "
                "driver-side spill applies to any CUDA work, so set the torch memory cap")
    else:
        note = "not WSL"
    return {"is_wsl": is_wsl, "wsl2": is_wsl and "wsl2" in lowered, "host_os": host,
            "platform": plat or None, "note": note}



def default_runner(kind: str, spec: dict) -> dict:
    """The real prober."""
    import importlib.util
    import platform as _platform
    import shutil
    from pathlib import Path

    try:
        if kind == "python_import":
            found = importlib.util.find_spec(spec["module"]) is not None
            return {"ok": found, "detail": "importable" if found else "not importable",
                    "digest": None}
        if kind == "binary_on_path":
            for name in spec["names"]:
                path = shutil.which(name)
                if path:
                    return {"ok": True, "detail": path, "digest": None}
            return {"ok": False, "detail": "not on PATH", "digest": None}
        if kind == "file_exists":
            ok = Path(spec["path"]).exists()
            return {"ok": ok, "detail": spec["path"] if ok else "no such file", "digest": None}
        if kind == "docker_image":
            if shutil.which("docker") is None:
                return {"ok": False, "detail": "docker is not on PATH", "digest": None}
            from argus.core import oversight

            ok, out = oversight._run(["docker", "image", "inspect", "--format",
                                      "{{range .RepoDigests}}{{println .}}{{end}}",
                                      spec["reference"]])
            digest = None
            for line in out.splitlines():
                if "@sha256:" in line:
                    digest = line.split("@", 1)[1].strip()
                    break
            return {"ok": ok, "detail": "image present" if ok else out, "digest": digest}
        if kind == "http_health":
            import urllib.request

            if not str(spec["url"]).startswith(("http://", "https://")):
                return {"ok": False, "detail": "unsupported url scheme", "digest": None}
            with urllib.request.urlopen(spec["url"], timeout=5) as r:
                return {"ok": 200 <= r.status < 300, "detail": "HTTP %d" % r.status,
                        "digest": None}
        if kind == "platform":
            return {"ok": True, "detail": _platform.platform(), "digest": None}
        if kind == "proc_version":
            p = Path("/proc/version")
            if not p.is_file():
                return {"ok": False, "detail": "", "digest": None}
            return {"ok": True, "detail": p.read_text(encoding="utf-8", errors="replace")[:400],
                    "digest": None}
    except Exception as e:
        return {"ok": None, "detail": "%s: %s" % (type(e).__name__, str(e)[:120]),
                "digest": None}
    return {"ok": None, "detail": "unknown probe kind %r" % kind, "digest": None}
