"""The allowlist itself: name -> callable, and nothing else."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import hashlib
import os
import time
import uuid
from pathlib import Path

from argus.core import actions as A
from argus.core.safe_names import is_safe_name
from argus.core import belisarius_gate as BG
from argus.core import eligible_target_operation_gate as ETG
from argus.core import ink_socket_gate as G
from argus.core import official_identity as OI
from argus.core import paths
from argus.core import route_wiring as RW
from argus.core import scroll_ids
from argus.core import seed_growth as SG
from argus.core import volume_acquisition as VA
from argus.core import volume_id_gate as VG
from argus.core import villa_provider_adapter as PVA
from argus.core import workspace as W
from argus.core import blender_roundtrip as BR
from argus.core import provider_updates as PU
from argus.core import provider_actions as PCA
from argus.core import storage_catalog


from argus.core import interpretation_actions as IA
from argus.core import science_attach as SCA
from argus.core import sealed_review_queue as SQ
from argus.core import storage_policy as SP
from argus.core import user_data as UD


def _storage() -> dict:
    current = SP.status()
    c = current["drives"]["c"]
    t = current["drives"]["t"]
    return {"policy_id": current["policy_id"],
            "c_free_gib": c["free_gib"], "t_free_gib": t["free_gib"],
            "c_floor_gib": SP.C_FLOOR_GIB, "t_floor_gib": SP.T_FLOOR_GIB,
            "above_floors": current["above_floors"]}



def plan_workspace_create(p: dict) -> dict:
    return {"changes": ["writes one workspace manifest under state/workspaces"],
            "cost": {"bytes": "< 2 KB", "gpu": "none", "seconds": "< 1"},
            "leases": [], "may_refuse": ["BAD_SLUG", "NO_SCROLL", "SLUG_TAKEN"],
            "reversible": True}


def do_workspace_create(spec: A.ActionSpec) -> dict:
    p = spec.params
    ws = W.create(p.get("slug", ""), scroll=p.get("scroll", ""),
                  objective=p.get("objective", ""), actor=spec.actor,
                  civilization=p.get("civilization"), palette=p.get("palette"))
    return {"status": "OK", "workspace": ws}


def plan_workspace_select(p: dict) -> dict:
    return {"changes": ["updates the current-workspace pointer"],
            "cost": {"bytes": "< 1 KB", "gpu": "none", "seconds": "< 1"},
            "leases": [], "may_refuse": ["NO_WORKSPACE"], "reversible": True}


def do_workspace_select(spec: A.ActionSpec) -> dict:
    slug = spec.params.get("slug", "")
    if not W.get(slug):
        raise A.Refused("NO_WORKSPACE", "no workspace named %r" % slug)
    A.STATE.mkdir(parents=True, exist_ok=True)
    (A.STATE / "current_workspace.json").write_text(
        json.dumps({"slug": slug, "by": spec.actor,
                    "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1),
        encoding="utf-8")
    return {"status": "OK", "selected": slug}


def plan_workspace_readiness(p: dict) -> dict:
    return {"changes": [], "cost": {"bytes": 0, "gpu": "none", "seconds": "< 2"},
            "leases": [], "may_refuse": ["NO_WORKSPACE"], "reversible": True,
            "read_only": True}


def do_workspace_readiness(spec: A.ActionSpec) -> dict:
    return {"status": "OK", "readiness": W.readiness(spec.params.get("slug", ""))}



def plan_inventory_scan(p: dict) -> dict:
    return {"changes": ["writes one inventory manifest; downloads nothing"],
            "cost": {"bytes_downloaded": 0, "gpu": "none", "seconds": "2-20"},
            "leases": ["data"], "may_refuse": ["LEASE_HELD"], "reversible": True,
            "note": ("bounded by construction: it lists declared roots and never walks a data root "
                     "recursively")}


def do_inventory_scan(spec: A.ActionSpec) -> dict:
    """What we already hold."""
    roots = {
        "fragments_remote_cache": paths.science_data("fragments_remote"),
        "runs": paths.runs(),
        "canonical_model": Path(_argus_public_path('legacy', 'models')),
    }
    fragment_rows = storage_catalog.fragments(
        fallback_root=paths.science_data("fragments"))
    out = {"fragments": {
        "present": any(row.get("present") for row in fragment_rows),
        "n_entries": len(fragment_rows),
        "entries": [row["member"] for row in fragment_rows[:40]],
        "truncated": len(fragment_rows) > 40,
        "local": sum(1 for row in fragment_rows if row.get("present")),
        "restorable": sum(1 for row in fragment_rows
                          if not row.get("present") and row.get("restorable")),
        "assets": fragment_rows[:40],
    }}
    for name, root in roots.items():
        if not root.is_dir():
            out[name] = {"present": False}
            continue
        entries = sorted(p.name for p in root.iterdir())
        out[name] = {"present": True, "path": str(root), "n_entries": len(entries),
                     "entries": entries[:40],
                     "truncated": len(entries) > 40}
    return {"status": "OK", "held": out, "downloaded_bytes": 0,
            "deduplication": ("nothing was fetched, so nothing could be duplicated. An "
                              "import action content-addresses before it writes"),
            "storage": _storage()}



def plan_preflight(p: dict) -> dict:
    return {"changes": [], "cost": {"bytes": 0, "gpu": "observes only", "seconds": "< 5"},
            "leases": [], "may_refuse": [], "reversible": True, "read_only": True}


def do_preflight(spec: A.ActionSpec) -> dict:
    """Everything that must be true before a GPU pipeline may start."""
    gpu = A.observe_external_gpu_owner()
    lease = A.read_lease("gpu")
    st = _storage()
    checks = [
        {"check": "storage above floors", "ok": st["above_floors"], "detail": st},
        {"check": "no external GPU owner", "ok": gpu.get("busy") is False, "detail": gpu,
         "why": "a chain started from a shell holds no lease and still owns the card"},
        {"check": "gpu lease free", "ok": lease is None, "detail": lease},
        {"check": "audit chain intact", "ok": A.verify_audit_chain()["status"] in
                                              ("INTACT", "EMPTY"),
         "detail": A.verify_audit_chain()},
    ]
    ok = all(c["ok"] for c in checks)
    return {"status": "OK" if ok else "BLOCKED", "checks": checks,
            "verdict": ("clear to start a bounded GPU job" if ok else
                        "at least one precondition is not met; the failing check names it")}



def plan_pipeline_start(p: dict) -> dict:
    name = p.get("pipeline")
    return {"changes": ["creates a job directory and starts one bounded worker"],
            "cost": {"gpu": "exclusive", "seconds": "job-dependent"},
            "leases": ["gpu", "experiment"],
            "may_refuse": ["LEASE_HELD", "EXTERNAL_GPU_OWNER", "UNKNOWN_PIPELINE",
                           "STORAGE_FLOOR"],
            "reversible": False,
            "pipeline": name,
            "note": "the pipeline name selects a registered plan; no command string is accepted"}


PIPELINES = {
    "example_pipeline": "example registered GPU pipeline; a build registers its own",
}


def do_pipeline_start(spec: A.ActionSpec) -> dict:
    name = spec.params.get("pipeline")
    if name not in PIPELINES:
        raise A.Refused("UNKNOWN_PIPELINE",
                        "%r is not a registered pipeline. Registered: %s"
                        % (name, sorted(PIPELINES)))
    st = _storage()
    if not st["above_floors"]:
        raise A.Refused("STORAGE_FLOOR", "storage is below a floor", storage=st)
    gpu = A.observe_external_gpu_owner()
    if gpu.get("busy"):
        raise A.Refused("EXTERNAL_GPU_OWNER",
                        "a science process already owns the card (pids %s). One owner, "
                        "always" % gpu.get("pids"), observed=gpu)
    try:
        RW.launch_v3(spec.params.get("authorization_id"),
                     packet=spec.params.get("launch_packet"),
                     receipt_dir=_route_receipt_dir(), stage="pipeline.start:%s" % name)
    except RW.RouteRefusal as exc:
        raise A.Refused("LAUNCH_AUTHORIZATION_V3", str(exc).splitlines()[0],
                        receipt=str(exc.receipt_path), pipeline=name) from None
    raise A.Refused("PIPELINE_NOT_ENABLED",
                    "starting a new GPU pipeline is registered and planned but not enabled in "
                    "this build. One process owns the card at a time; a second launcher would "
                    "be a second owner.",
                    pipeline=name, description=PIPELINES[name])


def _route_receipt_dir() -> Path:
    """Typed refusal receipts from service-side route gates, beside the command state."""
    return A.STATE / "route_refusals"



def plan_acquire_dry_run(p: dict) -> dict:
    return {"changes": ["none: enumerates an acquisition from local metadata and fetches nothing"],
            "cost": {"bytes": "0 fetched", "gpu": "none", "seconds": "< 2"},
            "leases": [],
            "may_refuse": ["VOLUME_ID", "ACQUISITION_DRY_RUN"],
            "reversible": True,
            "note": "the same argus.core.route_wiring.acquire_dry_run the CLI calls; a real "
                    "fetch is not an action on this surface"}


def do_acquire_dry_run(spec: A.ActionSpec) -> dict:
    p = spec.params
    try:
        out = RW.acquire_dry_run(receipt_dir=_route_receipt_dir(), scroll=p.get("scroll"),
                                 volume_id=p.get("volume_id"), url=p.get("url"),
                                 phase=p.get("phase"), array_path=p.get("array_path") or "",
                                 roi=p.get("roi"), zarray_meta=p.get("zarray"),
                                 byte_ceiling=p.get("byte_ceiling"))
    except RW.RouteRefusal as exc:
        raise A.Refused(exc.gate, str(exc).splitlines()[0],
                        receipt=str(exc.receipt_path)) from None
    return {"status": "OK", "plan": dict(out, keys=out["keys"][:20],
                                         keys_truncated=len(out["keys"]) > 20)}


ACQUIRE_EXECUTE_FIELDS = frozenset({
    "url", "scroll", "volume_id", "phase", "array_path", "roi", "zarray",
    "byte_ceiling", "declared", "official_identity", "target_authority",
    "authorization_id", "launch_packet", "approved_plan_sha256",
})


def _sha256_json(value) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _valid_official_identity(value) -> tuple[bool, str]:
    """Check the resolver's immutable proof envelope without making a network request."""
    if not isinstance(value, dict) or value.get("state") != OI.PROVEN:
        return False, "official_identity must be a PROVEN official catalogue resolution"
    if value.get("contract") != OI.CONTRACT:
        return False, "official_identity has the wrong resolver contract"
    resolved = value.get("resolved_identity")
    if not isinstance(resolved, dict) or OI.canon_sha(resolved) != value.get("identity_sha256"):
        return False, "official_identity identity_sha256 does not re-derive from resolved_identity"
    return True, ""


