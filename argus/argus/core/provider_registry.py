"""The provider control plane for the ARGUS process."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from argus.core import (blender_roundtrip, control_evidence_kinds as CEK, hecate_candidate, install_tiers,
                        job_preflight, lasagna_candidate, lasagna_maxflow_provider, native_3d_provider,
                        official_models, paths, pseudolabel_controls, reading_board,
                        science_candidates, surface_prediction_provider, translation_plan,
                        volume_acquisition)
from argus.adapters import inksurf_provider

SCHEMA = "argus-provider-registry-v1"
ROW_SCHEMA = "argus-provider-record-v2"
CANDIDATE_REFUSAL_SCHEMA = "argus-provider-candidate-refusal-v1"
VILLA_PROVIDER_RECEIPT_SCHEMA = "argus-villa-provider-receipt-v1"
INK_MAPS_SCHEMA = "argus-ink-maps-v1"

LIFECYCLE_STATES = {
    "CATALOG_ONLY": "recorded and pinned; ARGUS has no code path that plans or runs it",
    "PLAN_ONLY": "ARGUS builds a checked, hash-bound plan or worksheet; no ARGUS code runs it",
    "GATED_EXECUTABLE": "run through a governed action that needs an approved plan, an authorization and its env flag",
    "EXECUTABLE": "run in-process by ARGUS: no external process, no network, no shared-state write",
    "BLOCKED": "a named blocker makes it unusable until that blocker is removed",
    "RESEARCH_ONLY": "usable only as apparatus or comparison; ARGUS can never promote it",
}
_NEEDS_BLOCKER = frozenset({"CATALOG_ONLY", "PLAN_ONLY", "BLOCKED", "RESEARCH_ONLY"})

BLOCKER_KINDS = ("DATA_UNAVAILABLE", "CODE_MISSING", "LICENCE", "EXPOSURE", "RUNTIME", "NOT_AUTHORIZED")
_BLOCKER_PRECEDENCE = ("LICENCE", "DATA_UNAVAILABLE", "CODE_MISSING", "RUNTIME", "NOT_AUTHORIZED", "EXPOSURE")

FAMILIES = ("data", "geometry", "rendering", "fibers", "ink", "interpretation")
HARDWARE_STATES = ("MEASURED", "ASSUMED_NOT_MEASURED", "DECLARED_NOT_MEASURED", "NOT_MEASURED")
UNKNOWN = "UNKNOWN"

CLAIM_CEILINGS = {
    "NO_INK_CLAIM": "third-party audit output; never presented as ink",
    "NO_QUALIFIED_DETECTOR": "may run and emit output; no detector is qualified and no reading claim follows",
    "APPARATUS_ONLY": "exercises the apparatus on exposed material; not unseen-scroll generalization",
    "MECHANICS_ONLY": "shows a released model ran on a bounded control; nothing about ink, face semantics or qualification",
    "DIAGNOSTIC_ONLY": "non-promotable comparator; a diagnostic, never an authority",
    "CANDIDATE_ONLY": "identity and readiness only; nothing here is qualified",
    "OPERATIONAL_ONLY": "a zero-exit run is operational evidence; downstream scientific gates still qualify the output",
    "HUMAN_GATED": "output is a candidate for human review; nothing is certified automatically",
}
INK_COMPATIBLE_CEILINGS = frozenset({"NO_INK_CLAIM", "NO_QUALIFIED_DETECTOR", "APPARATUS_ONLY"})
_FORBIDDEN_ROLES = frozenset({CEK.INDEPENDENT_EVIDENCE, CEK.GROUND_TRUTH})

CANDIDATE_PLAN_ACTION = "provider.candidate.plan"
CANDIDATE_INVOKE_ACTION = "provider.candidate.invoke"

PLANNABLE_IDS = frozenset({
    "hecate_24um", "hecate_96um", "ink_3d_dino_guided", "native_full_3d_ink",
    "lasagna_predict3d", "lasagna_maxflow", "surface_m7_nnunet", "surface_recto",
})

_LICENCE_VC3D = "licence_registry:vc_render_tifxyz / volume-cartographer"
_LICENCE_CT = "licence_registry:Herculaneum CT volumes"

_EXEC_REVISION = re.compile(r"executed revision ([0-9a-f]{40})")
_PDE = CEK.PREDICTION_DISCOVERY_EVIDENCE


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()



def _blocker(kind: str, text: str) -> dict:
    if kind not in BLOCKER_KINDS:
        raise ValueError("unknown blocker kind %r" % (kind,))
    return {"kind": kind, "text": text}


def _primary(blockers: list[dict]) -> dict | None:
    return min(blockers, key=lambda b: _BLOCKER_PRECEDENCE.index(b["kind"])) if blockers else None


def _hw(state: str, summary: str, **fields) -> dict:
    if state not in HARDWARE_STATES:
        raise ValueError("unknown hardware state %r" % (state,))
    return {"state": state, "summary": summary, "needs_gpu": fields.pop("needs_gpu", None),
            "vram_gib": fields.pop("vram_gib", None), "host_ram_gib": fields.pop("host_ram_gib", None),
            **fields}


def _hw_not_measured() -> dict:
    return _hw("NOT_MEASURED", "not measured", source=None)


def _hw_floors(model_key: str) -> dict:
    floors = job_preflight.floors()
    measured = (floors.get("per_model") or {}).get(model_key)
    if not floors.get("measured") or not measured:
        return _hw_not_measured()
    vram = round(float(measured["peak_vram_mb"]) / 1024, 2)
    ram = round(float(measured["peak_working_set_mb"]) / 1024, 2)
    return _hw("MEASURED", "GPU, peak %.2f GiB VRAM and %.2f GiB RAM (one observation)" % (vram, ram),
               needs_gpu=True, vram_gib=vram, host_ram_gib=ram, observations=1,
               source="argus/core/resource_floors.json", measured_utc=floors.get("measured_utc"))


def _hw_native() -> dict:
    profiles = {
        name: {key: profile[key] for key in ("vram_gib", "host_ram_gib", "batch_size",
                                             "enforce_torch_memory_cap_gib", "figures_state")}
        for name, profile in native_3d_provider.RESOURCE_PROFILES.items()}
    state = ("ASSUMED_NOT_MEASURED" if any(p["figures_state"] == "ASSUMED_NOT_MEASURED"
                                            for p in profiles.values()) else "MEASURED")
    smallest = min(profiles.values(), key=lambda p: p["vram_gib"])
    return _hw(state, "GPU; " + " / ".join("%s %g GiB VRAM" % (n, p["vram_gib"]) for n, p in profiles.items())
               + " (figures assumed, not measured)",
               needs_gpu=True, vram_gib=smallest["vram_gib"], host_ram_gib=smallest["host_ram_gib"],
               profiles=profiles, source="argus.core.native_3d_provider.RESOURCE_PROFILES")


def _hw_component(component_id: str) -> dict:
    comp = next((c for c in install_tiers.COMPONENTS if c.id == component_id), None)
    if comp is None:
        return _hw_not_measured()
    note = comp.facts.get("platform_note") or ""
    return _hw("DECLARED_NOT_MEASURED",
               ("GPU required" if comp.needs_gpu else "no GPU declared") + ("; " + note if note else "")
               + " (declared, not measured)",
               needs_gpu=bool(comp.needs_gpu), source="argus.core.install_tiers.COMPONENTS:%s" % component_id,
               platform=note or None, requires_python=comp.facts.get("requires_python"))


def _hw_view(view: dict) -> dict:
    texts = view.get("hardware") or []
    if not texts:
        return _hw_not_measured()
    return _hw("DECLARED_NOT_MEASURED", "; ".join(texts) + " (declared by the capability ledger, not measured)",
               needs_gpu=(False if all(t.lower().startswith("cpu") for t in texts) else None),
               source="argus/capabilities.json")


def _pin(revision: str | None, kind: str, *, components=None, upstream_source=None, note: str | None = None) -> dict:
    return {"revision": revision, "kind": kind, "components": list(components or []),
            "upstream_source": list(upstream_source or []), "note": note}


def _view_pin(view: dict, source_commit: str) -> dict:
    components = [{"name": "VC3D binary (executed revision)", "revision": rev}
                  for rev in view["executed_revisions"]]
    if any("villa-vesuvius" in runtime for runtime in view["runtimes"]):
        components.append({"name": "Villa Python runtime (source pin)", "revision": source_commit})
    if not components:
        return _pin(None, "UNRESOLVED_NO_CALLABLE_LEDGER_ENTRY",
                    note="no callable ledger entry records what revision executes; ledger source pin %s" % source_commit)
    kind = ("VC3D_BINARY_EXECUTED_REVISION" if view["executed_revisions"]
            else "VILLA_PYTHON_RUNTIME_SOURCE_PIN")
    return _pin(components[0]["revision"], kind, components=components,
                note="Villa source pin recorded by the ledger: %s" % source_commit)


def _uniform(*, family: str, lifecycle_state: str, licence_id: str, hardware_profile: dict,
             blockers: list[dict], plan_action: str | None, invoke_action: str | None,
             receipt_schema: str, gate: str, pin: dict, evidence_role: str, claim_ceiling: str,
             plan_available: bool = False) -> dict:
    if family not in FAMILIES:
        raise ValueError("unknown family %r" % (family,))
    if lifecycle_state not in LIFECYCLE_STATES:
        raise ValueError("unknown lifecycle_state %r" % (lifecycle_state,))
    if lifecycle_state in _NEEDS_BLOCKER and not blockers:
        raise ValueError("%s row must name what blocks it" % lifecycle_state)
    if lifecycle_state not in _NEEDS_BLOCKER and blockers:
        raise ValueError("%s row cannot carry a blocker" % lifecycle_state)
    if evidence_role not in CEK.EVIDENCE_ROLES or evidence_role in _FORBIDDEN_ROLES:
        raise ValueError("evidence_role %r is not one a provider row may hold" % (evidence_role,))
    if claim_ceiling not in CLAIM_CEILINGS:
        raise ValueError("unknown claim_ceiling %r" % (claim_ceiling,))
    if family == "ink" and claim_ceiling not in INK_COMPATIBLE_CEILINGS:
        raise ValueError("an ink row may only carry a ceiling that refuses a detector claim")
    if not (licence_id == UNKNOWN or licence_id.startswith(("licence_registry:", "licence_resolver:"))):
        raise ValueError("licence_id must link to a licence entry or be UNKNOWN")
    ordered = sorted(blockers, key=lambda b: _BLOCKER_PRECEDENCE.index(b["kind"]))
    return {
        "row_schema": ROW_SCHEMA, "family": family, "lifecycle_state": lifecycle_state,
        "licence_id": licence_id, "hardware_profile": hardware_profile,
        "blocker": _primary(ordered), "blockers": ordered,
        "plan_action": plan_action, "invoke_action": invoke_action, "plan_available": bool(plan_available),
        "receipt_schema": receipt_schema, "gate": gate, "pin": pin,
        "evidence_role": evidence_role, "claim_ceiling": claim_ceiling,
    }


def _record(*, provider_id: str, stage: str, role: str, revision: str,
            lifecycle: str, source: str, input_contract: str, output_contract: str,
            coordinate_schema: str, license: str, controls: list[str], detail: str,
            family: str, lifecycle_state: str, licence_id: str, hardware_profile: dict,
            blockers: list[dict], plan_action: str | None, invoke_action: str | None,
            receipt_schema: str, gate: str, pin: dict, evidence_role: str, claim_ceiling: str,
            plan_available: bool = False,
            available: bool = False, executable: str | None = None) -> dict:
    row = {
        "id": provider_id,
        "stage": stage,
        "role": role,
        "revision": revision,
        "lifecycle": lifecycle,
        "source": source,
        "input_contract": input_contract,
        "output_contract": output_contract,
        "coordinate_schema": coordinate_schema,
        "license": license,
        "controls": list(controls),
        "detail": detail,
        "available": bool(available),
        "executable": executable,
        "promotion": "operator_only",
    }
    row.update(_uniform(
        family=family, lifecycle_state=lifecycle_state, licence_id=licence_id,
        hardware_profile=hardware_profile, blockers=blockers, plan_action=plan_action,
        invoke_action=invoke_action, receipt_schema=receipt_schema, gate=gate, pin=pin,
        evidence_role=evidence_role, claim_ceiling=claim_ceiling, plan_available=plan_available))
    return row



def _capability_ledger() -> dict:
    """Read the checked-in capability ledger, if present, without treating it as promotion."""
    path = Path(__file__).resolve().parents[2] / "argus" / "capabilities.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"upstream_commit": None, "capabilities": []}
    rows = doc.get("capabilities") if isinstance(doc, dict) else None
    return {"upstream_commit": doc.get("upstream_commit") if isinstance(doc, dict) else None,
            "capabilities": rows if isinstance(rows, list) else []}


def _capability_view(ledger: dict, ids: tuple[str, ...]) -> dict:
    rows = [row for row in ledger["capabilities"]
            if isinstance(row, dict) and row.get("capability_id") in ids]
    callable_rows = [row for row in rows if row.get("runtime") and
                     row.get("state") in {"CALLABLE", "REAL_DATA_PROVEN"}]
    revisions = sorted({m.group(1) for row in callable_rows
                        for m in [_EXEC_REVISION.search(str(row["runtime"]))] if m})
    return {
        "declared": [row.get("capability_id") for row in rows],
        "callable": [row.get("capability_id") for row in callable_rows],
        "runtimes": sorted({str(row["runtime"]).split(" (", 1)[0].strip()
                             for row in callable_rows}),
        "invocation_receipts": sorted({str(row["invocation_receipt"])
                                        for row in callable_rows if row.get("invocation_receipt")}),
        "executed_revisions": revisions,
        "hardware": sorted({str(row["hardware"]) for row in callable_rows if row.get("hardware")}),
    }


def _not_invocable(view: dict, why: str) -> dict:
    """A capability the ledger calls callable that provider.invoke cannot run: kept visible, never offered."""
    return {**view, "callable": [], "ledger_callable": view["callable"], "why": why}


def _upstream_sources() -> list[dict]:
    try:
        from argus.core import upstream_discovery
        return upstream_discovery.source_records()
    except (ImportError, OSError, ValueError, KeyError):
        return []


def upstream_source_provider_ids() -> set[str]:
    """Every provider id config/upstream_sources.json names; each must resolve to a registry row."""
    return {pid for source in _upstream_sources() for pid in source.get("providers", ())}


def _admitted(sources: list[dict], provider_id: str) -> list[dict]:
    return [{"source_id": s.get("source_id"), "admitted_revision": s.get("admitted_revision"),
             "watched_channel": s.get("watched_channel"), "classification": s.get("classification")}
            for s in sources if provider_id in s.get("providers", ())]



def _resolver_licence(repo_id: str) -> str:
    return "licence_resolver:" + repo_id.split("/")[-1]


def _held(row: dict) -> str:
    artifact = row.get("artifact") or {}
    return "%s (sha256 %s)" % (artifact.get("file"), str(artifact.get("sha256"))[:12])


def _candidate_spec(row: dict) -> dict:
    """Uniform fields for one science_candidates row; text is drawn from the row and the modules."""
    cid = row["id"]
    held = bool(row.get("held_locally"))
    door = dict(plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
                plan_available=cid in PLANNABLE_IDS)
    not_adapted = "provider.candidate.invoke refuses EXECUTION_NOT_ADAPTED: ARGUS has no execution adapter for it"
    spec = dict(door, family="ink", licence_id=UNKNOWN, hardware_profile=_hw_not_measured(),
                receipt_schema=CANDIDATE_REFUSAL_SCHEMA, gate="argus.core.provider_actions.invoke",
                evidence_role=_PDE, claim_ceiling="NO_QUALIFIED_DETECTOR")
    if cid.startswith("hecate_"):
        blockers = [] if held else [_blocker("DATA_UNAVAILABLE", "checkpoint %s is not in the local content store" % _held(row))]
        blockers += [
            _blocker("CODE_MISSING", not_adapted),
            _blocker("EXPOSURE", "training exposure is not closed and no clean control has passed")]
        spec.update(lifecycle_state="PLAN_ONLY", blockers=blockers, receipt_schema=hecate_candidate.SCHEMA,
                    gate="argus.core.hecate_candidate.plan")
    elif cid == "ink_3d_dino_guided":
        blockers = [] if held else [_blocker("DATA_UNAVAILABLE", "checkpoint %s is not in the local content store" % _held(row))]
        blockers += [
            _blocker("CODE_MISSING", "%s; the planned route is native_full_3d_ink, which plans and never runs" % not_adapted),
            _blocker("EXPOSURE", "the training exposure of the candidate and its backbone is not closed, so any run "
                                 "tests apparatus only")]
        spec.update(lifecycle_state="PLAN_ONLY", blockers=blockers, receipt_schema=native_3d_provider.SCHEMA,
                    gate="argus.core.native_3d_provider.plan", claim_ceiling="APPARATUS_ONLY",
                    hardware_profile=_hw_native())
    elif cid == "hercunet_v0":
        proof = row.get("operational_proof") or {}
        scientific_state = (row.get("scientific_status") or {}).get("state", "NOT_RECORDED")
        if proof:
            hardware_profile = _hw(
                "MEASURED", "GPU, peak %.2f GiB allocated on one 384^3 cube (%s; not proven on consumer hardware)"
                % (proof["peak_allocated_vram_gib"], proof["device"]), needs_gpu=True,
                vram_gib=proof["peak_allocated_vram_gib"], observations=1,
                consumer_hardware_proven=proof["consumer_hardware_proven"],
                source="argus.core.science_candidates:hercunet_v0.operational_proof")
        else:
            hardware_profile = _hw_not_measured()
        spec.update(
            family="geometry", lifecycle_state="RESEARCH_ONLY", evidence_role=CEK.GEOMETRY_PROVIDER,
            claim_ceiling="CANDIDATE_ONLY",
            blockers=[
                _blocker("LICENCE", row.get("license") or "licence is not recorded in this install"),
                _blocker("EXPOSURE", "training exposure is not closed; any result is development-only"),
                _blocker("CODE_MISSING", "no ARGUS plan or adapter: its scientific state is %s" % scientific_state)],
            hardware_profile=hardware_profile)
    elif cid == "copy_displacement_latest":
        spec.update(
            family="geometry", lifecycle_state="BLOCKED", evidence_role=CEK.GEOMETRY_PROVIDER,
            claim_ceiling="CANDIDATE_ONLY",
            blockers=[
                _blocker("LICENCE", row["license"]),
                _blocker("EXPOSURE", "no documented training exposure"),
                _blocker("DATA_UNAVAILABLE", "the checkpoint was never pulled and has no pinned hash")])
    elif cid in ("unmerge_cli", "surface_geometry_diagnostic"):
        spec.update(
            family="geometry", lifecycle_state="CATALOG_ONLY", evidence_role=CEK.GEOMETRY_PROVIDER,
            claim_ceiling="CANDIDATE_ONLY",
            blockers=[_blocker("CODE_MISSING", "no ARGUS reproduction, plan or adapter exists yet (%s)"
                               % "; ".join(row.get("controls") or ["minimal reproduction pending"]))])
    elif cid == "fiber_ink_4class_selfdistill":
        spec.update(
            lifecycle_state="BLOCKED", licence_id=_resolver_licence(row["official_model"]),
            blockers=[
                _blocker("RUNTIME", "a strict load of this checkpoint on the pinned runtime is not established"),
                _blocker("EXPOSURE", "trained on one scroll and the meaning of its ink class is not "
                                     "verified (%s)" % row["semantic_state"])])
    elif cid in ("dinovol_v2_ps8_supcon3class_step362500", "fiber_dinoguided_2class_step010000"):
        spec.update(
            family="fibers", lifecycle_state="CATALOG_ONLY", claim_ceiling="CANDIDATE_ONLY",
            blockers=[
                _blocker("LICENCE", "no licence tag or file has been read; an undetermined licence is a refusal"),
                _blocker("DATA_UNAVAILABLE", "never downloaded or load-tested (weights not held locally)"),
                _blocker("EXPOSURE", "training exposure is at best the named scroll(s) and the inherited "
                                     "backbone list; no closure exists")])
    elif cid == "icdar2023_grk_papyri_baseline":
        spec.update(
            family="interpretation", lifecycle_state="BLOCKED", claim_ceiling="CANDIDATE_ONLY",
            blockers=[
                _blocker("LICENCE", "dataset is CC BY-NC 4.0 and the trained checkpoint has no declared licence"),
                _blocker("CODE_MISSING", "reading_board cells carry no pixel content, so no accepted glyph can reach "
                                         "this classifier without rendering-export code that does not exist"),
                _blocker("DATA_UNAVAILABLE", "no accepted transcription exists to evaluate against, and the "
                                             "baseline is served through an interactive share link")])
    else:
        raise ValueError("science candidate %r has no registry mapping" % cid)
    return spec


def _candidate_pin(row: dict, sources: list[dict]) -> dict:
    source = str(row.get("source") or "")
    kind = ("UNPINNED_NEVER_DOWNLOADED" if not row.get("revision") else
            "HF_MODEL_REVISION" if source.startswith("hf://") else
            "GITHUB_REPO_REVISION" if source.startswith("github://") else "ARCHIVE_IDENTIFIER")
    components = []
    if row.get("code_revision"):
        components.append({"name": "code_revision", "revision": row["code_revision"]})
    if (row.get("artifact") or {}).get("sha256"):
        components.append({"name": "weights_sha256", "revision": row["artifact"]["sha256"]})
    if row["id"].startswith("hecate_"):
        components.append({"name": "hecate.py git blob sha1", "revision": hecate_candidate.SOURCE_BLOB_SHA1})
    return _pin(row.get("revision"), kind, components=components,
                upstream_source=_admitted(sources, row["id"]))


def _candidate_row(row: dict, sources: list[dict]) -> dict:
    spec = _candidate_spec(row)
    spec["pin"] = _candidate_pin(row, sources)
    row.update(_uniform(**spec))
    return row


def _surface_row(checkpoint_id: str, *, lifecycle: str, blockers: list[dict],
                 hardware: dict, licence_id: str, pin: dict) -> dict:
    entry = surface_prediction_provider.KNOWN_CHECKPOINTS[checkpoint_id]
    reference = entry["reference_pitch_um"]
    return _record(
        provider_id=checkpoint_id, stage="surface_prediction", role="recto_surface_prediction",
        revision=pin["revision"] or "checkpoint-sha256:%s" % entry["checkpoint_sha256"],
        lifecycle=lifecycle, source="hf://%s" % entry["repo_id"],
        input_contract="input domain %r at a declared pitch%s; the checkpoint file must match its pinned sha256"
                       % (entry["input_domain"], (" (reference %.2f um)" % reference) if reference else " (no upstream reference)"),
        output_contract="a hash-bound plan naming the argv for one bounded prediction into a new directory; nothing is run",
        coordinate_schema="declared input domain and spacing; %s" % entry["reference_pitch_evidence"],
        license="see licence_id", controls=["checkpoint sha256", "input domain", "declared spacing", "new output directory"],
        detail="%s scientific state %s: %s" % (entry["repo_id"], entry["scientific_state"], entry["evidence"]),
        family="geometry", lifecycle_state="PLAN_ONLY", licence_id=licence_id, hardware_profile=hardware,
        blockers=blockers, plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
        plan_available=True, receipt_schema=surface_prediction_provider.SCHEMA,
        gate="argus.core.surface_prediction_provider.plan", pin=pin, evidence_role=CEK.GEOMETRY_PROVIDER,
        claim_ceiling=entry["claim_ceiling"])


def inventory() -> dict:
    """Return the provider inventory and the safe default route."""
    ledger = _capability_ledger()
    sources = _upstream_sources()
    geometry_caps = _capability_view(
        ledger, ("vc_tifxyz", "vc_project_tifxyz", "vc_gen_normalgrids",
                 "vc_grow_seg_from_segments", "vesuvius.compute_st",
                 "vesuvius.voxelize_obj", "vc_diffuse_winding"))
    auxiliary_caps = _capability_view(
        ledger, ("vc_project_tifxyz", "vc_gen_normalgrids", "vc_calc_surface_metrics", "vc_ngrids",
                 "vc_fiber_trace_metric", "vesuvius.zarr_tasks"))
    preflight_caps = _capability_view(ledger, ("vesuvius.surface_preflight",))
    flatten_caps = _capability_view(
        ledger, ("vc_flatten", "vc_obj2tifxyz", "vc_tifxyz2obj"))
    render_caps = _capability_view(ledger, ("vc_render_tifxyz", "vc_render_video"))
    prediction_caps = _capability_view(ledger, ("vesuvius.predict",))
    maxflow_caps = _capability_view(ledger, (lasagna_maxflow_provider.CAPABILITY_ID,))
    flatboi_caps = _capability_view(ledger, ("flatboi",))
    geometry_runtime = geometry_caps["runtimes"][0] if geometry_caps["runtimes"] else None
    auxiliary_runtime = auxiliary_caps["runtimes"][0] if auxiliary_caps["runtimes"] else None
    preflight_runtime = preflight_caps["runtimes"][0] if preflight_caps["runtimes"] else None
    flatten_runtime = flatten_caps["runtimes"][0] if flatten_caps["runtimes"] else None
    render_runtime = render_caps["runtimes"][0] if render_caps["runtimes"] else None
    upstream_revision = ledger.get("upstream_commit") or "unknown"
    source_commit = (install_tiers.UPSTREAM_COMMIT
                     if install_tiers.UPSTREAM_COMMIT.startswith(upstream_revision) else upstream_revision)
    blender = paths.tools("blender", "blender-5.2.2-windows-x64", "blender.exe")

    def gated(available: bool, why: str) -> tuple[str, list[dict]]:
        if available:
            return "GATED_EXECUTABLE", []
        return "BLOCKED", [_blocker("RUNTIME", "%s (no callable ledger entry records a runtime that executes)" % why)]

    invoke = dict(plan_action="provider.invoke", invoke_action="provider.invoke",
                  receipt_schema=VILLA_PROVIDER_RECEIPT_SCHEMA,
                  gate="argus.core.action_registry.do_provider_invoke", licence_id=_LICENCE_VC3D,
                  claim_ceiling="OPERATIONAL_ONLY")

    def villa_pin(view: dict, provider_id: str) -> dict:
        pin = _view_pin(view, source_commit)
        pin["upstream_source"] = _admitted(sources, provider_id)
        return pin

    geometry_pin = villa_pin(geometry_caps, "vc3d_geometry_toolchain")
    flatten_pin = villa_pin(flatten_caps, "vc_flatten")
    render_pin = villa_pin(render_caps, "vc_renderer")
    support_pin = villa_pin(auxiliary_caps, "vc3d_support")
    preflight_pin = villa_pin(preflight_caps, "surface_preflight")

    geometry_state, geometry_blockers = gated(bool(geometry_runtime), "geometry toolchain")
    flatten_state, flatten_blockers = gated(bool(flatten_runtime), "vc_flatten")
    render_state, render_blockers = gated(bool(render_runtime), "vc_render_tifxyz")
    support_state, support_blockers = gated(bool(auxiliary_runtime), "VC3D support tools")
    preflight_state, preflight_blockers = gated(bool(preflight_runtime), "surface preflight")
    candidate_rows = [_candidate_row(row, sources) for row in science_candidates.inventory()["candidates"]]
    dino = next(r for r in candidate_rows if r["id"] == native_3d_provider.CANDIDATE_ID)

    recto = official_models.by_id("scrollprize/surface_recto")
    recto_verso = official_models.by_id("scrollprize/surface_recto_verso")
    fiber_hz_vt = official_models.by_id("scrollprize/fiber_hz_vt")
    m7 = surface_prediction_provider.KNOWN_CHECKPOINTS["surface_m7_nnunet"]
    execution_missing = "ARGUS has no execution adapter for it; provider.candidate.invoke refuses EXECUTION_NOT_ADAPTED"

    providers = [
        _record(
            provider_id="raw_ct_acquisition", stage="raw_ct", role="primary",
            revision="argus-acquisition-v1", lifecycle="ADAPTER_READY",
            source="argus.core.volume_acquisition",
            input_contract="governed acquisition packet: URL, array, ROI, phase, identity",
            output_contract="identity-bound local store plus ordered key-list receipt",
            coordinate_schema="OME-Zarr axis order and declared physical pitch",
            license="source and dataset licence must be declared per packet",
            controls=["byte ceiling", "missing chunk refusal", "volume identity", "dry run"],
            detail="Plans and validates acquisition without guessing a store identity.",
            available=True, family="data", lifecycle_state="GATED_EXECUTABLE", licence_id=_LICENCE_CT,
            hardware_profile=_hw("NOT_MEASURED", "not measured; bounded by the acquisition byte ceiling", source=None),
            blockers=[], plan_action="acquire.execute", invoke_action="acquire.execute",
            receipt_schema=volume_acquisition.CONTRACT, gate="argus.core.action_registry.do_acquire_execute",
            pin=_pin("argus-acquisition-v1", "ARGUS_OWNED_CONTRACT",
                     components=[{"name": "acquisition identity schema", "revision": volume_acquisition.ACQUISITION_ID_SCHEMA}]),
            evidence_role=CEK.RAW_MEASURED_INPUT, claim_ceiling="OPERATIONAL_ONLY"),
        _record(
            provider_id="vc3d_bounded_roi_acquisition", stage="raw_ct",
            role="upstream_candidate_not_active",
            revision="30fa0c3a15fdc8d98e347c18a040605862b72302",
            lifecycle="DISCOVERED_UPSTREAM_NOT_BUILT",
            source="Villa: vc_zarr_download_region (#1707, merged to main)",
            input_contract=("explicit VC3D project, remote OME-Zarr URL, level and bounded "
                            "z/y/x region; ARGUS must additionally bind physical scroll, "
                            "exact volume, byte ceiling and destination"),
            output_contract=("bounded chunks in the VC3D cache plus an ARGUS acquisition "
                             "identity receipt; no scientific stage is advanced by download"),
            coordinate_schema="explicit OME-Zarr level and inclusive/exclusive volume bounds",
            license="upstream GPL-3.0 VC3D subprocess; dataset licence remains per acquisition",
            controls=["source identity", "volume identity", "bbox bounds", "dry run",
                      "byte ceiling", "cache destination", "ordered fetched-object receipt"],
            detail=("Upstream merged the bounded region downloader on 2026-09-22. ARGUS tracks "
                    "the exact merge commit but does not expose it as a button yet: the built "
                    "Villa runtime remains pinned before this command, and the ARGUS acquisition "
                    "adapter has not passed compressed-shard, edge-bound and receipt controls."),
            available=False, family="data", lifecycle_state="CATALOG_ONLY",
            licence_id=_LICENCE_VC3D, hardware_profile=_hw_not_measured(),
            blockers=[_blocker("RUNTIME", "the admitted Villa runtime predates vc_zarr_download_region; rebuild and pass the acquisition controls before activation")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=CANDIDATE_REFUSAL_SCHEMA, gate="argus.core.provider_actions.invoke",
            pin=_pin("30fa0c3a15fdc8d98e347c18a040605862b72302",
                     "UPSTREAM_CANDIDATE_NOT_ADMITTED",
                     note="merged upstream revision observed live; not the active runtime pin"),
            evidence_role=CEK.RAW_MEASURED_INPUT, claim_ceiling="CANDIDATE_ONLY"),
        _record(
            provider_id="vc3d_geometry_toolchain", stage="surface_prediction", role="primary",
            revision=geometry_pin["revision"] or "unresolved", lifecycle="UPSTREAM_PINNED_PARTIAL",
            source="Villa: GrowPatch / VC3D / Lasagna",
            input_contract="identity-bound raw CT and declared surface seed",
            output_contract="surface mesh with source receipt and coordinate frame",
            coordinate_schema="volume coordinates with explicit axis/orientation receipt",
            license="upstream project and model terms",
            controls=["identity", "mesh continuity", "jump audit", "human topology review"],
            detail=("Pinned geometry binaries are present in the capability ledger (%s); ARGUS "
                    "can invoke the callable segment-growth boundary with typed receipts; "
                    "surface prediction and human topology review remain scientific gates." %
                    (", ".join(geometry_caps["callable"]) or "no callable entry")),
            available=bool(geometry_runtime), executable=geometry_runtime, family="geometry",
            lifecycle_state=geometry_state, blockers=geometry_blockers, pin=geometry_pin,
            hardware_profile=_hw_view(geometry_caps), evidence_role=CEK.GEOMETRY_PROVIDER, **invoke),
        _record(
            provider_id="vc_flatten", stage="flatten", role="primary",
            revision=flatten_pin["revision"] or "unresolved",
            lifecycle="ADAPTER_READY" if flatten_runtime else "PINNED_ADAPTER_REQUIRED",
            source="Villa: vc_flatten / vc_obj2tifxyz / vc_tifxyz2obj",
            input_contract="reviewed connected surface mesh",
            output_contract="flattened surface coordinates and mapping receipt",
            coordinate_schema="surface UV mapped back to volume xyz",
            license="upstream project and model terms",
            controls=["sheet identity", "coverage", "fold/winding audit", "round-trip sample"],
            detail=("The VC3D flattening binaries are present in the capability ledger (%s); ARGUS has a "
                    "typed provider.invoke boundary for the supported modes, while each real "
                    "output still needs its own receipt and fold/round-trip controls. This is the callable "
                    "provider; flatboi_slim is a separate, pinned-only row." %
                    (", ".join(flatten_caps["callable"]) or "no callable entry")),
            available=bool(flatten_runtime), executable=flatten_runtime, family="geometry",
            lifecycle_state=flatten_state, blockers=flatten_blockers, pin=flatten_pin,
            hardware_profile=_hw_view(flatten_caps), evidence_role=CEK.GEOMETRY_PROVIDER, **invoke),
        _record(
            provider_id="flatboi_slim", stage="flatten", role="pinned_library_never_invoked",
            revision=_view_pin(flatboi_caps, source_commit)["revision"] or "source-pin:%s" % source_commit,
            lifecycle="PINNED_ADAPTER_REQUIRED", source="Villa: flatboi / SLIM (volume-cartographer/libs/flatboi)",
            input_contract="reviewed connected surface mesh",
            output_contract="flattened surface coordinates and mapping receipt",
            coordinate_schema="surface UV mapped back to volume xyz",
            license="upstream project and model terms",
            controls=["sheet identity", "coverage", "fold/winding audit", "round-trip sample"],
            detail=("The ledger row `flatboi` is PINNED only (a CMake target, no runtime, no adapter) and is never "
                    "invoked from ARGUS. ARGUS flattening is the separate `vc_flatten` row."),
            available=False, family="geometry", lifecycle_state="CATALOG_ONLY", licence_id=_LICENCE_VC3D,
            hardware_profile=_hw_not_measured(),
            blockers=[_blocker("CODE_MISSING", "ledger capability `flatboi` is PINNED with no runtime and no ARGUS adapter; "
                                               "nothing in ARGUS invokes it")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=CANDIDATE_REFUSAL_SCHEMA, gate="argus.core.provider_actions.invoke",
            pin=_pin(source_commit, "VILLA_SOURCE_PIN_NOT_BUILT", upstream_source=_admitted(sources, "flatboi_slim"),
                     note="a source pin of the flatboi library; no binary or runtime revision executes it"),
            evidence_role=CEK.GEOMETRY_PROVIDER, claim_ceiling="CANDIDATE_ONLY"),
        _record(
            provider_id="vc_renderer", stage="render", role="primary_surface_renderer",
            revision=render_pin["revision"] or "unresolved",
            lifecycle="ADAPTER_READY" if render_runtime else "PINNED_ADAPTER_REQUIRED",
            source="Villa: vc_render_tifxyz / vc_render_video",
            input_contract="identity-bound TIFXYZ segmentation and OME-Zarr volume",
            output_contract="declared TIFF slice output plus renderer receipt",
            coordinate_schema="volume coordinates, declared group scale and surface normal orientation",
            license="upstream project and model terms",
            controls=["identity", "axis and level", "scale", "negative controls", "output non-degenerate"],
            detail=("ARGUS exposes the pinned renderer through provider.invoke with immutable input/output receipts; "
                    "mechanical real-data controls are recorded, while each new material still needs its own render receipt."),
            available=bool(render_runtime), executable=render_runtime, family="rendering",
            lifecycle_state=render_state, blockers=render_blockers, pin=render_pin,
            hardware_profile=_hw_view(render_caps), evidence_role=CEK.DERIVED_VISUALIZATION, **invoke),
        _record(
            provider_id="vc3d_support", stage="geometry", role="projection_normal_grids_and_metrics",
            revision=support_pin["revision"] or "unresolved",
            lifecycle="ADAPTER_READY" if auxiliary_runtime else "PINNED_ADAPTER_REQUIRED",
            source="Villa: vc_project_tifxyz / vc_gen_normalgrids / vc_ngrids / vc_calc_surface_metrics / vesuvius.zarr_tasks",
            input_contract="identity-bound volume, source surface, patch set and declared umbilicus",
            output_contract="projected surface, normal-grid store or surface-metrics receipt",
            coordinate_schema="declared volume xyz, level, winding and physical spacing",
            license="upstream project and model terms",
            controls=["identity", "axis and level", "winding", "output separation", "metrics receipt"],
            detail=("ARGUS exposes the callable VC3D support tools through typed provider.invoke; "
                    "these outputs remain mechanical inputs or QA artifacts until the scientific "
                    "surface and topology gates pass."),
            available=bool(auxiliary_runtime), executable=auxiliary_runtime, family="geometry",
            lifecycle_state=support_state, blockers=support_blockers, pin=support_pin,
            hardware_profile=_hw_view(auxiliary_caps), evidence_role=CEK.GEOMETRY_PROVIDER, **invoke),
        _record(
            provider_id="surface_preflight", stage="geometry", role="surface_volume_pairing_validator",
            revision=preflight_pin["revision"] or "unresolved",
            lifecycle="ADAPTER_READY" if preflight_runtime else "PINNED_ADAPTER_REQUIRED",
            source="Villa: vesuvius.surface_preflight",
            input_contract="TIFXYZ surface plus local or declared remote OME-Zarr volume",
            output_contract="pairing report with coordinate and CT-support gates",
            coordinate_schema="surface coordinates bound to declared volume axes and level",
            license="upstream project and model terms",
            controls=["identity", "coordinates in volume", "physical margin", "signal support"],
            detail=("ARGUS exposes the real-data-proven surface preflight through typed provider.invoke; "
                    "it validates a pairing and never promotes a surface to a scientific reading."),
            available=bool(preflight_runtime), executable=preflight_runtime, family="geometry",
            lifecycle_state=preflight_state, blockers=preflight_blockers, pin=preflight_pin,
            hardware_profile=_hw_view(preflight_caps), evidence_role=CEK.GEOMETRY_PROVIDER,
            **{**invoke, "licence_id": UNKNOWN}),
        _record(
            provider_id="blender", stage="geometry", role="optional_interchange_and_repair",
            revision="blender-5.2.2", lifecycle="ADAPTER_READY" if blender.is_file() else "NOT_INSTALLED",
            source="Blender headless interchange adapter",
            input_contract="ARGUS mesh interchange artifact",
            output_contract="inspection or conservative repair artifact with differential receipt",
            coordinate_schema="explicit scene transform; no implicit unit conversion",
            license="GPL-3.0-or-later for Blender; ARGUS adapter terms separately",
            controls=["headless smoke", "unit declaration", "no canonical trace", "differential receipt"],
            detail="Optional inspection and repair only; Blender is not the canonical tracer or flattener.",
            available=blender.is_file(), executable=str(blender) if blender.is_file() else None,
            family="geometry", licence_id=UNKNOWN, hardware_profile=_hw_not_measured(),
            lifecycle_state="GATED_EXECUTABLE" if blender.is_file() else "BLOCKED",
            blockers=[] if blender.is_file() else [_blocker("RUNTIME", "the Blender 5.2.2 executable is not installed at the pinned tools path")],
            plan_action="blender.launch", invoke_action="blender.launch",
            receipt_schema=blender_roundtrip.SCHEMA,
            gate="argus.core.action_registry.do_blender_launch",
            pin=_pin("blender-5.2.2", "EXTERNAL_APP_VERSION", note="a desktop application; no immutable revision exists"),
            evidence_role=CEK.GEOMETRY_PROVIDER, claim_ceiling="OPERATIONAL_ONLY"),
        _record(
            provider_id="ink_2d", stage="ink_2d", role="primary_surface_detector",
            revision="argus-pinned-ink2d-v1", lifecycle="RUNS_UNQUALIFIED",
            source="argus.adapters.ink_maps",
            input_contract="certified paired surface render in both depth orientations",
            output_contract="paired 2D ink maps plus detector receipt",
            coordinate_schema="flattened surface pixel coordinates with orientation pair",
            license="checkpoint and label terms must travel with the run",
            controls=["paired orientations", "ink socket", "hard negatives", "cross-scroll gate"],
            detail="The mechanics run; current scientific evidence does not qualify the detector for general reading.",
            family="ink", lifecycle_state="RESEARCH_ONLY", licence_id=UNKNOWN, hardware_profile=_hw_not_measured(),
            blockers=[_blocker("NOT_AUTHORIZED", "no governed action carries ink_2d and no qualified detector exists; "
                                                 "the ink socket gate refuses any reading claim")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=INK_MAPS_SCHEMA, gate="argus.core.ink_socket_gate.decide",
            pin=_pin("argus-pinned-ink2d-v1", "ARGUS_OWNED_PIN", note="checkpoint and label terms travel with each run"),
            evidence_role=_PDE, claim_ceiling="NO_QUALIFIED_DETECTOR"),
        *candidate_rows,
        _record(
            provider_id="lasagna_predict3d", stage="surface_prediction", role="lasagna_predict3d_field_producer",
            revision=lasagna_candidate.CANDIDATE_SHA, lifecycle="PLANNED_REMOTE_ONLY",
            source="Villa: lasagna predict3d (current main, isolated candidate worktree)",
            input_contract="identity-bound OME-Zarr acquisition, one U-Net checkpoint, a new .json output manifest",
            output_contract="seven raw accumulator channels; persisted grad_mag, nx, ny, cos and pred_dt fields",
            coordinate_schema="the input OME-Zarr's own axes; crop given as x,y,z,w,h,d",
            license="see licence_id", controls=["frozen candidate commit", "PR-1752 seven-channel regression",
                                                "new output only", "checkpoint present"],
            detail="A plan can be emitted locally; execution is Linux/GPU work (current Lasagna imports POSIX file locking) "
                   "and has never been run for real, so no real pred_dt field exists.",
            family="geometry", lifecycle_state="PLAN_ONLY", licence_id=UNKNOWN,
            hardware_profile=_hw_component("lasagna"),
            blockers=[
                _blocker("DATA_UNAVAILABLE", "no Lasagna U-Net checkpoint is pinned or held: the plan takes a caller-supplied "
                                             "path and registers no artifact hash"),
                _blocker("RUNTIME", "execution needs an isolated Linux/GPU runtime; the stable Villa runtime and Windows are not providers"),
                _blocker("CODE_MISSING", execution_missing)],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION, plan_available=True,
            receipt_schema=lasagna_candidate.SCHEMA, gate="argus.core.lasagna_candidate.plan",
            pin=_pin(lasagna_candidate.CANDIDATE_SHA, "VILLA_MAIN_CANDIDATE_COMMIT",
                     components=[{"name": "required fix (PR 1752 seven-channel)", "revision": lasagna_candidate.REQUIRED_FIX_SHA},
                                 {"name": "install_tiers lasagna component pin", "revision": "villa@%s" % install_tiers.UPSTREAM_COMMIT}],
                     note="not the runtime pin (739eefd) and not the VC3D binary (49966df)"),
            evidence_role=CEK.GEOMETRY_PROVIDER, claim_ceiling="CANDIDATE_ONLY"),
        _record(
            provider_id="lasagna_maxflow", stage="multi_sheet_fit", role="lasagna_maxflow_graph_builder",
            revision=_view_pin(maxflow_caps, source_commit)["revision"] or "unresolved",
            lifecycle="PLAN_RUN_BLOCKED", source="Villa: vc_lasagna_maxflow_graph (VC3D binary)",
            input_contract="a Lasagna manifest whose pred_dt group is uint8, plus repeated --src/--sink points and a source binding",
            output_contract="a graph-build report parsed from stdout into an append-only receipt",
            coordinate_schema="the manifest's base coordinate space",
            license="see licence_id",
            controls=["manifest pred_dt precondition reproduced from upstream C++", "typed argv", "source binding",
                      "nonzero exit refusal"],
            detail=lasagna_maxflow_provider.SCIENTIFIC_BOUNDARY,
            family="geometry", lifecycle_state="PLAN_ONLY", licence_id=_LICENCE_VC3D,
            hardware_profile=_hw("NOT_MEASURED", "not measured; CPU graph construction only in the pinned build", source=None),
            blockers=[
                _blocker("DATA_UNAVAILABLE", "no real pred_dt field exists locally; its only producer is lasagna_predict3d, never run"),
                _blocker("RUNTIME", "the pinned Windows VC3D build has neither ECL-MaxFlow (CUDA) nor AMGX compiled in, so no solve can run"),
                _blocker("NOT_AUTHORIZED", "run_lasagna exists but no governed action authorizes it; "
                                           "provider.candidate.invoke refuses NOT_AUTHORIZED")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION, plan_available=True,
            receipt_schema=lasagna_maxflow_provider.SCHEMA_PLAN,
            gate="argus.core.lasagna_maxflow_provider.plan_lasagna",
            pin=_view_pin(maxflow_caps, source_commit), evidence_role=CEK.GEOMETRY_PROVIDER,
            claim_ceiling="OPERATIONAL_ONLY"),
        _surface_row(
            "surface_m7_nnunet", lifecycle="PLAN_ONLY_NON_PROMOTABLE",
            blockers=[
                _blocker("CODE_MISSING", "the plan's argv is argus_vesuvius.cli curated-surface-baseline; %s" % execution_missing),
                _blocker("EXPOSURE", "the checkpoint fingerprint describes 786 training volumes with no published sample "
                                     "identifiers or split manifest, so exposure is unknown")],
            hardware=_hw_not_measured(), licence_id=UNKNOWN,
            pin=_pin("checkpoint-sha256:%s" % m7["checkpoint_sha256"], "OBSERVED_LOCAL_CHECKPOINT_SHA256",
                     upstream_source=_admitted(sources, "surface_m7_nnunet"),
                     note=m7["checkpoint_sha256_provenance"])),
        _surface_row(
            "surface_recto", lifecycle="PLAN_ONLY_MECHANICS",
            blockers=[_blocker("CODE_MISSING", "vesuvius.predict is reachable only through provider.invoke with its own typed "
                                               "options; %s" % execution_missing)],
            hardware=_hw_floors("surface_recto"), licence_id=_resolver_licence(recto.repo_id),
            pin=_pin(recto.revision, "HF_MODEL_REVISION", upstream_source=_admitted(sources, "surface_recto"),
                     components=[{"name": "weights_sha256", "revision": recto.weight_sha256}],
                     note="semantic state %s" % recto.semantic_state)),
        _record(
            provider_id="surface_recto_verso", stage="surface_prediction", role="multiclass_surface_model",
            revision=recto_verso.revision, lifecycle="CATALOGUED_PINNED", source="hf://%s" % recto_verso.repo_id,
            input_contract="CT volume (channel T2) for a nnU-Net run", output_contract="background / surface1 / surface2 / intersection",
            coordinate_schema="the input volume's own axes; class meanings are declared by the checkpoint only",
            license="see licence_id", controls=["all four classes preserved", "known-side control before any face mapping"],
            detail="Pinned by revision and weight hash. %s" % recto_verso.what_it_does_not_establish,
            family="geometry", lifecycle_state="CATALOG_ONLY", licence_id=_resolver_licence(recto_verso.repo_id),
            hardware_profile=_hw_floors("surface_recto_verso"),
            blockers=[_blocker("CODE_MISSING", "no ARGUS plan or execution adapter binds this checkpoint")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=CANDIDATE_REFUSAL_SCHEMA, gate="argus.core.provider_actions.invoke",
            pin=_pin(recto_verso.revision, "HF_MODEL_REVISION",
                     components=[{"name": "weights_sha256", "revision": recto_verso.weight_sha256}],
                     note="semantic state %s" % recto_verso.semantic_state),
            evidence_role=CEK.GEOMETRY_PROVIDER, claim_ceiling="MECHANICS_ONLY"),
        _record(
            provider_id="fiber_hz_vt", stage="fiber_segmentation", role="fiber_orientation_cue",
            revision=fiber_hz_vt.revision, lifecycle="CATALOGUED_PINNED", source="hf://%s" % fiber_hz_vt.repo_id,
            input_contract="CT volume (channel T2) for a nnU-Net run", output_contract="background / vt-fiber / hz-fiber / intersection",
            coordinate_schema="the input volume's own axes", license="see licence_id",
            controls=["fibre direction is a cue, never a face label", "all four classes preserved"],
            detail="Pinned by revision and weight hash. %s" % fiber_hz_vt.what_it_does_not_establish,
            family="fibers", lifecycle_state="CATALOG_ONLY", licence_id=_resolver_licence(fiber_hz_vt.repo_id),
            hardware_profile=_hw_floors("fiber_hz_vt"),
            blockers=[_blocker("CODE_MISSING", "no ARGUS plan or execution adapter binds this checkpoint")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=CANDIDATE_REFUSAL_SCHEMA, gate="argus.core.provider_actions.invoke",
            pin=_pin(fiber_hz_vt.revision, "HF_MODEL_REVISION",
                     components=[{"name": "weights_sha256", "revision": fiber_hz_vt.weight_sha256}],
                     note="semantic state %s" % fiber_hz_vt.semantic_state),
            evidence_role=_PDE, claim_ceiling="MECHANICS_ONLY"),
        _record(
            provider_id="native_full_3d_ink", stage="native_full_3d_ink", role="tifxyz_conditioned_full_3d_ink_route",
            revision=native_3d_provider.UPSTREAM["inspected_main_commit"], lifecycle="PLAN_ONLY_EXPOSED_APPARATUS",
            source="Villa: vesuvius.ink_detection.inference.infer_full3d_tifxyz",
            input_contract="a tifxyz directory (%s plus %s naming the exact eligible volume), one checkpoint and a bounded target bbox"
                           % (", ".join(native_3d_provider.TIFXYZ_REQUIRED), native_3d_provider.VOLUME_SOURCE_FILE),
            output_contract="a hash-bound plan carrying argv_plan_only and argv_execute; nothing is run",
            coordinate_schema="tifxyz surface coordinates bound to the exact eligible volume at level 0",
            license="see licence_id", controls=list(native_3d_provider.REQUIRED_CONTROLS[:4]),
            detail="%s Known open upstream defects (a watch list, none assumed fixed): %s."
                   % (native_3d_provider.CLAIM_CEILING_EXPOSED, "; ".join(native_3d_provider.UPSTREAM["known_open_defects"])),
            family="ink", lifecycle_state="PLAN_ONLY", licence_id=UNKNOWN, hardware_profile=_hw_native(),
            blockers=[
                _blocker("CODE_MISSING", "the plan is execution_authorized=False and %s" % execution_missing),
                _blocker("DATA_UNAVAILABLE", "no chunk acquisition has run" if dino["held_locally"]
                         else "the checkpoint is not held locally and no chunk acquisition has run"),
                _blocker("EXPOSURE", "qualification is refused (EXPOSED_FOR_QUALIFICATION) on every scroll whose exposure is not CLEAN_CLOSED")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION, plan_available=True,
            receipt_schema=native_3d_provider.SCHEMA, gate="argus.core.native_3d_provider.plan",
            pin=_pin(native_3d_provider.UPSTREAM["inspected_main_commit"], "VILLA_MAIN_INSPECTED_COMMIT",
                     components=[{"name": "direct 3D landing commit", "revision": native_3d_provider.UPSTREAM["direct_3d_landing_commit"]},
                                 {"name": "Villa runtime pin (upstream.lock.json)", "revision": install_tiers.UPSTREAM_COMMIT},
                                 {"name": native_3d_provider.CANDIDATE_ID, "revision": dino["revision"]}],
                     upstream_source=_admitted(sources, "native_full_3d_ink"),
                     note="the inspected main is not the runtime pin; the native files were verified present at both"),
            evidence_role=_PDE, claim_ceiling="APPARATUS_ONLY"),
        _record(
            provider_id="inksurf_audit", stage="ink_audit", role="external_claim_audit_provider",
            revision=(_admitted(sources, "inksurf_audit") or [{}])[0].get("admitted_revision") or "unpinned",
            lifecycle="EXTERNAL_PROVIDER_RECEIPT_ONLY", source=inksurf_provider.UPSTREAM_URL,
            input_contract="one InkSurf claim-audit report plus the exact upstream commit and author handle",
            output_contract="an attributed provider receipt copying InkSurf's own verdict verbatim",
            coordinate_schema="none; it audits a claim, it does not localize ink",
            license="third-party MIT; not an official ScrollPrize tool", controls=list(inksurf_provider.STAGE4_LICENCES),
            detail="Another team's failure-diagnostic software (upstream status %s). It is not an ARGUS detector and "
                   "supplies none of Stage 4's five licences." % inksurf_provider.UPSTREAM_STATUS_EXPECTED,
            family="ink", lifecycle_state="RESEARCH_ONLY", licence_id=UNKNOWN, hardware_profile=_hw_not_measured(),
            blockers=[_blocker("NOT_AUTHORIZED", "third-party tool, not an ARGUS detector: ARGUS only receipts one of its reports "
                                                 "and caps the claim at %s; it cannot certify a candidate" % inksurf_provider.ARGUS_CLAIM_CAP)],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=inksurf_provider.SCHEMA, gate="argus.adapters.inksurf_provider.build_receipt",
            pin=_pin((_admitted(sources, "inksurf_audit") or [{}])[0].get("admitted_revision"), "GITHUB_REPO_REVISION",
                     upstream_source=_admitted(sources, "inksurf_audit")),
            evidence_role=_PDE, claim_ceiling=inksurf_provider.ARGUS_CLAIM_CAP),
        _record(
            provider_id=pseudolabel_controls.PROVIDER_ID, stage="ink_pseudolabel_research", role="pseudolabel_controls",
            revision="argus-pseudolabel-controls-v1", lifecycle=pseudolabel_controls.LIFECYCLE,
            source="argus.core.pseudolabel_controls",
            input_contract="teacher and student outputs, sample lineages and cross-scroll scores",
            output_contract="collapse, leakage and cross-scroll control reports; never a label",
            coordinate_schema="none; the controls are CPU-only and coordinate-free",
            license="ARGUS-owned controls; no model or label is bundled",
            controls=list(pseudolabel_controls.PROMOTION_REQUIREMENTS),
            detail=pseudolabel_controls.provider_record()["notes"],
            family="ink", lifecycle_state="RESEARCH_ONLY", licence_id=UNKNOWN,
            hardware_profile=_hw("DECLARED_NOT_MEASURED", "controls are CPU-only; the teacher/student model needs a GPU (declared, not measured)",
                                 needs_gpu=True, source="argus.core.pseudolabel_controls.provider_record"),
            blockers=[
                _blocker("DATA_UNAVAILABLE", pseudolabel_controls.provider_record()["runnable_requirements"][0]),
                _blocker("NOT_AUTHORIZED", "promotion is a human decision; this module tops out at %s" % pseudolabel_controls.STATUS_ELIGIBLE)],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=CANDIDATE_REFUSAL_SCHEMA, gate="argus.core.pseudolabel_controls.may_be_production",
            pin=_pin("argus-pseudolabel-controls-v1", "ARGUS_OWNED_PIN"),
            evidence_role=_PDE, claim_ceiling="NO_QUALIFIED_DETECTOR"),
        _record(
            provider_id="spiral_fit", stage="surface_fit", role="spiral_surface_fitter",
            revision=install_tiers.UPSTREAM_COMMIT, lifecycle="PINNED_CONTROLS_ONLY",
            source="Villa: spiral-fitting", input_contract="a declared umbilicus, CT and fibre supervision in its own environment",
            output_contract="a fitted spiral surface with nested winding sheets",
            coordinate_schema="the fit's own level-scaled voxel frame; area metadata must be verified",
            license="see licence_id", controls=["geometric", "topological", "holdout"],
            detail="ARGUS holds geometric, topological and holdout controls for a fitted spiral (spiral_fit_controls) but cannot run "
                   "the fit.",
            family="geometry", lifecycle_state="CATALOG_ONLY", licence_id=UNKNOWN,
            hardware_profile=_hw_component("spiral_fitting"),
            blockers=[
                _blocker("CODE_MISSING", "no ARGUS plan or adapter runs fit_spiral; only the controls exist (argus.core.spiral_fit_controls)"),
                _blocker("RUNTIME", "needs Python >=3.14, torch below 2.13, a GPU and Linux or WSL in its own environment")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=CANDIDATE_REFUSAL_SCHEMA, gate="argus.core.spiral_fit_controls.evaluate_spiral_fit",
            pin=_pin(install_tiers.UPSTREAM_COMMIT, "VILLA_SOURCE_PIN_NOT_BUILT",
                     note="the install_tiers component pin; a newer candidate commit is wired but not promoted"),
            evidence_role=CEK.GEOMETRY_PROVIDER, claim_ceiling="CANDIDATE_ONLY"),
        _record(
            provider_id="transcription_review", stage="transcription", role="human_gated",
            revision="argus-reading-board-v1", lifecycle="HUMAN_GATED",
            source="argus.core.reading_board / text_assembly",
            input_contract="independently accepted glyph evidence and uncertainty",
            output_contract="reviewed glyph sequence with provenance links",
            coordinate_schema="glyph IDs and source surface coordinates",
            license="source evidence and editorial terms",
            controls=["independent acceptance", "uncertainty", "no auto-certification"],
            detail="ARGUS can assemble accepted glyph evidence; it does not invent unread text.",
            family="interpretation", lifecycle_state="EXECUTABLE", licence_id=UNKNOWN, hardware_profile=_hw_not_measured(),
            blockers=[], plan_action=None, invoke_action=None, receipt_schema=reading_board.CONTRACT_ID,
            gate="argus.core.reading_board.claim_transcription",
            pin=_pin("argus-reading-board-v1", "ARGUS_OWNED_CONTRACT"),
            evidence_role=CEK.HUMAN_JUDGMENT, claim_ceiling="HUMAN_GATED"),
        _record(
            provider_id="translation_review", stage="translation", role="human_gated",
            revision="argus-translation-review-v1", lifecycle="HUMAN_GATED",
            source="ARGUS translation boundary",
            input_contract="accepted transcription with language and provenance context",
            output_contract="translation candidate with source-to-phrase links and uncertainty",
            coordinate_schema="text span to accepted glyph IDs",
            license="corpus and translation-model terms",
            controls=["accepted-input-only", "language identification", "human review", "citation"],
            detail="A provenance-bound translation worksheet is available; automatic translation "
                   "is not promoted and every draft remains human reviewed.",
            family="interpretation", lifecycle_state="PLAN_ONLY", licence_id=UNKNOWN, hardware_profile=_hw_not_measured(),
            blockers=[_blocker("NOT_AUTHORIZED", "automatic translation is not promoted; only the worksheet exists and every draft is human reviewed")],
            plan_action=CANDIDATE_PLAN_ACTION, invoke_action=CANDIDATE_INVOKE_ACTION,
            receipt_schema=translation_plan.SCHEMA, gate="argus.core.translation_plan.build",
            pin=_pin("argus-translation-review-v1", "ARGUS_OWNED_CONTRACT"),
            evidence_role=CEK.HUMAN_JUDGMENT, claim_ceiling="HUMAN_GATED"),
    ]
    by_state = {state: sum(p["lifecycle_state"] == state for p in providers) for state in LIFECYCLE_STATES}
    return {
        "schema": SCHEMA,
        "generated_by": "argus.core.provider_registry.inventory",
        "read_only": True,
        "providers": providers,
        "counts": {
            "total": len(providers),
            "available": sum(p["available"] for p in providers),
            "adapter_ready": sum("ADAPTER_READY" in p["lifecycle"] for p in providers),
            "qualified": sum(p["lifecycle"] == "QUALIFIED" for p in providers),
            "operator_promotion_required": len(providers),
            "by_lifecycle_state": by_state,
            "blocked_or_unreachable": sum(p["blocker"] is not None for p in providers),
        },
        "lifecycle_states": dict(LIFECYCLE_STATES),
        "blocker_kinds": list(BLOCKER_KINDS),
        "families": list(FAMILIES),
        "claim_ceilings": dict(CLAIM_CEILINGS),
        "capability_ledger": {
            "upstream_commit": upstream_revision,
            "geometry": geometry_caps,
            "flatten": flatten_caps,
            "render": render_caps,
            "auxiliary": auxiliary_caps,
            "surface_preflight": preflight_caps,
            "prediction": prediction_caps,
            "lasagna_maxflow": _not_invocable(
                maxflow_caps, "vc_lasagna_maxflow_graph has no single input/output path and cannot run through "
                              "provider.invoke; see the lasagna_maxflow row and lasagna_maxflow_provider"),
            "note": "ledger availability is mechanical evidence, not adapter integration or scientific qualification",
        },
        "replacement_policy": {
            "automatic": ["discover", "pin", "stage", "run controls", "write differential receipt"],
            "operator_only": "promote a candidate after reviewing the differential receipt",
            "identity_rule": "existing runs retain their provider revision and coordinates",
        },
    }


def record(provider_id: str) -> dict | None:
    """One provider's uniform record, or None when no row has that id."""
    return next((p for p in inventory()["providers"] if p["id"] == provider_id), None)


