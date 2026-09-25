"""`argus doctor` -- what is wrong, in plain language, with the command that fixes it."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

from argus.core import paths, side_permit
from argus.cli import acquire as A
from argus.cli import hardware, manifest, state

PASS, FAIL, UNKNOWN, INFO = "PASS", "FAIL", "UNKNOWN", "INFO"


def _c(name: str, status: str, means: str, fix: str = "", verified=None) -> dict:
    """One check."""
    return {"check": name, "status": status, "means": means, "fix": fix,
            "fix_verified": verified}



def check_python() -> list:
    v = sys.version_info
    ok = v >= (3, 11)
    return [_c("python", PASS if ok else FAIL,
               "this interpreter is %d.%d.%d; ARGUS needs 3.11 or newer"
               % (v.major, v.minor, v.micro),
               "" if ok else "install Python 3.11+ and recreate the virtual environment",
               True)]


def check_path_contract() -> list:
    """Every declared root, and whether it exists."""
    out = []
    for r in paths.describe():
        src = "environment" if r.from_environment else "default"
        out.append(_c("root:%s" % r.var, PASS if r.exists else FAIL,
                      "%s (%s) holds %s" % (r.path, src, r.purpose),
                      "" if r.exists else
                      "create it, or point %s at the real location: set %s=<path>"
                      % (r.var, r.var), True))
    return out


def check_write_root() -> list:
    """Receipts have ONE canonical destination and it must actually accept a write."""
    root = paths.artifact_write_root()
    probe = pathlib.Path(root) / "_argus_cli" / "doctor_write_probe.tmp"
    try:
        paths.assert_writable(probe)
    except paths.WriteRootRefusal as exc:
        return [_c("write-root", FAIL,
                   "the canonical receipt root is refused as a write target: %s" % exc,
                   "unset ARGUS_REPO overrides, or point it at a writable checkout", True)]
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return [_c("write-root", FAIL,
                   "%s exists but cannot be written: %s" % (root, exc),
                   "check permissions on %s, or set ARGUS_REPO to a writable checkout"
                   % root, True)]
    return [_c("write-root", PASS, "receipts can be written to %s" % root, "", None)]


def check_gpu(measured: dict) -> list:
    g = measured["gpu"]
    if g["status"] == UNKNOWN:
        return [_c("gpu", UNKNOWN,
                   "could not determine whether this machine has a usable GPU: %s" % g["why"],
                   g["fix"] or "", False),
                _c("fp16-nan-trap", UNKNOWN,
                   "the fp16 NaN trap depends on the card's compute capability, which is "
                   "unknown while the GPU is unknown. This is NOT a pass: on a pre-Ampere "
                   "card the trap produces an ink map that is entirely empty and reads as a "
                   "confident negative.",
                   "run `nvidia-smi --query-gpu=name,compute_cap --format=csv` and, if the "
                   "capability is below 8.0, run everything with mixed precision off", True)]
    if g["status"] == "ABSENT":
        return [_c("gpu", INFO,
                   "no GPU device was reported. setup, doctor and demo are fully supported "
                   "on CPU; ink inference is not, and the CPU profile says so rather than "
                   "offering a run that never finishes.",
                   "no action needed unless you intend to run inference here", None),
                _c("fp16-nan-trap", INFO, "not applicable without a GPU", "", None)]
    rows = []
    for d in g["devices"]:
        vram = ("%d MB" % d["vram_mb"]) if d["vram_mb"] else "UNKNOWN"
        rows.append(_c("gpu", PASS, "%s with %s VRAM" % (d["name"], vram), "", None))
        cc = d["compute_capability"]
        if cc is None:
            rows.append(_c("fp16-nan-trap", UNKNOWN,
                           "this driver does not report compute capability (%s), so whether "
                           "fp16 autocast is safe on this card is unknown -- and unknown is "
                           "treated as unsafe, because the failure looks like a result."
                           % d["compute_capability_why_unknown"],
                           "upgrade the driver, or run with mixed precision off", True))
        elif cc < hardware.TENSOR_CORE_CC:
            rows.append(_c("fp16-nan-trap", FAIL,
                           "compute %.1f is pre-Ampere and has no tensor cores. fp16 "
                           "autocast produces NaN here, NaN casts to 0 in the uint8 write "
                           "with only a RuntimeWarning, and your ink map will be EMPTY while "
                           "looking like a confident negative." % cc,
                           "run with mixed precision disabled (the gpu_6gb profile sets "
                           "amp=False for exactly this reason).", True))
        else:
            rows.append(_c("fp16-nan-trap", PASS,
                           "compute %.1f has tensor cores; fp16 autocast is safe here" % cc,
                           "", None))
    from argus.core import gpu_budget
    cap = gpu_budget.doctor_row()
    rows.append(_c("gpu-memory-cap", UNKNOWN,
                   "ARGUS GPU paths cap the allocator at %s GiB, so an oversized ARGUS run fails "
                   "with an out-of-memory error instead of spilling into system RAM. Other programs "
                   "on this machine are protected only by the driver policy, which cannot be read here."
                   % (cap["default_cap_gib"] if cap["default_cap_gib"] is not None else "card-minus-0.5"),
                   "NVIDIA Control Panel > Manage 3D settings > Global Settings > CUDA - Sysmem "
                   "Fallback Policy = Prefer No Sysmem Fallback", True))
    return rows


def check_resources(measured: dict) -> list:
    out = []
    ram = measured["free_ram_gib"]
    if ram is None:
        out.append(_c("ram", UNKNOWN, "available memory could not be read: %s"
                      % measured["free_ram_why_unknown"],
                      "report the platform; the CLI reads memory without shelling out and "
                      "has no path for this OS yet", False))
    else:
        ok = ram >= hardware.FLOOR_FREE_RAM_GIB
        out.append(_c("ram", PASS if ok else FAIL,
                      "%.1f GiB free against a %.1f GiB floor"
                      % (ram, hardware.FLOOR_FREE_RAM_GIB),
                      "" if ok else "close other work, or run the cpu_only profile", True))
    for d in measured["disks"]:
        if d["free_gib"] is None:
            out.append(_c("disk:%s" % d["volume"], UNKNOWN,
                          "free space could not be read: %s" % d["why_unknown"], "", False))
            continue
        ok = d["free_gib"] >= hardware.FLOOR_FREE_DISK_GIB
        out.append(_c("disk:%s" % d["volume"], PASS if ok else FAIL,
                      "%.1f GiB free of %.1f GiB, against a %.1f GiB floor for a real run"
                      % (d["free_gib"], d["total_gib"], hardware.FLOOR_FREE_DISK_GIB),
                      "" if ok else "free space on %s before starting a run; a half-finished "
                                    "fetch is worse than no fetch" % d["volume"], True))
    cpu = measured["cpu_count"]
    out.append(_c("cpu", PASS if cpu else UNKNOWN,
                  "%s logical cores" % (cpu or "UNKNOWN -- os.cpu_count() returned None"),
                  "", None))
    return out


def check_permit() -> list:
    """The frozen scoring run owns the card."""
    st = side_permit.permit_state()
    if st.get("allow"):
        return [_c("side-work-permit", PASS,
                   "side work is permitted (%s)" % st.get("reason"), "", None)]
    return [_c("side-work-permit", INFO,
               "side work is currently suspended: %s -- %s. This is the governor protecting "
               "a frozen scoring run, and a denial is also what a MISSING governor produces, "
               "on purpose: a dead governor must not hand every lane a green light."
               % (st.get("reason"), st.get("detail")),
               "wait; `argus setup` and `argus run` wait for the permit by themselves", None)]


def check_dependencies() -> list:
    """Importable or not, established WITHOUT importing."""
    need = {"numpy": "numpy", "zarr": "zarr", "fsspec": "fsspec", "s3fs": "s3fs"}
    missing = [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]
    out = []
    if missing:
        out.append(_c("python-deps", FAIL, "missing: %s" % ", ".join(missing),
                      "uv pip install %s   (or: uv pip install -e .[volume])"
                      % " ".join(missing), True))
    else:
        out.append(_c("python-deps", PASS, "all %d volume-reading imports are installed"
                      % len(need), "", None))
    torch_there = importlib.util.find_spec("torch") is not None
    out.append(_c("torch", PASS if torch_there else INFO,
                  "torch is installed" if torch_there else
                  "torch is not installed. setup, doctor and demo do not need it; ink "
                  "inference does.",
                  "" if torch_there else "uv pip install -e .[surface-model]", True))
    return out


def check_components(root, components) -> list:
    accepted = state.load_acceptance(root)
    out = []
    for c in components:
        if c.id not in accepted:
            out.append(_c("component:%s" % c.id, FAIL,
                          "%s: its licence (%s) has not been accepted on this machine, so "
                          "nothing has been fetched" % (c.name, c.spdx),
                          "argus setup --accept %s   (read %s first)"
                          % (c.id, c.licence_url), True))
            continue
        if not c.is_verifiable:
            out.append(_c("component:%s" % c.id, UNKNOWN,
                          "%s cannot be verified by this project: integrity mode %s -- %s"
                          % (c.name, c.integrity_mode, c.integrity_why_unknown),
                          "record a real content address in argus/cli/components.json. Do "
                          "not fill the field in with a value you have not verified.", True))
            continue
        st = A.state(c, root)
        if st.get("verified"):
            note = ""
            if st.get("partial_bytes"):
                note = (" (a %d-byte .part is also present and is NOT counted as progress)"
                        % st["partial_bytes"])
            out.append(_c("component:%s" % c.id, PASS,
                          "held and digest-verified%s" % note, "", None))
        elif st.get("partial_bytes"):
            out.append(_c("component:%s" % c.id, FAIL,
                          "only a partial transfer is present (%d bytes). A partial file is "
                          "not a component: it is resumed, never used."
                          % st["partial_bytes"],
                          "argus setup   (it resumes from where it stopped)", True))
        else:
            out.append(_c("component:%s" % c.id, FAIL, "not acquired",
                          "argus setup", True))
    return out


def check_upstream_pin() -> list:
    """The pinned villa checkout, and whether the tree on disk is that pin."""
    lock = pathlib.Path(paths.repo("argus", "upstream.lock.json"))
    if not lock.is_file():
        return [_c("upstream-pin", UNKNOWN, "no upstream lock file at %s" % lock,
                   "restore argus/upstream.lock.json from the repository", False)]
    try:
        doc = json.loads(lock.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [_c("upstream-pin", FAIL, "the upstream lock is not valid JSON: %s" % exc,
                   "restore argus/upstream.lock.json from the repository", True)]
    pin = (doc.get("upstream") or {}).get("commit")
    tree = pathlib.Path(paths.upstream("villa"))
    if not tree.is_dir():
        return [_c("upstream-pin", FAIL,
                   "villa is pinned at %s but no checkout exists at %s" % (pin[:12], tree),
                   "argus setup   (it prints the clone command; villa is GPL-3.0 and is run "
                   "as an external subprocess, never linked in)", True)]
    git = hardware.which_on_path("git")
    if not git:
        return [_c("upstream-pin", UNKNOWN,
                   "a villa checkout exists at %s but git is not on PATH, so whether it is "
                   "the pinned commit cannot be established" % tree,
                   "install git, then re-run argus doctor", True)]
    try:
        r = subprocess.run([git, "-C", str(tree), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return [_c("upstream-pin", UNKNOWN, "git could not read the checkout: %s" % exc,
                   "run git -C %s rev-parse HEAD by hand" % tree, False)]
    head = (r.stdout or "").strip()
    if r.returncode != 0 or not head:
        return [_c("upstream-pin", UNKNOWN,
                   "git could not resolve HEAD in %s: %s" % (tree, (r.stderr or "")[:120]),
                   "check the checkout is a git repository", False)]
    if head != pin:
        return [_c("upstream-pin", FAIL,
                   "the villa checkout is at %s but the lock pins %s. A different upstream "
                   "is a different sampler, and results from the two are not comparable."
                   % (head[:12], (pin or "")[:12]),
                   "git -C %s fetch --depth 1 origin %s && git -C %s checkout %s"
                   % (tree, pin, tree, pin), True)]
    return [_c("upstream-pin", PASS, "villa is checked out at the pinned commit %s"
               % pin[:12], "", None)]


def check_capability_route() -> list:
    """Where the science chain is blocked."""
    try:
        from argus.core import capability_graph as CG
        rec = CG.as_record(paths.artifact_write_root())
    except Exception as exc:
        return [_c("capability-route", UNKNOWN,
                   "the capability graph could not be derived: %s: %s"
                   % (type(exc).__name__, str(exc)[:160]),
                   "run python -m argus.core.capability_graph for the full error", False)]
    route = rec.get("route") or {}
    blocked = route.get("first_blocked_stage")
    counts = rec.get("counts") or {}
    rows = [_c("capability-installed", INFO,
               "%s of %s capabilities installed, %s control-passed"
               % (counts.get("installed"), counts.get("total"),
                  counts.get("control_passed")), "", None)]
    if blocked is None:
        rows.append(_c("capability-route", PASS,
                       "every stage of the chain can be produced or is already held",
                       "", None))
    else:
        rows.append(_c("capability-route", FAIL,
                       "the chain is blocked at the %s stage. Product readiness and "
                       "scientific readiness are separate: a green route can still be "
                       "scientifically inadmissible, and this is not." % blocked,
                       "argus setup, then read the evidence rows in "
                       "argus.core.capability_graph.as_record()", True))
    rows.append(_c("scientific-admissibility", INFO,
                   "%s of %s scientific capabilities are admissible. This build makes no scientific "
                   "claim, so no output may be presented as a reading."
                   % (counts.get("scientifically_admissible"),
                      counts.get("scientific_capabilities")), "", None))
    return rows


def check_deep(enabled: bool) -> list:
    """The historical torch-level doctor."""
    if not enabled:
        return [_c("deep-gpu-probe", UNKNOWN,
                   "the torch-level GPU checks were not run. They import torch, which "
                   "creates a CUDA context -- unwelcome on a machine where a frozen scoring "
                   "run owns the card. Everything above was answered from the driver's own "
                   "query tool instead.",
                   "argus doctor --deep   (only when the GPU is free)", True)]
    script = pathlib.Path(paths.repo("scripts", "vesuvius_doctor.py"))
    if not script.is_file():
        return [_c("deep-gpu-probe", UNKNOWN, "the deep doctor script is not present at %s"
                   % script, "", False)]
    try:
        r = subprocess.run([sys.executable, str(script), "--json", "--skip-net"],
                           capture_output=True, text=True, timeout=600,
                           cwd=str(paths.repo()))
        rows = json.loads(r.stdout)
    except Exception as exc:
        return [_c("deep-gpu-probe", UNKNOWN, "the deep doctor did not complete: %s: %s"
                   % (type(exc).__name__, str(exc)[:160]),
                   "run python scripts/vesuvius_doctor.py by hand", False)]
    status = {"ok": PASS, "fail": FAIL, "warn": UNKNOWN, "info": INFO}
    return [_c("deep:%s" % row.get("check"), status.get(row.get("status"), UNKNOWN),
               row.get("detail", ""), row.get("fix", ""), row.get("fix_verified"))
            for row in (rows if isinstance(rows, list) else [])]



def collect(*, deep: bool = False, manifest_path=None, root=None) -> list:
    measured = hardware.probe()
    rows = check_python() + check_path_contract() + check_write_root()
    rows += check_gpu(measured) + check_resources(measured) + check_permit()
    rows += check_dependencies()
    try:
        components = manifest.load(manifest_path)
        rows += check_components(root or state.install_root(), components)
    except manifest.ManifestError as exc:
        rows.append(_c("component-manifest", FAIL,
                       "the component manifest did not load: %s" % exc,
                       "correct argus/cli/components.json", True))
    rows += check_upstream_pin() + check_capability_route() + check_deep(deep)
    return rows


def render(rows, out=sys.stdout) -> int:
    icon = {PASS: " pass ", FAIL: " FAIL ", UNKNOWN: "UNKNOWN", INFO: " info "}
    print("=" * 78, file=out)
    print("  ARGUS DOCTOR -- a check that could not run says UNKNOWN, never pass", file=out)
    print("=" * 78, file=out)
    for r in rows:
        print("[%s] %-28s %s" % (icon[r["status"]], r["check"], r["means"]), file=out)
        if r["fix"]:
            tag = ("" if r["fix_verified"] is None
                   else "" if r["fix_verified"] else "  (UNVERIFIED here)")
            print("            fix: %s%s" % (r["fix"], tag), file=out)
    n_fail = sum(1 for r in rows if r["status"] == FAIL)
    n_unk = sum(1 for r in rows if r["status"] == UNKNOWN)
    print("-" * 78, file=out)
    print("  %d checks: %d pass, %d FAIL, %d UNKNOWN"
          % (len(rows), sum(1 for r in rows if r["status"] == PASS), n_fail, n_unk), file=out)
    if n_unk:
        print("  UNKNOWN is not a pass. Each one above says what stopped it.", file=out)
    return 1 if n_fail else 0


def run(argv, out=sys.stdout) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="argus doctor")
    ap.add_argument("--json", action="store_true", help="machine-readable, for CI")
    ap.add_argument("--deep", action="store_true",
                    help="also run the torch-level GPU checks. These import torch and create "
                         "a CUDA context -- only use this when the card is free")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--root", default=None)
    ns = ap.parse_args(argv)
    rows = collect(deep=ns.deep, manifest_path=ns.manifest, root=ns.root)
    if ns.json:
        print(json.dumps({"schema": "argus-cli-doctor-v1", "checks": rows}, indent=1),
              file=out)
        return 1 if any(r["status"] == FAIL for r in rows) else 0
    return render(rows, out)