def _acquire_execute_plan(p: dict) -> dict:
    """Build the immutable, zero-fetch execution plan shared by /plan and submit()."""
    unknown = sorted(set(p) - ACQUIRE_EXECUTE_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "acquire.execute does not accept %s" % unknown)
    required = ("url", "scroll", "volume_id", "phase", "authorization_id", "launch_packet",
                "official_identity")
    missing = [k for k in required if p.get(k) in (None, "")]
    if missing:
        return {
            "ready": False, "required": list(required), "missing": missing,
            "changes": ["downloads only after every listed gate passes"],
            "cost": {"gpu": "none", "network": "bounded by phase ceiling", "seconds": "variable"},
            "leases": ["data"], "may_refuse": ["MISSING_PARAMETER", "PLAN_NOT_APPROVED",
                                                   "LAUNCH_AUTHORIZATION_V3", "VOLUME_ID",
                                                   "OFFICIAL_IDENTITY", "TARGET_AUTHORITY",
                                                   "ACQUISITION_DISABLED", "ACQUISITION_FAILED"],
            "reversible": False,
            "note": "run /plan with all required fields; this planner never makes a request",
        }
    try:
        v = VG.verify("ACQUISITION", scroll=p["scroll"],
                      declared_volume_id=p["volume_id"],
                      observed_volume_id=VG.parse_volume_id_from_url(p["url"]))
    except Exception as exc:
        v = {"verified": False, "state": "UNKNOWN", "reasons": [str(exc)]}
    try:
        acquisition = VA.plan(url=p["url"], array_path=p.get("array_path") or "",
                              roi=p.get("roi"), phase=p["phase"], zarray_meta=p.get("zarray"),
                              scroll=p["scroll"], volume_id=p["volume_id"],
                              byte_ceiling=p.get("byte_ceiling"))
    except VA.AcquisitionRefusal as exc:
        acquisition = {"ready": False, "planning_refusal": str(exc)}
    official_ok, official_why = _valid_official_identity(p.get("official_identity"))
    if official_ok:
        resolved = p["official_identity"].get("resolved_identity") or {}
        if resolved.get("canonical_scroll") != p["scroll"]:
            official_ok = False
            official_why = ("official_identity canonical_scroll does not match the requested "
                            "physical scroll")
        elif resolved.get("volume_id") not in (None, "", p["volume_id"]):
            official_ok = False
            official_why = ("official_identity volume_id does not match the requested volume_id")
    bindings = {
        "authorization_id": p.get("authorization_id"),
        "launch_packet_sha256": _sha256_json(p.get("launch_packet")),
        "official_identity_sha256": (p.get("official_identity") or {}).get("identity_sha256"),
        "target_authority_sha256": _sha256_json(p.get("target_authority"))
                               if p.get("target_authority") is not None else None,
        "declared_sha256": _sha256_json(p.get("declared") or {}),
    }
    body = {"schema": "argus-acquire-execution-plan-v1", "ready": bool(
        v.get("verified") and acquisition.get("contract") == VA.CONTRACT and official_ok),
            "volume_identity": v, "acquisition": acquisition, "bindings": bindings,
            "changes": ["writes a content-addressed acquisition store under the ARGUS science-data root",
                        "writes STORE_IDENTITY and ACQUIRE_MANIFEST receipts"],
            "cost": {"gpu": "none", "network": "phase-bounded", "bytes":
                     acquisition.get("byte_ceiling")},
            "leases": ["data"],
            "may_refuse": ["PLAN_NOT_APPROVED", "ACQUISITION_DISABLED", "LAUNCH_AUTHORIZATION_V3",
                           "VOLUME_ID", "OFFICIAL_IDENTITY", "TARGET_AUTHORITY", "LEASE_HELD",
                           "ACQUISITION_FAILED"],
            "reversible": False,
            "execution_root": "paths.science_data('acquisitions')",
            "scientific_boundary": "acquisition receipts do not qualify a detector or a reading"}
    if not official_ok:
        body["official_identity_refusal"] = official_why
    if acquisition.get("planning_refusal"):
        body["ready"] = False
    body["plan_sha256"] = _sha256_json(body)
    return body


def plan_acquire_execute(p: dict) -> dict:
    return _acquire_execute_plan(p)


def plan_identity_resolve(p: dict) -> dict:
    required = ("scroll", "volume_id", "source_url")
    missing = [key for key in required if p.get(key) in (None, "")]
    token = str(p.get("volume_id") or "")
    return {
        "schema": "argus-official-identity-resolution-plan-v1",
        "ready": not missing and OI.is_token(token),
        "required": list(required),
        "missing": missing,
        "changes": ["observes the official catalogue and small .zarray/.zattrs metadata only",
                    "appends content-addressed metadata observations under the science cache"],
        "cost": {"gpu": "none", "network": "metadata only", "bytes": "bounded to 32 MiB per response"},
        "leases": [],
        "may_refuse": ["MISSING_PARAMETER", "VOLUME_TOKEN", "OFFICIAL_IDENTITY"],
        "reversible": True,
        "why_not_ready": (None if OI.is_token(token) else
                          "volume_id must be the official 14-digit volume token"),
    }


def do_identity_resolve(spec: A.ActionSpec) -> dict:
    p = spec.params
    plan = plan_identity_resolve(p)
    if not plan["ready"]:
        raise A.Refused("OFFICIAL_IDENTITY", plan.get("why_not_ready") or
                        "scroll, volume_id and source_url are required")
    cache = OI.MetadataCache(paths.science_cache("official_identity"))
    try:
        catalogue = OI.Catalogue.from_cache(cache)
        resolution = OI.resolve({
            "control_id": p["scroll"],
            "token": p["volume_id"],
            "scroll": p["scroll"],
            "representation": "VOLUME",
            "source_url": p["source_url"],
            "array_path": p.get("array_path") or "0",
            "declared": p.get("declared") if isinstance(p.get("declared"), dict) else {},
        }, catalogue=catalogue, cache=cache)
    except (OI.OfficialIdentityRefusal, OSError, ValueError) as exc:
        raise A.Refused("OFFICIAL_IDENTITY", str(exc)) from None
    if not resolution.get("proven"):
        return {"status": "REFUSED", "code": "OFFICIAL_IDENTITY",
                "why": "official metadata did not prove every required identity field",
                "official_identity": resolution}
    return {"status": "OK", "official_identity": resolution}


def do_acquire_execute(spec: A.ActionSpec) -> dict:
    p = spec.params
    if os.environ.get("ARGUS_ACQUISITION_ENABLED") != "1":
        raise A.Refused("ACQUISITION_DISABLED",
                        "real acquisition is disabled until the operator explicitly sets "
                        "ARGUS_ACQUISITION_ENABLED=1")
    plan = _acquire_execute_plan(p)
    if not plan.get("ready"):
        raise A.Refused("ACQUISITION_PLAN_INVALID",
                        "the acquisition plan is not executable",
                        plan=plan)
    if p.get("approved_plan_sha256") != plan.get("plan_sha256"):
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the zero-fetch plan hash returned by /plan",
                        expected=plan.get("plan_sha256"))
    holder = "acquire:%s" % spec.idempotency_key
    with A.held_lease("data", holder):
        try:
            RW.launch_v3(p["authorization_id"], packet=p["launch_packet"],
                         receipt_dir=_route_receipt_dir(), stage="acquire.execute")
        except RW.RouteRefusal as exc:
            raise A.Refused("LAUNCH_AUTHORIZATION_V3", str(exc).splitlines()[0],
                            receipt=str(exc.receipt_path)) from None
        root = paths.assert_writable(paths.science_data("acquisitions"))
        root.mkdir(parents=True, exist_ok=True)
        try:
            result = VA.acquire(plan["acquisition"], fetcher=VA.HttpFetcher(), root=root,
                                target_authority=p.get("target_authority"),
                                declared=p.get("declared") or {},
                                official=p.get("official_identity"))
        except VA.AcquisitionRefusal as exc:
            raise A.Refused("ACQUISITION_FAILED", str(exc)) from None
        except Exception as exc:
            raise A.Refused("ACQUISITION_FAILED", "%s: %s" % (type(exc).__name__, exc)) from None
        return {"status": "OK", "plan": plan, "acquisition": result}



