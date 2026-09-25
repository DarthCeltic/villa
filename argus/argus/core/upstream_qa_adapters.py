"""Adapters that run third-party geometry-QA tools AT A VERIFIED PIN, or say exactly why they cannot."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from argus.core import paths
from argus.core import surface_geometry_qa as QA
from argus.core import upstream_sources as US

DEFAULT_MAX_VOLUME_BYTES = 512 * 1024 * 1024
DEFAULT_TIMEOUT_S = 900
LABELSCOPE_CAVEAT = (
    "labelscope's own source: the 0.5 / 0.3 verdict cuts are calibrated on PHercParis4 at 2.4 um and are a reading aid for one scan, "
    "not a transferable threshold; a verdict is only meaningful against a baseline measured in the SAME volume"
)


def _source(source_id: str) -> dict:
    for row in US.UPSTREAM_SOURCES:
        if row["id"] == source_id:
            return row
    raise KeyError(source_id)


def _git(checkout: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "git failed")
    return out.stdout.strip()


def license_gate(source_id: str, *, environ: dict | None = None) -> dict:
    env = os.environ if environ is None else environ
    status = _source(source_id)["license_status"]
    if status.startswith("MIT_VERIFIED") or status.startswith("INHERITS"):
        return {"allowed": True, "license_status": status}
    ack = {x.strip() for x in str(env.get("ARGUS_ALLOW_UNLICENSED_UPSTREAM", "")).split(",") if x.strip()}
    if source_id in ack:
        return {"allowed": True, "license_status": status, "acknowledged_by_operator": True,
                "note": "local execution only; the code is not vendored, packaged or redistributed"}
    return {"allowed": False, "license_status": status,
            "prerequisite": "set ARGUS_ALLOW_UNLICENSED_UPSTREAM=%s to acknowledge that this tool declares no licence (local execution only)" % source_id}


def resolve_pinned_checkout(source_id: str, *, root: Path | None = None) -> dict:
    """AVAILABLE only if the tool's checkout exists, is at the pinned commit and has no local modification."""
    src = _source(source_id)
    pin = src.get("pin")
    base = Path(root) if root else paths.upstream(source_id)
    result = {"source_id": source_id, "pin": pin, "checkout": str(base)}
    if not pin or len(pin) != 40:
        return {**result, "status": "UNAVAILABLE", "reason_code": "NO_FULL_PIN", "prerequisite": "a full 40-character commit pin in argus.core.upstream_sources"}
    if not base.is_dir():
        return {**result, "status": "UNAVAILABLE", "reason_code": "CHECKOUT_ABSENT",
                "prerequisite": "a checkout of %s at %s under %s (nothing is fetched by ARGUS)" % (src["url"], pin, base)}
    try:
        top = Path(_git(base, "rev-parse", "--show-toplevel")).resolve()
        if top != base.resolve():
            return {**result, "status": "UNAVAILABLE", "reason_code": "NOT_A_CHECKOUT_ROOT", "toplevel": str(top),
                    "prerequisite": "the checkout's own root (%s is inside a repository at %s)" % (base, top)}
        head = _git(base, "rev-parse", "HEAD")
        dirty = _git(base, "status", "--porcelain", "--untracked-files=all")
    except (RuntimeError, OSError) as exc:
        return {**result, "status": "UNAVAILABLE", "reason_code": "NOT_A_GIT_CHECKOUT", "prerequisite": "a git checkout (%s)" % exc}
    if head != pin:
        return {**result, "status": "UNAVAILABLE", "reason_code": "PIN_MISMATCH", "head": head,
                "prerequisite": "check out %s (found %s); a moving branch tip is not the pin" % (pin, head)}
    if dirty:
        return {**result, "status": "UNAVAILABLE", "reason_code": "CHECKOUT_MODIFIED", "head": head, "modified": dirty.splitlines()[:5],
                "prerequisite": "an unmodified checkout at the pin"}
    return {**result, "status": "AVAILABLE", "head": head}


def _refusal(check: str, reason_code: str, reason: str, prerequisite: str, source_id: str, extra: dict | None = None) -> QA.GeometryQAResult:
    return QA.GeometryQAResult(
        check, QA.UNAVAILABLE, {"reason_code": reason_code, "prerequisite": prerequisite, **(extra or {})},
        {"provider": source_id, "pin": _source(source_id).get("pin"), "pin_status": _source(source_id).get("pin_status")},
        reason, QA.PROVENANCE_UPSTREAM, "none: no threshold was applied")


def vc_segqa(*, checkout_root: Path | None = None, environ: dict | None = None) -> QA.GeometryQAResult:
    """vc-segqa declares no licence and is a set of scripts that read the open-data bucket over HTTP: never run implicitly."""
    gate = license_gate("vc-segqa", environ=environ)
    if not gate["allowed"]:
        return _refusal("vc_segqa_mask_escape", "LICENSE_NOT_DECLARED", "vc-segqa declares no licence", gate["prerequisite"], "vc-segqa")
    co = resolve_pinned_checkout("vc-segqa", root=checkout_root)
    if co["status"] != "AVAILABLE":
        return _refusal("vc_segqa_mask_escape", co["reason_code"], "no verified checkout of vc-segqa", co["prerequisite"], "vc-segqa", {"checkout": co})
    return _refusal("vc_segqa_mask_escape", "NO_OFFLINE_ENTRY_POINT",
                    "vc-segqa's scan reads the open-data bucket over HTTP; it has no local-volume entry point ARGUS may call without network",
                    "an offline entry point that reads a local, hash-identified volume (none exists at this pin)", "vc-segqa", {"checkout": co})


