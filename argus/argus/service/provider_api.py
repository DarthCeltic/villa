"""Read-only provider and retrieval planning routes."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/api/providers")
async def providers():
    from argus.core import provider_registry as registry

    return await asyncio.to_thread(registry.inventory)


@router.get("/api/providers/resolve")
async def resolve_provider(stage: str, scroll: str | None = None,
                           provider: str | None = None):
    from argus.core import provider_registry as registry

    return await asyncio.to_thread(registry.resolve, stage, scroll=scroll,
                                   provider_id=provider)


@router.get("/api/providers/plan")
async def provider_plan(capability_id: str | None = None, input_path: str | None = None,
                        output_path: str | None = None, physical_scroll: str | None = None,
                        volume_id: str | None = None, acquisition_id: str | None = None,
                        options_json: str = Query("{}")):
    """Build one generic, zero-execution provider plan for the operator surface."""
    required = [key for key, value in (
        ("capability_id", capability_id), ("input_path", input_path),
        ("output_path", output_path), ("physical_scroll", physical_scroll),
        ("volume_id", volume_id), ("acquisition_id", acquisition_id))
        if value in (None, "")]
    if required:
        return {"schema": "argus-villa-provider-plan-v1", "state": "INPUT_REQUIRED",
                "read_only": True, "requests_made": 0, "bytes_fetched": 0,
                "required": required,
                "why": "name the exact registered capability, existing input, new output and material binding"}
    try:
        options = json.loads(options_json or "{}")
    except (TypeError, ValueError):
        return {"schema": "argus-villa-provider-plan-v1", "state": "REFUSED",
                "read_only": True, "requests_made": 0, "bytes_fetched": 0,
                "why": "options_json must be a JSON object"}
    if not isinstance(options, dict):
        return {"schema": "argus-villa-provider-plan-v1", "state": "REFUSED",
                "read_only": True, "requests_made": 0, "bytes_fetched": 0,
                "why": "options_json must be a JSON object"}
    from argus.core import paths as _paths
    from argus.core import villa_provider_adapter as adapter

    outside = _paths.outside_served_roots(input_path=input_path, output_path=output_path)
    if outside:
        return {"schema": "argus-villa-provider-plan-v1", "state": "REFUSED", "read_only": True, "requests_made": 0, "bytes_fetched": 0,
                "why": "%s is outside ARGUS's declared roots; this read-only route only plans inside them" % outside}

    try:
        with adapter.confined_to_served_roots():
            plan = await asyncio.to_thread(
                adapter.plan,
                capability_id=capability_id,
                input_path=input_path,
                output_path=output_path,
                source_binding={"physical_scroll": physical_scroll, "volume_id": volume_id,
                                "acquisition_id": acquisition_id},
                options=options,
            )
    except adapter.ProviderRefusal as exc:
        return {"schema": "argus-villa-provider-plan-v1", "state": "REFUSED",
                "read_only": True, "requests_made": 0, "bytes_fetched": 0,
                "why": str(exc)}
    return {"schema": "argus-villa-provider-plan-v1", "state": "PLANNED",
            "read_only": True, "requests_made": 0, "bytes_fetched": 0,
            "plan": plan, "plan_sha256": plan["plan_sha256"],
            "execution": "separate governed provider.invoke action; this route never runs the tool"}


@router.get("/api/providers/surface-preflight/plan")
async def surface_preflight_plan(surface: str, volume: str, output: str,
                                  physical_scroll: str, volume_id: str, acquisition_id: str,
                                  array_key: str | None = None, margin: float | None = None,
                                  max_samples: int | None = None,
                                  minimum_support_fraction: float | None = None,
                                  support_threshold: float | None = None):
    """Build the read-only surface/volume pairing plan used before surface consumption."""
    from argus.core import villa_provider_adapter as adapter

    from argus.core import paths as _paths

    outside = _paths.outside_served_roots(url_labels=("volume",), surface=surface, output=output, volume=volume)
    if outside:
        return {"schema": "argus-surface-preflight-plan-v1", "state": "REFUSED", "read_only": True, "requests_made": 0, "bytes_fetched": 0,
                "why": "%s is outside ARGUS's declared roots; this read-only route only plans inside them" % outside}
    options = {"volume_path": volume}
    for key, value in (("array_key", array_key), ("margin", margin), ("max_samples", max_samples),
                       ("minimum_support_fraction", minimum_support_fraction),
                       ("support_threshold", support_threshold)):
        if value is not None:
            options[key] = value
    try:
        with adapter.confined_to_served_roots():
            plan = await asyncio.to_thread(
                adapter.plan, capability_id="vesuvius.surface_preflight", input_path=surface,
                output_path=output,
                source_binding={"physical_scroll": physical_scroll, "volume_id": volume_id,
                                "acquisition_id": acquisition_id},
                options=options)
    except adapter.ProviderRefusal as exc:
        return {"schema": "argus-surface-preflight-plan-v1", "state": "REFUSED",
                "read_only": True, "why": str(exc), "requests_made": 0,
                "bytes_fetched": 0}
    return {"schema": "argus-surface-preflight-plan-v1", "state": "PLANNED",
            "read_only": True, "requests_made": 0, "bytes_fetched": 0,
            "plan": plan, "plan_sha256": plan["plan_sha256"],
            "execution": "separate governed provider.invoke action; this route never runs the tool"}


@router.get("/api/providers/lasagna/plan")
async def lasagna_plan(scroll: str, acquisition_id: str, input_zarr: str,
                        checkpoint: str, output_manifest: str,
                        crop_xyzwhd: str | None = None, device: str = "cuda"):
    """Build a frozen current-Villa predict3d job; never start it from this GET."""
    from argus.core import lasagna_candidate as lasagna
    from argus.core import paths as _paths

    outside = _paths.outside_served_roots(url_labels=("input_zarr",), input_zarr=input_zarr, checkpoint=checkpoint, output_manifest=output_manifest)
    if outside:
        return {"schema": lasagna.SCHEMA, "state": "REFUSED", "read_only": True,
                "why": "%s is outside ARGUS's declared roots; this read-only route only plans inside them" % outside}
    crop = None
    if crop_xyzwhd:
        try:
            crop = [int(value.strip()) for value in crop_xyzwhd.split(",")]
        except ValueError:
            return {"schema": lasagna.SCHEMA, "state": "REFUSED", "read_only": True,
                    "why": "crop_xyzwhd must be six comma-separated integers"}
    try:
        return await asyncio.to_thread(
            lasagna.plan, scroll=scroll, acquisition_id=acquisition_id,
            input_zarr=input_zarr, checkpoint=checkpoint, output_manifest=output_manifest,
            crop_xyzwhd=crop, device=device)
    except lasagna.LasagnaPlanRefusal as exc:
        return {"schema": lasagna.SCHEMA, "state": "REFUSED", "read_only": True,
                "why": str(exc)}


@router.get("/api/providers/hecate/plan")
async def hecate_plan(scroll: str, acquisition_id: str, input_render: str,
                      spacing_um: float, source_script: str, checkpoint: str,
                      python_executable: str,
                      output_png: str | None = None, output_3d: str | None = None,
                      array: str = "0", device: str = "cuda", reverse: bool = False,
                      batch_size: int = 1, stride: int | None = None,
                      precision: str = "fp32"):
    """Build an immutable Hecate invocation plan; never download or execute from this GET."""
    from argus.core import hecate_candidate as hecate
    from argus.core import paths as _paths

    outside = _paths.outside_served_roots(input_render=input_render, checkpoint=checkpoint, output_png=output_png, output_3d=output_3d)
    remote = _paths.is_remote_path(source_script) or _paths.is_remote_path(python_executable)
    if remote:
        outside = outside or "source_script/python_executable"
    if outside:
        return {"schema": hecate.SCHEMA, "state": "REFUSED", "read_only": True,
                "why": "%s is outside ARGUS's declared roots; this read-only route only plans inside them" % outside}
    try:
        return await asyncio.to_thread(
            hecate.plan, scroll=scroll, acquisition_id=acquisition_id,
            input_render=input_render, spacing_um=spacing_um, source_script=source_script,
            checkpoint=checkpoint, python_executable=python_executable,
            output_png=output_png, output_3d=output_3d,
            array=array, device=device, reverse=reverse, batch_size=batch_size,
            stride=stride, precision=precision)
    except (hecate.HecatePlanRefusal, OSError) as exc:
        return {"schema": hecate.SCHEMA, "state": "REFUSED", "read_only": True,
                "why": str(exc)}


@router.get("/api/providers/blender/plan")
async def blender_launch_plan(mesh_path: str, mode: str, output_path: str | None = None,
                              report_path: str | None = None):
    """Build the exact Blender command a launch would run."""
    from argus.core import blender_roundtrip as blender
    from argus.core import paths as _paths

    outside = _paths.outside_served_roots(mesh_path=mesh_path, output_path=output_path, report_path=report_path)
    if outside:
        return {"schema": "argus-blender-launch-preview-v1", "read_only": True, "requests_made": 0, "bytes_fetched": 0,
                "plan": {"read_only": True, "ready": False, "mode": mode, "argv": None, "blender_executable": None,
                         "problems": ["%s is outside ARGUS's declared roots; this read-only route only plans inside them" % outside]},
                "execution": "separate governed blender.launch action; this route never starts Blender"}
    plan = await asyncio.to_thread(blender.plan_launch, mesh_path, mode,
                                   output_path=output_path, report_path=report_path)
    return {"schema": "argus-blender-launch-preview-v1", "read_only": True,
            "requests_made": 0, "bytes_fetched": 0, "plan": plan,
            "execution": "separate governed blender.launch action; this route never starts Blender"}


@router.get("/api/profile/plan")
async def profile_plan(scroll: str, segment: str | None = None,
                       receipt_path: str | None = None):
    """Resolve one exact profile receipt into a bounded recipe handoff."""
    from argus.core import paths as _paths
    from argus.core import profile_plan as planner

    if _paths.is_remote_path(receipt_path or ""):
        return {"state": "REFUSED", "why": "receipt_path may not be a network (UNC) path"}
    return await asyncio.to_thread(planner.build, scroll=scroll, segment=segment,
                                   receipt_path=receipt_path)


@router.get("/api/translation/plan")
async def translation_plan(language: str | None = None, target: str | None = None):
    """Expose the human-gated downstream handoff without accepting text from query strings."""
    from argus.core import translation_plan as planner

    if target:
        from fastapi import HTTPException

        from argus.core import interpretation_actions as IA
        from argus.core import paths

        d = paths.resolve_ui_target_dir(target)
        if d is None:
            raise HTTPException(404, "unknown target %r" % target)
        out = await asyncio.to_thread(IA.translation_state, d, language=language)
        out["target"] = target
        return out
    return await asyncio.to_thread(planner.build, language=language)


@router.get("/api/retrieval/plan")
async def retrieval_plan(scroll: str | None = None, url: str | None = None,
                        phase: str = "A0", array_path: str = "", roi: str | None = None,
                        volume_id: str | None = None, byte_ceiling: int | None = None):
    """Prepare an identity-bound acquisition plan without making a request."""
    from argus.core import provider_registry as registry
    from argus.core import paths
    from argus.core import volume_acquisition as acquisition

    destination = str(paths.science_data("acquisitions"))

    if not url:
        return {
            "schema": "argus-retrieval-plan-v1",
            "state": "INPUT_REQUIRED",
            "scroll": scroll,
            "read_only": True,
            "requests_made": 0,
            "bytes_fetched": 0,
            "destination": destination,
            "required": ["official mirror URL", "phase", "volume identity"],
            "why": "no acquisition is planned until the operator names the exact volume",
        }
    try:
        plan = await asyncio.to_thread(
            acquisition.dry_run,
            url=url, array_path=array_path, roi=roi, phase=phase,
            scroll=scroll, volume_id=volume_id, byte_ceiling=byte_ceiling,
        )
    except acquisition.AcquisitionRefusal as exc:
        return {
            "schema": "argus-retrieval-plan-v1", "state": "REFUSED", "read_only": True,
            "scroll": scroll, "requests_made": 0, "bytes_fetched": 0,
            "destination": destination,
            "why": str(exc), "next": "satisfy the exact packet requirement and plan again",
        }
    return {
        "schema": "argus-retrieval-plan-v1", "state": "PLANNED", "read_only": True,
        "scroll": scroll, "requests_made": 0, "bytes_fetched": 0,
        "destination": destination,
        "provider": registry.resolve("raw_ct", scroll=scroll),
        "plan": plan,
        "plan_sha256": registry.fingerprint(plan),
        "execution": "separate governed command service; this route never fetches",
    }


@router.get("/api/providers/{provider_id}")
async def provider_record(provider_id: str):
    """One provider's uniform record: lifecycle state, blocker, pin, hardware, gate and doors."""
    from argus.core import provider_registry as registry

    row = await asyncio.to_thread(registry.record, provider_id)
    if row is None:
        known = [p["id"] for p in (await asyncio.to_thread(registry.inventory))["providers"]]
        raise HTTPException(status_code=404, detail={
            "error": "unknown provider", "provider_id": provider_id, "known": known,
            "why": "no registry row has this id; nothing was substituted"})
    return {"schema": registry.ROW_SCHEMA, "read_only": True, "provider": row}