PROVIDER_EXECUTE_FIELDS = frozenset({
    "capability_id", "input_path", "output_path", "source_binding", "options",
    "authorization_id", "launch_packet", "approved_plan_sha256", "timeout_s",
})


def _provider_execute_plan(p: dict) -> dict:
    unknown = sorted(set(p) - PROVIDER_EXECUTE_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "provider.invoke does not accept %s" % unknown)
    required = ("capability_id", "input_path", "output_path", "source_binding",
                "authorization_id", "launch_packet")
    missing = [k for k in required if p.get(k) in (None, "")]
    if missing:
        return {"schema": "argus-villa-provider-plan-v1", "ready": False,
                "required": list(required), "missing": missing,
                "changes": ["invokes one pinned Villa provider only after the plan hash and launch authorization pass",
                            "writes one differential provider receipt"],
                "cost": {"gpu": "provider-dependent", "seconds": "provider-dependent",
                         "bytes": "declared input/output only"},
                "leases": ["data"], "may_refuse": ["MISSING_PARAMETER", "PLAN_NOT_APPROVED",
                                                       "PROVIDER_DISABLED", "LAUNCH_AUTHORIZATION_V3",
                                                       "PROVIDER_FAILED"],
                "reversible": False,
                "scientific_boundary": "provider invocation is operational; downstream gates still qualify geometry"}
    try:
        out = PVA.plan(capability_id=p["capability_id"], input_path=p["input_path"],
                       output_path=p["output_path"], source_binding=p["source_binding"],
                       options=p.get("options"))
    except PVA.ProviderRefusal as exc:
        return {"schema": "argus-villa-provider-plan-v1", "ready": False,
                "planning_refusal": str(exc), "leases": ["data"],
                "changes": ["none: invalid provider plans never start a subprocess"],
                "cost": {"gpu": "none", "seconds": "< 1", "bytes": 0},
                "reversible": False,
                "may_refuse": ["PROVIDER_PLAN_INVALID", "PROVIDER_DISABLED", "PROVIDER_FAILED"],
                "scientific_boundary": "provider invocation is operational; downstream gates still qualify geometry"}
    body = {"schema": "argus-provider-invocation-plan-v1", "ready": True,
            "provider_plan": out, "launch_packet_sha256": _sha256_json(p["launch_packet"]),
            "authorization_id": p["authorization_id"], "leases": ["data"],
            "changes": ["invokes one pinned Villa provider", "writes one differential provider receipt"],
            "cost": {"gpu": "provider-dependent", "seconds": "provider-dependent",
                     "bytes": "declared input/output only"},
            "reversible": False,
            "scientific_boundary": out.get("scientific_boundary")}
    body["plan_sha256"] = _sha256_json(body)
    return body


def plan_provider_invoke(p: dict) -> dict:
    return _provider_execute_plan(p)


def do_provider_invoke(spec: A.ActionSpec) -> dict:
    if os.environ.get("ARGUS_PROVIDER_EXECUTION_ENABLED") != "1":
        raise A.Refused("PROVIDER_DISABLED",
                        "pinned provider execution is disabled until the operator explicitly "
                        "sets ARGUS_PROVIDER_EXECUTION_ENABLED=1")
    p = spec.params
    plan = _provider_execute_plan(p)
    if not plan.get("ready"):
        raise A.Refused("PROVIDER_PLAN_INVALID", "the provider plan is not executable", plan=plan)
    if p.get("approved_plan_sha256") != plan.get("plan_sha256"):
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the provider plan hash returned by /plan",
                        expected=plan.get("plan_sha256"))
    holder = "provider:%s" % spec.idempotency_key
    with A.held_lease("data", holder):
        try:
            RW.launch_v3(p["authorization_id"], packet=p["launch_packet"],
                         receipt_dir=_route_receipt_dir(), stage="provider.invoke")
        except RW.RouteRefusal as exc:
            raise A.Refused("LAUNCH_AUTHORIZATION_V3", str(exc).splitlines()[0],
                            receipt=str(exc.receipt_path)) from None
        try:
            return PVA.run(plan["provider_plan"], timeout_s=int(p.get("timeout_s", 7200)))
        except PVA.ProviderRefusal as exc:
            raise A.Refused("PROVIDER_FAILED", str(exc)) from None



BLENDER_LAUNCH_FIELDS = frozenset({"mesh_path", "mode", "output_path", "report_path", "timeout_s",
                                   "approved_plan_sha256"})


def _blender_launch_plan(p: dict) -> dict:
    unknown = sorted(set(p) - BLENDER_LAUNCH_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "blender.launch does not accept %s" % unknown)
    required = ("mesh_path", "mode")
    missing = [k for k in required if p.get(k) in (None, "")]
    if missing:
        return {"schema": "argus-blender-launch-plan-v1", "ready": False,
                "required": list(required), "missing": missing,
                "changes": ["starts one real Blender process against one existing mesh"],
                "cost": {"gpu": "none", "seconds": "interactive: unbounded; headless: minutes",
                         "bytes": "declared output mesh and report only"},
                "leases": ["data"],
                "may_refuse": ["MESH_NOT_FOUND", "UNSUPPORTED_FORMAT", "BLENDER_NOT_INSTALLED",
                               "BLENDER_DISABLED", "BLENDER_LAUNCH_FAILED"],
                "reversible": True,
                "scientific_boundary": "launching Blender is operational; it makes no geometry claim"}
    launch_plan = BR.plan_launch(p["mesh_path"], p["mode"], output_path=p.get("output_path"),
                                 report_path=p.get("report_path"))
    body = {"schema": "argus-blender-launch-plan-v1", "ready": bool(launch_plan["ready"]),
            "launch_plan": launch_plan, "leases": ["data"],
            "changes": ["starts one real Blender process against one existing mesh; the source "
                       "mesh is never overwritten"],
            "cost": {"gpu": "none", "seconds": "interactive: unbounded; headless: minutes",
                     "bytes": "declared output mesh and report only"},
            "reversible": True,
            "may_refuse": ["MESH_NOT_FOUND", "UNSUPPORTED_FORMAT", "BLENDER_NOT_INSTALLED",
                           "BLENDER_DISABLED", "BLENDER_LAUNCH_FAILED"],
            "scientific_boundary": "launching Blender is operational; it makes no geometry claim"}
    if not launch_plan["ready"]:
        body["planning_refusal"] = "; ".join(launch_plan["problems"])
    body["plan_sha256"] = _sha256_json(body)
    return body


def plan_blender_launch(p: dict) -> dict:
    return _blender_launch_plan(p)


def do_blender_launch(spec: A.ActionSpec) -> dict:
    if os.environ.get("ARGUS_BLENDER_EXECUTION_ENABLED") != "1":
        raise A.Refused("BLENDER_DISABLED",
                        "Blender execution is disabled until the operator explicitly sets "
                        "ARGUS_BLENDER_EXECUTION_ENABLED=1")
    p = spec.params
    plan = _blender_launch_plan(p)
    if not plan.get("ready"):
        raise A.Refused("BLENDER_PLAN_INVALID", "the Blender launch plan is not executable", plan=plan)
    if p.get("approved_plan_sha256") != plan.get("plan_sha256"):
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the launch plan hash returned by /plan",
                        expected=plan.get("plan_sha256"))
    for key in ("output_path", "report_path"):
        if p.get(key):
            A.refuse_if_sealed(p[key])
    holder = "blender:%s" % spec.idempotency_key
    with A.held_lease("data", holder):
        try:
            return BR.launch(p["mesh_path"], p["mode"], output_path=p.get("output_path"),
                             report_path=p.get("report_path"),
                             timeout_s=int(p.get("timeout_s", 600)))
        except BR.RoundtripRefusal as exc:
            raise A.Refused("BLENDER_LAUNCH_FAILED", str(exc)) from None


BLENDER_VERIFY_FIELDS = frozenset({
    "source_mesh_path", "edited_mesh_path", "receipt_path", "boundary_rel_tolerance", "edit_kind",
    "approved_plan_sha256",
})


def _blender_verify_plan(p: dict) -> dict:
    unknown = sorted(set(p) - BLENDER_VERIFY_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "blender.verify_roundtrip does not accept %s" % unknown)
    required = ("source_mesh_path", "edited_mesh_path", "receipt_path")
    missing = [k for k in required if p.get(k) in (None, "")]
    body = {"schema": "argus-blender-verify-plan-v1",
            "ready": not missing, "required": list(required), "missing": missing,
            "source_mesh_path": p.get("source_mesh_path"), "edited_mesh_path": p.get("edited_mesh_path"),
            "receipt_path": p.get("receipt_path"), "boundary_rel_tolerance": p.get("boundary_rel_tolerance"),
            "edit_kind": p.get("edit_kind"),
            "changes": ["reads two existing meshes; writes one parity receipt; never edits "
                       "either mesh"],
            "cost": {"gpu": "none", "seconds": "< 5", "bytes": "one JSON receipt"},
            "leases": ["data"],
            "may_refuse": ["MISSING_PARAMETER", "BLENDER_DISABLED", "MESH_UNREADABLE",
                           "TOPOLOGY_CHANGED", "COORDINATE_SCALE_DRIFT"],
            "reversible": True,
            "scientific_boundary": "PASS is a mechanical parity result, not a scientific admissibility claim"}
    body["plan_sha256"] = _sha256_json(body)
    return body


def plan_blender_verify(p: dict) -> dict:
    return _blender_verify_plan(p)


