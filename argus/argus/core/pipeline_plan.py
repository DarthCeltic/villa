"""Compose the one ARGUS route from stage contracts and provider declarations."""
from __future__ import annotations

from argus.core import process_contract, provider_registry

CONTRACT = "argus-pipeline-plan-v1"

PROVIDER_FOR_STAGE = {
    "raw_ct": "raw_ct_acquisition",
    "surface_prediction": "vc3d_geometry_toolchain",
    "mesh_tracing": "vc3d_geometry_toolchain",
    "topology_repair": "vc3d_geometry_toolchain",
    "flatten": "vc_flatten",
    "ink_2d": "ink_2d",
    "transcription": "transcription_review",
    "translation": "translation_review",
}


def build(scroll: str | None = None) -> dict:
    contract = process_contract.derive(scroll=scroll)
    registry = provider_registry.inventory()
    registry_by_id = {p["id"]: p for p in registry["providers"]}
    rows = []
    first_gap = None
    for stage in contract["stages"]:
        provider_id = PROVIDER_FOR_STAGE.get(stage["id"])
        provider = registry_by_id.get(provider_id) if provider_id else None
        provider_candidates = ([p for p in registry["providers"] if p["stage"] == "ink_3d"]
                               if stage["id"] == "ink_3d" else [])
        if provider_candidates:
            state, why = "QUALIFICATION_REQUIRED", (
                "multiple distinct 3-D ink candidates exist; acquisition pitch, input product, "
                "exposure and controls must resolve before one may be selected")
        elif provider_id and provider is None:
            state, why = "NO_PROVIDER", "the named provider is absent from the registry"
        elif provider is not None and provider["lifecycle"] in {"ADAPTER_READY", "QUALIFIED"}:
            state, why = "AVAILABLE", "provider contract resolves; stage-specific evidence still applies"
        elif provider is not None:
            state, why = provider["lifecycle"], provider["detail"]
        else:
            state, why = stage["state"], stage["gap"]
        if first_gap is None and state not in {"WIRED", "AVAILABLE"}:
            first_gap = {"stage": stage["id"], "state": state, "why": why}
        rows.append({
            "id": stage["id"], "label": stage["label"], "contract_state": stage["state"],
            "plan_state": state, "provider_id": provider_id, "provider": provider,
            "provider_candidates": provider_candidates,
            "why": why,
        })
    return {
        "schema": CONTRACT,
        "scroll": scroll,
        "read_only": True,
        "complete": first_gap is None,
        "first_gap": first_gap,
        "stages": rows,
        "provider_registry_schema": registry["schema"],
        "next": ("resolve the first gap and record its controls before continuing"
                 if first_gap else "all declared plan stages resolve; run the evidence gates"),
        "claim_ceiling": "planning and readiness only; no stage was run by this endpoint",
    }