def labelscope_onsheet(mesh_dirs: list, volume_tiff: str, baseline_mesh: str, *, checkout_root: Path | None = None, python: str | None = None,
                       max_volume_bytes: int = DEFAULT_MAX_VOLUME_BYTES, timeout_s: int = DEFAULT_TIMEOUT_S, blocks: int = 6, block_size: int = 12,
                       reach: float = 70.0, step: float = 1.0, seed: int = 0, environ: dict | None = None) -> QA.GeometryQAResult:
    """Run `labelscope onsheet` (local mode) at the pinned commit on tifxyz directories against ONE local TIFF volume and a same-volume baseline."""
    check = "labelscope_onsheet"
    gate = license_gate("labelscope", environ=environ)
    if not gate["allowed"]:
        return _refusal(check, "LICENSE_GATE", "labelscope licence gate refused", gate["prerequisite"], "labelscope")
    co = resolve_pinned_checkout("labelscope", root=checkout_root)
    if co["status"] != "AVAILABLE":
        return _refusal(check, co["reason_code"], "no verified checkout of labelscope", co["prerequisite"], "labelscope", {"checkout": co})
    if not baseline_mesh:
        return _refusal(check, "BASELINE_REQUIRED", "labelscope's verdict is only meaningful against a baseline measured in the same volume",
                        "a published surface from the same scan, passed as baseline_mesh", "labelscope", {"checkout": co})
    vol = Path(volume_tiff)
    if not vol.is_file():
        return _refusal(check, "VOLUME_ABSENT", "the volume file is not present", "a local TIFF of the scan the surfaces were traced on", "labelscope", {"checkout": co})
    if vol.stat().st_size > max_volume_bytes:
        return _refusal(check, "VOLUME_TOO_LARGE",
                        "labelscope's local mode loads the whole volume into memory; %d bytes exceeds the %d byte bound" % (vol.stat().st_size, max_volume_bytes),
                        "a crop of the scan under the bound (or a larger explicit bound)", "labelscope", {"checkout": co})
    interp = python or str(paths.tools("labelscope-venv", "Scripts", "python.exe"))
    if not Path(interp).is_file() and python is None and not os.environ.get("ARGUS_LABELSCOPE_PYTHON"):
        return _refusal(check, "INTERPRETER_ABSENT", "no interpreter with labelscope's dependencies",
                        "a Python with numpy, scipy, scikit-image, tifffile, imagecodecs, requests and pillow (ARGUS_LABELSCOPE_PYTHON or python=)", "labelscope", {"checkout": co})
    interp = python or os.environ.get("ARGUS_LABELSCOPE_PYTHON") or interp
    with tempfile.TemporaryDirectory(prefix="argus_labelscope_") as tmp:
        out_json = Path(tmp) / "onsheet.json"
        code = "import sys; from labelscope.cli import main; sys.exit(main(sys.argv[1:]))"
        argv = [interp, "-c", code, "onsheet", "--mesh", *map(str, mesh_dirs), "--volume", str(vol), "--baseline", str(baseline_mesh),
                "--blocks", str(blocks), "--block-size", str(block_size), "--reach", str(reach), "--step", str(step), "--seed", str(seed), "--out", str(out_json)]
        env = {**os.environ, "PYTHONPATH": str(Path(co["checkout"]) / "src"), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPYCACHEPREFIX": str(Path(tmp) / "pycache")}
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, env=env, cwd=tmp)
        except subprocess.TimeoutExpired:
            return _refusal(check, "TIMEOUT", "labelscope did not finish within %d s" % timeout_s, "a smaller crop or a larger explicit timeout", "labelscope", {"checkout": co})
        if proc.returncode != 0 or not out_json.is_file():
            return _refusal(check, "TOOL_FAILED", "labelscope exited with %d" % proc.returncode, "see the tool's stderr", "labelscope",
                            {"checkout": co, "stderr_tail": proc.stderr[-600:]})
        payload = json.loads(out_json.read_text(encoding="utf-8"))
    rows = []
    for row in payload.get("results", []):
        rows.append({k: row.get(k) for k in ("mesh", "blocks", "columns", "range_p10", "range_median", "range_p90", "peak_offset_abs_median", "baseline", "error") if k in row})
    labelled = []
    base = next((r for r in payload.get("results", []) if r.get("baseline") and "error" not in r), None)
    if base is not None:
        relay = ("import json, sys; from labelscope.onsheet import verdict; d = json.load(sys.stdin); b = d['base']; "
                 "print(json.dumps([{'mesh': r['mesh'], 'fraction_of_baseline': verdict(r['range_median'], b)[1], 'upstream_label': verdict(r['range_median'], b)[0]} "
                 "for r in d['rows']]))")
        rows_in = [r for r in payload["results"] if not r.get("baseline") and "error" not in r]
        with tempfile.TemporaryDirectory() as relay_cwd:
            proc2 = subprocess.run([interp, "-c", relay], input=json.dumps({"base": base["range_median"], "rows": rows_in}), capture_output=True, text=True, timeout=120, env=env, cwd=relay_cwd)
        if proc2.returncode == 0:
            labelled = json.loads(proc2.stdout)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return QA.GeometryQAResult(
        check, QA.MEASURED,
        {"rows": rows, "against_baseline": labelled, "upstream_output_sha256": digest, "interpreter": interp, "volume_bytes": vol.stat().st_size},
        {"provider": "labelscope", "pin": co["head"], "pin_status": _source("labelscope")["pin_status"], "license_status": gate["license_status"]},
        LABELSCOPE_CAVEAT, QA.PROVENANCE_UPSTREAM, "none applied by ARGUS; the tool's own reading-aid label is relayed as upstream_label")