def do_blender_verify(spec: A.ActionSpec) -> dict:
    if os.environ.get("ARGUS_BLENDER_EXECUTION_ENABLED") != "1":
        raise A.Refused("BLENDER_DISABLED",
                        "Blender round-trip verification is disabled until the operator "
                        "explicitly sets ARGUS_BLENDER_EXECUTION_ENABLED=1")
    p = spec.params
    plan = _blender_verify_plan(p)
    if not plan.get("ready"):
        raise A.Refused("MISSING_PARAMETER", "the verify plan is not executable", plan=plan)
    if p.get("approved_plan_sha256") != plan.get("plan_sha256"):
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the verify plan hash returned by /plan",
                        expected=plan.get("plan_sha256"))
    A.refuse_if_sealed(p["receipt_path"])
    holder = "blender:%s" % spec.idempotency_key
    with A.held_lease("data", holder):
        kwargs = {}
        if p.get("boundary_rel_tolerance") is not None:
            kwargs["boundary_rel_tolerance"] = float(p["boundary_rel_tolerance"])
        try:
            result = BR.verify_roundtrip(p["source_mesh_path"], p["edited_mesh_path"], **kwargs)
        except BR.RoundtripRefusal as exc:
            raise A.Refused("MESH_UNREADABLE", str(exc)) from None
        receipt = BR.build_receipt(p["source_mesh_path"], p["edited_mesh_path"], result,
                                   edit_kind=p.get("edit_kind"))
        BR.write_receipt(receipt, p["receipt_path"])
        if result["verdict"] == "REFUSE":
            code = ("TOPOLOGY_CHANGED"
                   if any(x.startswith("TOPOLOGY_CHANGED") for x in result["problems"])
                   else "COORDINATE_SCALE_DRIFT")
            raise A.Refused(code, "; ".join(result["problems"]), receipt=receipt,
                            receipt_path=str(p["receipt_path"]))
        return {"status": "OK", "verdict": "PASS", "receipt": receipt,
                "receipt_path": str(p["receipt_path"])}



BELISARIUS_FIELDS = frozenset({
    "vigiles_receipt", "decision", "rationale", "supersedes_sha256",
})


def plan_belisarius_final(p: dict) -> dict:
    unknown = sorted(set(p) - BELISARIUS_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER",
                        "vigiles.final_decision does not accept %s" % unknown)
    missing = [k for k in ("vigiles_receipt", "decision", "rationale")
               if p.get(k) in (None, "")]
    if missing:
        return {"schema": BG.SCHEMA, "ready": False,
                "authority_id": BG.AUTHORITY_ID, "authority_name": BG.AUTHORITY_NAME,
                "required": ["vigiles_receipt", "decision", "rationale"],
                "missing": missing, "decision_options": list(BG.DECISIONS),
                "changes": ["writes one human final-disposition receipt"],
                "cost": {"gpu": "none", "network": "none", "seconds": "< 2"},
                "reversible": False,
                "may_refuse": ["HUMAN_AUTHORITY_REQUIRED", "VIGILES_RECEIPT",
                               "VIGILES_REFUSED", "SUPERSESSION_REQUIRED"],
                "scientific_effect": "none: the wrapper cannot promote or override VIGILES",
                "output_file": str(BG.output_path(A.STATE))}
    try:
        out = BG.plan(vigiles_receipt=p["vigiles_receipt"], decision=p["decision"],
                      state_root=A.STATE)
    except BG.BelisariusRefusal as exc:
        return {"schema": BG.SCHEMA, "ready": False, "authority_id": BG.AUTHORITY_ID,
                "authority_name": BG.AUTHORITY_NAME, "planning_refusal": str(exc),
                "changes": ["none: an invalid final disposition never writes a receipt"],
                "cost": {"gpu": "none", "network": "none", "seconds": "< 1"},
                "reversible": False,
                "may_refuse": ["VIGILES_RECEIPT", "VIGILES_REFUSED"],
                "output_file": str(BG.output_path(A.STATE))}
    out["may_refuse"] = ["HUMAN_AUTHORITY_REQUIRED", "VIGILES_REFUSED",
                          "SUPERSESSION_REQUIRED"]
    out.setdefault("changes", ["writes one human final-disposition receipt"])
    out.setdefault("cost", {"gpu": "none", "network": "none", "seconds": "< 2"})
    out.setdefault("reversible", False)
    return out


def do_belisarius_final(spec: A.ActionSpec) -> dict:
    if not spec.actor.startswith("human:"):
        raise A.Refused("HUMAN_AUTHORITY_REQUIRED",
                        "the final-disposition door accepts a human actor only")
    p = spec.params
    planned = plan_belisarius_final(p)
    if not planned.get("ready"):
        if planned.get("vigiles_terminal") == "REFUSED":
            raise A.Refused("VIGILES_REFUSED",
                            "a final YES cannot override a REFUSED VIGILES receipt",
                            plan=planned)
        raise A.Refused("FINAL_DISPOSITION_INVALID",
                        planned.get("planning_refusal", "the final-disposition plan is not ready"),
                        plan=planned)
    try:
        return {"status": "OK", "final": BG.record(
            vigiles_receipt=p["vigiles_receipt"], decision=p["decision"],
            rationale=p["rationale"], actor=spec.actor, state_root=A.STATE,
            supersedes_sha256=p.get("supersedes_sha256"))}
    except BG.BelisariusRefusal as exc:
        code = "VIGILES_REFUSED" if "REFUSED VIGILES" in str(exc) else "FINAL_DISPOSITION_REFUSED"
        raise A.Refused(code, str(exc)) from None



def plan_job_cancel(p: dict) -> dict:
    return {"changes": ["marks one cancellable job cancelled"],
            "cost": {"gpu": "releases", "seconds": "< 5"}, "leases": [],
            "may_refuse": ["NO_JOB", "NOT_CANCELLABLE"], "reversible": False}


def do_job_cancel(spec: A.ActionSpec) -> dict:
    job = A.read_job(spec.params.get("job_id", ""))
    if not job:
        raise A.Refused("NO_JOB", "no job %r" % spec.params.get("job_id"))
    if not job.get("cancellable"):
        raise A.Refused("NOT_CANCELLABLE",
                        "job %s declares itself not cancellable. A science run stopped "
                        "mid-write leaves a partial artefact that looks complete"
                        % job["job_id"])
    job["state"] = "CANCELLED"
    job["cancelled_by"] = spec.actor
    A.write_job(job)
    return {"status": "OK", "job": job}


def plan_job_resume(p: dict) -> dict:
    from argus.core import job_resume as JR
    return JR.plan(p)


def do_job_resume(spec: A.ActionSpec) -> dict:
    from argus.core import job_resume as JR
    return JR.execute(spec.params, actor=spec.actor, request_id=spec.request_id,
                      idempotency_key=spec.idempotency_key)



def plan_model_qualify(p: dict) -> dict:
    qualifying = p.get("to_state") in ("CONTROL_QUALIFIED", "HUNT_QUALIFIED")
    return {"changes": ["advances one model by at most one rung, if its evidence exists"] +
                       (["binds exposure, licence and evaluation evidence for qualifying rungs"]
                        if qualifying else []),
            "cost": {"bytes": "< 4 KB", "gpu": "none"}, "leases": [],
            "may_refuse": ["NO_EVIDENCE", "RUNG_SKIP", "UNKNOWN_MODEL"] +
                          (["NO_LIFECYCLE_PACKET", "PROMOTION_REFUSED"] if qualifying else []),
            "reversible": False}


LADDER = ("DISCOVERED", "PINNED", "INVOKED", "PARITY_VERIFIED",
          "CONTROL_QUALIFIED", "HUNT_QUALIFIED")


def do_model_qualify(spec: A.ActionSpec) -> dict:
    """A rung is earned by an artefact on disk, never by a click."""
    model = spec.params.get("model")
    want = spec.params.get("to_state")
    if want not in LADDER:
        raise A.Refused("UNKNOWN_RUNG", "to_state must be one of %s" % (LADDER,))
    evidence = spec.params.get("evidence_path")
    if not evidence or not Path(evidence).is_file():
        raise A.Refused("NO_EVIDENCE",
                        "advancing %r to %s requires an evidence artefact that exists. "
                        "A rung asserted without a receipt is the thing the ladder exists "
                        "to prevent" % (model, want))
    result = {"status": "OK", "model": model, "to_state": want,
              "evidence": evidence,
              "note": "recorded as a request; the ledger writer applies it under its own rules"}
    if want in ("CONTROL_QUALIFIED", "HUNT_QUALIFIED"):
        from argus.core import model_promotion as MP
        try:
            result["promotion_packet"] = MP.validate(
                model=model, to_state=want, lifecycle=spec.params.get("lifecycle"))
        except MP.PromotionRefusal as exc:
            code = ("NO_LIFECYCLE_PACKET" if "required" in str(exc) or "missing" in str(exc)
                    else "PROMOTION_REFUSED")
            raise A.Refused(code, str(exc)) from exc
    return result



def plan_candidate_open(p: dict) -> dict:
    return IA.PLANNERS["candidate.open"](p)


def do_candidate_open(spec: A.ActionSpec) -> dict:
    return IA.DOERS["candidate.open"](spec)


def plan_evidence_reproduce(p: dict) -> dict:
    return {"changes": [], "cost": {"gpu": "none", "seconds": "< 2"}, "leases": [],
            "may_refuse": ["NO_RUN"], "reversible": True, "read_only": True}