def resolve(stage: str, *, scroll: str | None = None, provider_id: str | None = None) -> dict:
    """Resolve a stage without silently selecting an unsafe provider."""
    rows = inventory()["providers"]
    candidates = [p for p in rows if p["stage"] == stage or stage in p["stage"]]
    if provider_id:
        candidates = [p for p in candidates if p["id"] == provider_id]
    if not candidates:
        return {"schema": SCHEMA, "verdict": "NO_MATCH", "stage": stage, "scroll": scroll,
                "why": "no provider declares this stage; nothing was substituted", "candidates": []}
    usable = [p for p in candidates if p["lifecycle"] in {"QUALIFIED", "ADAPTER_READY"}]
    if len(usable) != 1:
        return {
            "schema": SCHEMA, "verdict": "QUALIFICATION_REQUIRED", "stage": stage,
            "scroll": scroll, "candidates": candidates,
            "why": ("a provider is present but no single qualified provider may be selected "
                    "for this material; review controls and promote explicitly"),
        }
    return {"schema": SCHEMA, "verdict": "RESOLVED", "stage": stage, "scroll": scroll,
            "provider": usable[0], "why": "resolved by the declared adapter contract"}


def fingerprint(payload: dict) -> str:
    """Stable digest for a plan, useful in receipts and differential comparisons."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    default=str).encode("utf-8")).hexdigest()