def do_evidence_reproduce(spec: A.ActionSpec) -> dict:
    """The exact command and the identities it must reproduce."""
    run = spec.params.get("run")
    if not isinstance(run, str) or not run or not is_safe_name(run):
        raise A.Refused("BAD_RUN", "a run is named by its plain run id, never by a path")
    root = Path(_argus_public_path('home', 'runs')) / run
    if not root.is_dir():
        raise A.Refused("NO_RUN", "no run root %r" % run)
    plan = root / "PLAN.json"
    d = json.loads(plan.read_text(encoding="utf-8")) if plan.is_file() else {}
    arm = d.get("arm")
    return {"status": "OK", "run": run, "sealed": not (root.stat().st_mode & 0o200),
            "command": (d.get("command") or "see the run's own receipt"),
            "must_reproduce": {k: v.get("membership_hash")
                               for k, v in (d.get("fragments") or {}).items()},
            "artifacts": sorted(p.name for p in root.iterdir()),
            "note": "this returns the command and the identities. It does not run anything"}



def plan_import_action(params: dict) -> dict:
    return {"changes": [],
            "cost": {"bytes_downloaded": 0, "gpu": "none", "network": "one HEAD for a URL",
                     "seconds": "< 30"},
            "leases": [], "reversible": True, "read_only": True,
            "may_refuse": ["NO_SUCH_PATH", "UNREACHABLE"],
            "note": ("inspects and decides. It never transfers: planning and spending are "
                     "different acts")}


def do_import_plan(spec: A.ActionSpec) -> dict:
    from argus.core.ingest import IngestRefusal, plan_import
    src = spec.params.get("source")
    if not src:
        raise A.Refused("NO_SOURCE", "params.source must be a path or a URL")
    try:
        return {"status": "OK", "plan": plan_import(
            src, meta=spec.params.get("meta") or {},
            name_hint=spec.params.get("name_hint") or src)}
    except IngestRefusal as exc:
        raise A.Refused("INGEST_REFUSED", str(exc)[:200])


def plan_import_segment(params: dict) -> dict:
    from argus.core import segment_import as SI
    return SI.plan(params)


def do_import_segment(spec: A.ActionSpec) -> dict:
    from argus.core import segment_import as SI
    return SI.execute(spec.params, actor=spec.actor)



def plan_secret(op):
    def _p(params):
        return {"changes": ["writes to the OS credential manager only"
                            if op != "validate" else "makes one read-only provider call"],
                "cost": {"bytes": "< 1 KB", "gpu": "none", "network":
                         ("one request" if op in ("validate", "rotate") else "none")},
                "leases": [], "reversible": op != "remove",
                "may_refuse": ["UNKNOWN_PROVIDER", "TOO_SHORT", "VALIDATION_FAILED"],
                "never": "no value is returned, logged, or echoed by any of these"}
    return _p


def _secret_call(fn, *a, **kw):
    from argus.core.secrets import SecretRefusal
    try:
        return fn(*a, **kw)
    except SecretRefusal as exc:
        raise A.Refused("SECRET_REFUSED", str(exc)[:200])


def do_secret_set(spec: A.ActionSpec) -> dict:
    from argus.core import secrets as S
    v = spec.params.get("value")
    if not isinstance(v, str):
        raise A.Refused("NO_VALUE", "params.value must be the credential string")
    return {"status": "OK",
            "result": _secret_call(S.set_secret, spec.params.get("provider", ""), v,
                                   actor=spec.actor)}


def do_secret_validate(spec: A.ActionSpec) -> dict:
    from argus.core import secrets as S
    return {"status": "OK",
            "result": _secret_call(S.validate, spec.params.get("provider", ""))}


def do_secret_rotate(spec: A.ActionSpec) -> dict:
    from argus.core import secrets as S
    v = spec.params.get("value")
    if not isinstance(v, str):
        raise A.Refused("NO_VALUE", "params.value must be the new credential string")
    return {"status": "OK",
            "result": _secret_call(S.rotate, spec.params.get("provider", ""), v,
                                   actor=spec.actor)}


def do_secret_remove(spec: A.ActionSpec) -> dict:
    from argus.core import secrets as S
    return {"status": "OK",
            "result": _secret_call(S.remove, spec.params.get("provider", ""),
                                   actor=spec.actor)}


def do_secret_status(spec: A.ActionSpec) -> dict:
    from argus.core import secrets as S
    return {"status": "OK", "result": S.status()}




SEED_GROW_EXECUTE_FIELDS = frozenset({
    "executable", "qualification_receipt", "volume", "output_path", "params_path",
    "candidates", "source_binding", "policy_path", "authorized", "approved_plan_sha256",
    "timeout_s",
})


def _seed_grow_execute_plan(p: dict) -> dict:
    unknown = sorted(set(p) - SEED_GROW_EXECUTE_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "seed.grow.execute does not accept %s" % unknown)
    required = ("executable", "qualification_receipt", "volume", "output_path", "params_path",
               "candidates", "source_binding")
    missing = [k for k in required if p.get(k) in (None, "", [])]
    body = {"schema": "argus-seed-grow-execute-plan-v1",
            "changes": ["runs one real, already-qualified seed-growth binary against one "
                       "already-registered scroll's volume; writes one new, unused output"],
            "cost": {"gpu": "none", "seconds": "minutes, volume-dependent",
                     "bytes": "one grown tifxyz surface"},
            "leases": ["data"], "reversible": True,
            "may_refuse": ["MISSING_PARAMETER", "SEED_GROW_DISABLED", "ELIGIBLE_TARGET_GATE",
                          "SEED_GROW_PLAN_INVALID", "PLAN_NOT_APPROVED", "SEED_GROW_FAILED"],
            "scientific_boundary": "A successful grow is geometry apparatus. Sheet identity, "
                                   "topology, alignment, and ink remain separately gated."}
    if missing:
        body.update(ready=False, required=list(required), missing=missing)
        return body
    source_binding = p["source_binding"] if isinstance(p["source_binding"], dict) else {}
    gate_verdict = ETG.preflight(
        declared_physical_scroll=source_binding.get("physical_scroll") or "",
        target_volume_id=source_binding.get("volume_id") or "",
        operation_class=ETG.GEOMETRY_ONLY,
        authorized=p.get("authorized"),
        target_volume_source=p.get("volume"),
    )
    body["eligible_target_gate"] = gate_verdict
    if gate_verdict["verdict"] != "PERMITTED":
        body.update(ready=False, planning_refusal="; ".join(gate_verdict["reasons"]))
        return body
    try:
        sg_plan = SG.plan(executable=p["executable"], qualification_receipt=p["qualification_receipt"],
                          volume=p["volume"], output_path=p["output_path"],
                          params_path=p["params_path"], candidates=p["candidates"],
                          source_binding=source_binding, policy_path=p.get("policy_path"))
    except SG.SeedGrowthRefusal as exc:
        body.update(ready=False, planning_refusal=str(exc))
        return body
    body.update(ready=True, seed_growth_plan=sg_plan)
    body["plan_sha256"] = _sha256_json(body)
    return body


def plan_seed_grow_execute(p: dict) -> dict:
    return _seed_grow_execute_plan(p)


def do_seed_grow_execute(spec: A.ActionSpec) -> dict:
    if os.environ.get("ARGUS_SEED_GROW_EXECUTION_ENABLED") != "1":
        raise A.Refused("SEED_GROW_DISABLED",
                        "real seed-growth execution is disabled until the operator explicitly "
                        "sets ARGUS_SEED_GROW_EXECUTION_ENABLED=1")
    p = spec.params
    plan = _seed_grow_execute_plan(p)
    gv = plan.get("eligible_target_gate") or {}
    if gv.get("verdict") != "PERMITTED":
        raise A.Refused("ELIGIBLE_TARGET_GATE",
                        "the eligible-target operation gate refused this grow",
                        gate_receipt=gv)
    if not plan.get("ready"):
        raise A.Refused("SEED_GROW_PLAN_INVALID", "the seed-growth plan is not executable", plan=plan)
    if p.get("approved_plan_sha256") != plan.get("plan_sha256"):
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the seed-grow plan hash returned by /plan",
                        expected=plan.get("plan_sha256"))
    holder = "seed-grow:%s" % spec.idempotency_key
    with A.held_lease("data", holder):
        try:
            return SG.run(plan["seed_growth_plan"], timeout_s=int(p.get("timeout_s", 7200)))
        except SG.SeedGrowthRefusal as exc:
            raise A.Refused("SEED_GROW_FAILED", str(exc)) from None


SURFACE_PREPARE_FIELDS = frozenset({"scroll", "authorized", "approved_plan_sha256"})


def _surface_prepare_plan(p: dict) -> dict:
    """\"Prepare exact eligible surface\", in one call: identity preflight, a bounded read of the scroll's own exact eligible volume, surface growth, flattening, multi-depth rendering and evidence..."""
    unknown = sorted(set(p) - SURFACE_PREPARE_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER",
                        "surface.prepare_exact_eligible does not accept %s" % unknown)
    body = {
        "schema": "argus-surface-prepare-plan-v1",
        "changes": ["walks this scroll's process-contract pipeline (identity, a bounded read "
                    "of its exact eligible volume, surface growth, flattening, multi-depth "
                    "rendering, evidence recording) via the existing resumable workflow "
                    "runner; halts honestly at the first stage that is not ready rather than "
                    "fabricating success"],
        "cost": {"gpu": "none", "seconds": "stage-dependent, minutes to tens of minutes",
                 "network": "a bounded read of the scroll's own exact eligible volume only"},
        "leases": ["data"], "reversible": False,
        "may_refuse": ["MISSING_PARAMETER", "UNRESOLVABLE_SCROLL", "NOT_PRIZE_ELIGIBLE",
                       "ELIGIBLE_TARGET_GATE", "SURFACE_PREPARE_DISABLED", "STORAGE_FLOOR",
                       "PLAN_NOT_APPROVED"],
        "scientific_boundary": "this prepares GEOMETRY only -- identity, acquisition, growth, "
                               "flattening and rendering. It runs no detector and makes no ink "
                               "claim; ink and detector work remain separately gated.",
    }
    scroll = str(p.get("scroll") or "").strip()
    if not scroll:
        body.update(ready=False, planning_refusal="MISSING_PARAMETER: scroll is required")
        return body
    try:
        canon = scroll_ids.resolve(scroll)
    except KeyError as e:
        body.update(ready=False, planning_refusal="UNRESOLVABLE_SCROLL: %s" % e)
        return body
    reg = ETG.load_registry()
    entry = ETG._find_entry(reg["doc"], canon)
    grand_prize = (entry or {}).get("grand_prize_2027") or {}
    volume_id = grand_prize.get("volume_id")
    if not grand_prize.get("eligible") or not volume_id:
        body.update(ready=False,
                    planning_refusal=("NOT_PRIZE_ELIGIBLE: %r has no eligible "
                                      "grand_prize_2027 volume_id in %s" % (canon, reg["path"])))
        return body
    target_volume_source = (
        "https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com/%s/volumes/"
        "%s-masked.zarr" % (canon, volume_id))
    gate_verdict = ETG.preflight(
        declared_physical_scroll=canon,
        target_volume_id=volume_id,
        operation_class=ETG.GEOMETRY_ONLY,
        authorized=bool(p.get("authorized", True)),
        target_volume_source=target_volume_source,
    )
    body["eligible_target_gate"] = gate_verdict
    body["resolved_scroll"] = canon
    body["target_volume_id"] = volume_id
    body["target_volume_source"] = target_volume_source
    if gate_verdict["verdict"] != "PERMITTED":
        body.update(ready=False, planning_refusal="; ".join(gate_verdict["reasons"]))
        return body
    body.update(ready=True)
    stable = json.loads(json.dumps(body))
    stable.get("eligible_target_gate", {}).pop("decided_utc", None)
    body["plan_sha256"] = _sha256_json(stable)
    return body


def plan_surface_prepare(p: dict) -> dict:
    return _surface_prepare_plan(p)


def do_surface_prepare(spec: A.ActionSpec) -> dict:
    if os.environ.get("ARGUS_SURFACE_PREPARE_ENABLED") != "1":
        raise A.Refused("SURFACE_PREPARE_DISABLED",
                        "real surface preparation is disabled until the operator explicitly "
                        "sets ARGUS_SURFACE_PREPARE_ENABLED=1")
    p = spec.params
    plan = _surface_prepare_plan(p)
    gv = plan.get("eligible_target_gate") or {}
    if gv.get("verdict") != "PERMITTED":
        raise A.Refused("ELIGIBLE_TARGET_GATE",
                        "the eligible-target operation gate refused this preparation",
                        gate_receipt=gv)
    if not plan.get("ready"):
        raise A.Refused("SURFACE_PREPARE_PLAN_INVALID", "the plan is not executable", plan=plan)
    if p.get("approved_plan_sha256") != plan.get("plan_sha256"):
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the plan hash returned by /plan",
                        expected=plan.get("plan_sha256"))
    st = _storage()
    if not st["above_floors"]:
        raise A.Refused("STORAGE_FLOOR", "storage is below a floor", storage=st)
    holder = "surface-prepare:%s" % spec.idempotency_key
    with A.held_lease("data", holder):
        from argus.core import scroll_workflow as SW
        try:
            return SW.run(plan["resolved_scroll"], operator_id=spec.actor or "operator")
        except KeyError as e:
            raise A.Refused("UNRESOLVABLE_SCROLL", str(e)) from None


COPY_OUT_IN_EXECUTE_FIELDS = frozenset({
    "source_tifxyz_path", "volume_path", "normal_grid_path", "direction", "output_dir",
    "source_binding", "review", "pass1_options", "pass2_options", "authorized",
    "approved_plan_sha256",
})


def _copy_out_in_execute_plan(p: dict) -> dict:
    unknown = sorted(set(p) - COPY_OUT_IN_EXECUTE_FIELDS)
    if unknown:
        raise A.Refused("UNKNOWN_PARAMETER", "copy_out_in.execute does not accept %s" % unknown)
    required = ("source_tifxyz_path", "volume_path", "normal_grid_path", "direction",
               "output_dir", "source_binding", "review")
    missing = [k for k in required if p.get(k) in (None, "")]
    body = {"schema": "argus-copy-out-in-execute-plan-v1",
            "changes": ["runs the classical (non-neural) VC3D Copy Out/In tool against one "
                       "already-reviewed source wrap; the source is never overwritten"],
            "cost": {"gpu": "none", "seconds": "minutes", "bytes": "one candidate tifxyz surface"},
            "leases": ["data"], "reversible": True,
            "may_refuse": ["MISSING_PARAMETER", "COPY_OUT_IN_DISABLED", "ELIGIBLE_TARGET_GATE",
                          "COPY_OUT_IN_PLAN_INVALID", "PLAN_NOT_APPROVED", "COPY_OUT_IN_FAILED"],
            "scientific_boundary": "a completed round trip is geometry apparatus with a real "
                                   "propagation-error control; sheet identity remains separately gated"}
    if missing:
        body.update(ready=False, required=list(required), missing=missing)
        return body
    source_binding = p["source_binding"] if isinstance(p["source_binding"], dict) else {}
    gate_verdict = ETG.preflight(
        declared_physical_scroll=source_binding.get("physical_scroll") or "",
        target_volume_id=source_binding.get("volume_id") or "",
        operation_class=ETG.GEOMETRY_ONLY,
        authorized=p.get("authorized"),
        target_volume_source=p.get("volume_path"),
    )
    body["eligible_target_gate"] = gate_verdict
    if gate_verdict["verdict"] != "PERMITTED":
        body.update(ready=False, planning_refusal="; ".join(gate_verdict["reasons"]))
        return body
    try:
        coi_plan = PVA.plan_copy_out_in(
            source_tifxyz_path=p["source_tifxyz_path"], volume_path=p["volume_path"],
            normal_grid_path=p["normal_grid_path"], direction=p["direction"],
            output_dir=p["output_dir"], source_binding=source_binding, review=p["review"],
            pass1_options=p.get("pass1_options"), pass2_options=p.get("pass2_options"))
    except PVA.ProviderRefusal as exc:
        body.update(ready=False, planning_refusal=str(exc))
        return body
    body.update(ready=True, copy_out_in_plan=coi_plan)
    body["plan_sha256"] = _sha256_json(body)
    return body


def plan_copy_out_in_execute(p: dict) -> dict:
    return _copy_out_in_execute_plan(p)


def do_copy_out_in_execute(spec: A.ActionSpec) -> dict:
    if os.environ.get("ARGUS_COPY_OUT_IN_EXECUTION_ENABLED") != "1":
        raise A.Refused("COPY_OUT_IN_DISABLED",
                        "real Copy Out/In execution is disabled until the operator explicitly "
                        "sets ARGUS_COPY_OUT_IN_EXECUTION_ENABLED=1")
    p = spec.params
    plan = _copy_out_in_execute_plan(p)
    gv = plan.get("eligible_target_gate") or {}
    if gv.get("verdict") != "PERMITTED":
        raise A.Refused("ELIGIBLE_TARGET_GATE",
                        "the eligible-target operation gate refused this Copy Out/In run",
                        gate_receipt=gv)
    if not plan.get("ready"):
        raise A.Refused("COPY_OUT_IN_PLAN_INVALID", "the Copy Out/In plan is not executable", plan=plan)
    if p.get("approved_plan_sha256") != plan.get("plan_sha256"):
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the Copy Out/In plan hash returned by /plan",
                        expected=plan.get("plan_sha256"))
    holder = "copy-out-in:%s" % spec.idempotency_key
    with A.held_lease("data", holder):
        try:
            return PVA.run_copy_out_in(plan["copy_out_in_plan"])
        except PVA.ProviderRefusal as exc:
            raise A.Refused("COPY_OUT_IN_FAILED", str(exc)) from None


def _upd_refuse(exc):
    raise A.Refused(getattr(exc, "code", "UPDATE_REFUSED"), str(exc))


def _upd_plan_hash(action: str, plan: dict) -> str:
    """Bind an approval to exactly what was planned: the action, its target and what it would do now."""
    return hashlib.sha256(json.dumps({"action": action, "target": plan.get("target"),
                                      "readiness": plan.get("readiness"), "state": plan.get("state")},
                                     sort_keys=True, default=str).encode()).hexdigest()


def _upd_require_approval(action: str, p: dict, plan: dict) -> None:
    if p.get("approved_plan_sha256") != plan.get("plan_sha256"):
        raise A.Refused("PLAN_NOT_APPROVED",
                        "approved_plan_sha256 must equal the plan hash returned by /plan for %s" % action)


def plan_update_policy(p: dict) -> dict:
    return {"changes": ["records the operator's standing answer to 'may ARGUS check upstream for updates'"],
            "cost": {"bytes": "< 1 KB", "gpu": "none", "seconds": "< 1"}, "leases": [],
            "may_refuse": ["BAD_MODE"], "reversible": True,
            "note": "AUTOMATIC, ASK_FIRST or NEVER. Nothing contacts upstream until this is answered; NEVER means no outbound check at all."}


def do_update_policy(spec: A.ActionSpec) -> dict:
    try:
        return {"status": "OK", "policy": PU.set_policy(spec.params.get("mode", ""), by=spec.actor)}
    except PU.UC.ConveyorError as exc:
        raise A.Refused("BAD_MODE", str(exc)) from None


def plan_update_check(p: dict) -> dict:
    return {"changes": ["records one observation per watched upstream and, for a new immutable revision, a DISCOVERED update with a semantic diff"],
            "cost": {"bytes_downloaded": "metadata only (GitHub and Hugging Face APIs)", "gpu": "none", "seconds": "5-60"},
            "leases": [], "may_refuse": ["POLICY_UNSET", "POLICY_NEVER"], "reversible": True,
            "note": "downloads no model weights and builds nothing; a failed check is reported as a failed check, never as 'up to date'"}


def do_update_check(spec: A.ActionSpec) -> dict:
    try:
        rev, sources = spec.params.get("revision"), spec.params.get("sources") or None
        if rev:
            if not sources or len(sources) != 1:
                raise A.Refused("BAD_PARAMS", "an exact revision needs exactly one source in `sources`")
            return {"status": "OK", "result": PU.discover_exact(sources[0], str(rev))}
        return {"status": "OK", "result": PU.check(sources=sources)}
    except PU.UpdateRefused as exc:
        _upd_refuse(exc)


def _update_target(p: dict) -> tuple:
    return str(p.get("source_id", "")), str(p.get("revision", ""))


def plan_update_stage(p: dict) -> dict:
    sid, rev = _update_target(p)
    return {"changes": ["advances one discovered update: classify, check the licence at that revision, fetch the adapter-contract files (a few files, not the repository), run the contract and its sabotage self-check"],
            "cost": {"bytes_downloaded": "a few source files", "gpu": "none", "seconds": "10-60"}, "leases": [],
            "may_refuse": ["POLICY_UNSET", "POLICY_NEVER", "UNKNOWN_UPDATE", "UNKNOWN_SOURCE"], "reversible": True,
            "target": {"source_id": sid, "revision": rev},
            "note": "does not build the upstream tool and does not activate anything"}


def do_update_stage(spec: A.ActionSpec) -> dict:
    sid, rev = _update_target(spec.params)
    try:
        return {"status": "OK", "result": PU.stage(sid, rev)}
    except PU.UpdateRefused as exc:
        _upd_refuse(exc)


def plan_update_test(p: dict) -> dict:
    sid, rev = _update_target(p)
    return {"changes": ["runs ARGUS's own adapter tests named for this source and records pass/fail on the update"],
            "cost": {"bytes": "< 1 MB", "gpu": "none", "seconds": "10-300"}, "leases": [],
            "may_refuse": ["UNKNOWN_UPDATE", "UNKNOWN_SOURCE"], "reversible": True, "target": {"source_id": sid, "revision": rev}}


def do_update_test(spec: A.ActionSpec) -> dict:
    sid, rev = _update_target(spec.params)
    try:
        return {"status": "OK", "result": PU.run_required_tests(sid, rev)}
    except PU.UpdateRefused as exc:
        _upd_refuse(exc)


def plan_update_real_data_control(p: dict) -> dict:
    sid, rev = _update_target(p)
    return {"changes": ["attaches a receipt for a control run on REAL scroll data to one update"],
            "cost": {"bytes": "< 1 MB", "gpu": "none", "seconds": "< 5"}, "leases": [],
            "may_refuse": ["UNKNOWN_UPDATE", "WRONG_STAGE", "NOT_A_REAL_DATA_RECEIPT"], "reversible": False,
            "target": {"source_id": sid, "revision": rev, "receipt_path": str(p.get("receipt_path", ""))},
            "plan_sha256": _upd_plan_hash("provider.update.attach_real_data_control",
                                          {"target": {"source_id": sid, "revision": rev, "receipt_path": str(p.get("receipt_path", ""))}}),
            "note": "the receipt must say real_data=true and carry hashes; a synthetic pass can never stand in for it"}


def do_update_real_data_control(spec: A.ActionSpec) -> dict:
    sid, rev = _update_target(spec.params)
    _upd_require_approval("provider.update.attach_real_data_control", spec.params,
                          plan_update_real_data_control(spec.params))
    path = Path(str(spec.params.get("receipt_path", "")))
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise A.Refused("NOT_A_REAL_DATA_RECEIPT", "receipt_path %r is not a readable JSON receipt" % str(path)) from None
    try:
        return {"status": "OK", "result": PU.attach_real_data_control(sid, rev, doc, by=spec.actor)}
    except PU.UpdateRefused as exc:
        _upd_refuse(exc)


def plan_update_activate(p: dict) -> dict:
    sid, rev = _update_target(p)
    scope = str(p.get("scope", ""))
    out = {"changes": ["makes this revision the one NEW runs of this scope use; existing runs keep the pins they started with"],
           "cost": {"bytes": "< 1 KB", "gpu": "none", "seconds": "< 2"}, "leases": [],
           "may_refuse": ["POLICY_UNSET", "NOT_READY", "UPDATE_HELD", "BAD_SCOPE"], "reversible": True,
           "target": {"source_id": sid, "revision": rev, "scope": scope},
           "note": "the previous pin is kept as the rollback target"}
    try:
        revision = PU.S.find_revision(sid, rev) or rev
        u, _ = PU.S.load_update(sid, revision)
        src = {r["source_id"]: r for r in PU.D.source_records()}.get(sid)
        if u is not None and src is not None and scope in PU.S.SCOPES:
            r = PU.readiness(u, src)[scope]
            out["readiness"] = r
            out["would_be_refused"] = not r["allowed"]
    except Exception:
        pass
    out["plan_sha256"] = _upd_plan_hash("provider.update.activate", out)
    return out


def do_update_activate(spec: A.ActionSpec) -> dict:
    sid, rev = _update_target(spec.params)
    _upd_require_approval("provider.update.activate", spec.params, plan_update_activate(spec.params))
    try:
        return {"status": "OK", "result": PU.activate(sid, rev, str(spec.params.get("scope", "")), by=spec.actor)}
    except PU.UpdateRefused as exc:
        _upd_refuse(exc)


def plan_update_rollback(p: dict) -> dict:
    sid, scope = str(p.get("source_id", "")), str(p.get("scope", ""))
    active = PU.S.active_pin(sid, scope) if scope in PU.S.SCOPES else None
    out = {"changes": ["restores the previous pin for this source and scope; the rolled-back revision is quarantined, not deleted"],
           "cost": {"bytes": "< 1 KB", "gpu": "none", "seconds": "< 2"}, "leases": [],
           "may_refuse": ["NOTHING_ACTIVE", "BAD_SCOPE"], "reversible": True,
           "target": {"source_id": sid, "scope": scope},
           "state": {"active_revision": (active or {}).get("revision")},
           "would_be_refused": active is None}
    out["plan_sha256"] = _upd_plan_hash("provider.update.rollback", out)
    return out


def do_update_rollback(spec: A.ActionSpec) -> dict:
    _upd_require_approval("provider.update.rollback", spec.params, plan_update_rollback(spec.params))
    try:
        return {"status": "OK", "result": PU.rollback(str(spec.params.get("source_id", "")), str(spec.params.get("scope", "")), by=spec.actor)}
    except PU.UpdateRefused as exc:
        _upd_refuse(exc)


def plan_provider_candidate_plan(p: dict) -> dict:
    return PCA.plan_candidate(p)


def do_provider_candidate_plan(spec: A.ActionSpec) -> dict:
    return PCA.do_plan(spec.params)


def plan_provider_candidate_invoke(p: dict) -> dict:
    return PCA.plan_invoke(p)


def do_provider_candidate_invoke(spec: A.ActionSpec) -> dict:
    return PCA.invoke(spec.params)



def plan_glyph_annotate(p: dict) -> dict:
    return IA.PLANNERS['glyph.annotate'](p)


def do_glyph_annotate(spec: A.ActionSpec) -> dict:
    return IA.DOERS['glyph.annotate'](spec)


def plan_htr_propose(p: dict) -> dict:
    return IA.PLANNERS['htr.propose'](p)


def do_htr_propose(spec: A.ActionSpec) -> dict:
    return IA.DOERS['htr.propose'](spec)


def plan_transcription_claim(p: dict) -> dict:
    return IA.PLANNERS['transcription.claim'](p)


def do_transcription_claim(spec: A.ActionSpec) -> dict:
    return IA.DOERS['transcription.claim'](spec)


def plan_language_write(p: dict) -> dict:
    return IA.PLANNERS['language.write'](p)


def do_language_write(spec: A.ActionSpec) -> dict:
    return IA.DOERS['language.write'](spec)


def plan_translation_propose(p: dict) -> dict:
    return IA.PLANNERS['translation.propose'](p)


def do_translation_propose(spec: A.ActionSpec) -> dict:
    return IA.DOERS['translation.propose'](spec)


def plan_packet_export(p: dict) -> dict:
    return IA.PLANNERS['packet.export'](p)


def do_packet_export(spec: A.ActionSpec) -> dict:
    return IA.DOERS['packet.export'](spec)


def plan_packet_verify(p: dict) -> dict:
    return IA.PLANNERS['packet.verify'](p)


def do_packet_verify(spec: A.ActionSpec) -> dict:
    return IA.DOERS['packet.verify'](spec)



def plan_science_attach_plan(p: dict) -> dict:
    return SCA.PLANNERS['science.attach.plan'](p)


def do_science_attach_plan(spec: A.ActionSpec) -> dict:
    return SCA.DOERS['science.attach.plan'](spec)


def plan_science_attach_execute(p: dict) -> dict:
    return SCA.PLANNERS['science.attach.execute'](p)


def do_science_attach_execute(spec: A.ActionSpec) -> dict:
    return SCA.DOERS['science.attach.execute'](spec)


def plan_user_data_detach(p: dict) -> dict:
    return UD.detach_plan(p)


def do_user_data_detach(spec: A.ActionSpec) -> dict:
    try:
        return UD.detach_execute(spec.params, actor=spec.actor)
    except (UD.UserDataError, OSError) as exc:
        message = str(exc)
        code = message.split(":", 1)[0] if ":" in message else "USER_DATA_DETACH_REFUSED"
        raise A.Refused(code, message) from None


def plan_review_blind_answer(p: dict) -> dict:
    return SQ.PLANNERS['review.blind.answer'](p)


def do_review_blind_answer(spec: A.ActionSpec) -> dict:
    return SQ.DOERS['review.blind.answer'](spec)


REGISTRY = {
    "workspace.create": (plan_workspace_create, do_workspace_create),
    "workspace.select": (plan_workspace_select, do_workspace_select),
    "workspace.readiness": (plan_workspace_readiness, do_workspace_readiness),
    "inventory.scan": (plan_inventory_scan, do_inventory_scan),
    "preflight": (plan_preflight, do_preflight),
    "pipeline.start": (plan_pipeline_start, do_pipeline_start),
    "job.cancel": (plan_job_cancel, do_job_cancel),
    "job.resume": (plan_job_resume, do_job_resume),
    "model.qualify": (plan_model_qualify, do_model_qualify),
    "candidate.open": (plan_candidate_open, do_candidate_open),
    "evidence.reproduce": (plan_evidence_reproduce, do_evidence_reproduce),
    "secret.set": (plan_secret("set"), do_secret_set),
    "secret.validate": (plan_secret("validate"), do_secret_validate),
    "secret.rotate": (plan_secret("rotate"), do_secret_rotate),
    "secret.remove": (plan_secret("remove"), do_secret_remove),
    "secret.status": (plan_secret("status"), do_secret_status),
    "import.plan": (plan_import_action, do_import_plan),
    "acquire.dry_run": (plan_acquire_dry_run, do_acquire_dry_run),
    "identity.resolve": (plan_identity_resolve, do_identity_resolve),
    "acquire.execute": (plan_acquire_execute, do_acquire_execute),
    "provider.invoke": (plan_provider_invoke, do_provider_invoke),
    "seed.grow.execute": (plan_seed_grow_execute, do_seed_grow_execute),
    "surface.prepare_exact_eligible": (plan_surface_prepare, do_surface_prepare),
    "copy_out_in.execute": (plan_copy_out_in_execute, do_copy_out_in_execute),
    "blender.launch": (plan_blender_launch, do_blender_launch),
    "blender.verify_roundtrip": (plan_blender_verify, do_blender_verify),
    "vigiles.final_decision": (plan_belisarius_final, do_belisarius_final),
    "provider.update.policy": (plan_update_policy, do_update_policy),
    "provider.update.check": (plan_update_check, do_update_check),
    "provider.update.stage": (plan_update_stage, do_update_stage),
    "provider.update.test": (plan_update_test, do_update_test),
    "provider.update.attach_real_data_control": (plan_update_real_data_control, do_update_real_data_control),
    "provider.update.activate": (plan_update_activate, do_update_activate),
    "provider.update.rollback": (plan_update_rollback, do_update_rollback),
    "import.segment": (plan_import_segment, do_import_segment),
    "provider.candidate.plan": (plan_provider_candidate_plan, do_provider_candidate_plan),
    "provider.candidate.invoke": (plan_provider_candidate_invoke, do_provider_candidate_invoke),


    "glyph.annotate": (plan_glyph_annotate, do_glyph_annotate),
    "htr.propose": (plan_htr_propose, do_htr_propose),
    "transcription.claim": (plan_transcription_claim, do_transcription_claim),
    "language.write": (plan_language_write, do_language_write),
    "translation.propose": (plan_translation_propose, do_translation_propose),
    "packet.export": (plan_packet_export, do_packet_export),
    "packet.verify": (plan_packet_verify, do_packet_verify),
    "science.attach.plan": (plan_science_attach_plan, do_science_attach_plan),
    "science.attach.execute": (plan_science_attach_execute, do_science_attach_execute),
    "user_data.detach": (plan_user_data_detach, do_user_data_detach),
    "review.blind.answer": (plan_review_blind_answer, do_review_blind_answer),
}


def plan(spec_obj: dict) -> dict:
    spec = A.ActionSpec.parse(spec_obj)
    if spec.action not in REGISTRY:
        raise A.Refused("UNKNOWN_ACTION",
                        "%r is not an allowlisted action. Allowlisted: %s"
                        % (spec.action, sorted(REGISTRY)))
    p = REGISTRY[spec.action][0](spec.params)
    return dict(p, action=spec.action, actor=spec.actor, request_id=spec.request_id,
                idempotency_key=spec.idempotency_key,
                fingerprint=spec.fingerprint(), dry_run=True)


RUN_STARTING_ACTIONS = {
    "provider.invoke", "provider.candidate.invoke", "acquire.execute", "seed.grow.execute",
    "surface.prepare_exact_eligible", "copy_out_in.execute", "blender.launch",
}


def _freeze_run_pin(job: dict) -> None:
    from argus.core import update_store as US
    from argus.core import upstream_discovery as UD
    try:
        pin = US.record_run_pin(job["job_id"], UD.source_records())
    except Exception as exc:
        raise A.Refused("RUN_PIN_FAILED", "the provider pin set could not be frozen for this run (%s: %s); "
                        "a run that is not pinned does not start" % (type(exc).__name__, str(exc)[:120]))
    job["run_pin"] = {"pin_set_sha256": pin["pin_set_sha256"], "frozen_utc": pin["frozen_utc"]}


def submit(spec_obj: dict) -> dict:
    """The one door."""
    spec = A.ActionSpec.parse(spec_obj)
    if spec.action not in REGISTRY:
        raise A.Refused("UNKNOWN_ACTION",
                        "%r is not an allowlisted action. Allowlisted: %s"
                        % (spec.action, sorted(REGISTRY)))
    planned = REGISTRY[spec.action][0](spec.params)

    job_id = "job_%s" % uuid.uuid4().hex[:12]
    claim_key = ("dry-run:" + hashlib.sha256(spec.idempotency_key.encode("utf-8")).hexdigest()) if spec.dry_run else spec.idempotency_key
    seen = A.claim_idempotency(claim_key, job_id, spec.fingerprint())
    if seen:
        if seen.get("fingerprint") != spec.fingerprint():
            raise A.Refused("IDEMPOTENCY_CONFLICT",
                            "idempotency key %r was used for a different request. Returning "
                            "the old job would answer a question nobody asked"
                            % spec.idempotency_key, existing_job=seen.get("job_id"))
        job = A.read_job(seen["job_id"])
        return {"status": "DUPLICATE", "job_id": seen["job_id"], "job": job,
                "note": "same idempotency key and same request: one job, not two"}

    job = {"job_id": job_id, "action": spec.action, "actor": spec.actor,
           "request_id": spec.request_id, "idempotency_key": spec.idempotency_key,
           "fingerprint": spec.fingerprint(), "params": A.redact(spec.params),
           "plan": planned, "state": "RUNNING",
           "cancellable": bool(planned.get("leases")),
           "resumable": False,
           "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        A.write_job(job)
        A.audit({"event": "action.submit", "job_id": job_id, "action": spec.action,
                 "actor": spec.actor, "request_id": spec.request_id,
                 "idempotency_key": spec.idempotency_key, "fingerprint": spec.fingerprint()})
    except Exception as exc:
        job.update(state="FAILED", result={"status": "FAILED", "code": "NOT_STARTED_" + type(exc).__name__,
                                          "why": "the job could not be recorded (%s); the action did not run" % type(exc).__name__},
                   finished_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        try:
            A.write_job(job)
        except Exception:
            pass
        try:
            A.release_idempotency(claim_key, job_id)
        except Exception:
            pass
        raise

    if spec.dry_run:
        job.update(state="PLANNED_ONLY", result={"status": "DRY_RUN", "plan": planned})
        A.write_job(job)
        return {"status": "DRY_RUN", "job_id": job_id, "plan": planned}

    try:
        if spec.action in RUN_STARTING_ACTIONS:
            _freeze_run_pin(job)
            A.write_job(job)
        result = REGISTRY[spec.action][1](spec)
        job.update(state="SUCCEEDED", result=result,
                   finished_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except A.Refused as exc:
        result = exc.as_dict()
        job.update(state="REFUSED", result=result,
                   finished_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except Exception as exc:
        job.update(state="FAILED", result={"status": "FAILED", "code": "UNHANDLED_" + type(exc).__name__,
                                            "why": "the action raised %s; see the service log" % type(exc).__name__},
                   finished_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        A.write_job(job)
        A.audit({"event": "action.finish", "job_id": job_id, "action": spec.action, "actor": spec.actor, "state": "FAILED",
                 "code": job["result"]["code"]})
        raise
    A.write_job(job)
    A.audit({"event": "action.finish", "job_id": job_id, "action": spec.action,
             "actor": spec.actor, "state": job["state"],
             "code": (result or {}).get("code")})
    return {"status": job["state"], "job_id": job_id, "result": result}
