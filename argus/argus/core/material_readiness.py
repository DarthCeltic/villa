"""Selected-scroll route from raw CT through 3-D geometry to 2-D evidence."""
from __future__ import annotations

import time

from argus.core import official_models, science_candidates, scroll_ids, scroll_shelf

SCHEMA = "argus-material-readiness-v1"

ROUTE_STAGE_IDS = ("raw_ct", "surface_prediction", "mesh_tracing", "flatten", "ink", "review", "text")

_PRODUCT_OVERRIDES: dict = {}
_PRODUCT_OVERRIDE_EVIDENCE: dict = {}
_MODEL_DIRECT_EXPOSURE = {
    "fiber_ink_4class_selfdistill": {"PHercParis4"},
}


def _stage(stage: str, label: str, state: str, why: str, provider: str | None = None) -> dict:
    return {"stage": stage, "label": label, "state": state, "why": why,
            "provider": provider}


def _product(product: str, state: str, why: str, *, producer: str | None = None,
             evidence: str | None = None, next_action: str | None = None) -> dict:
    return {"product": product, "state": state, "why": why, "producer": producer,
            "evidence": evidence, "next_action": next_action}


_LASAGNA_TTL_S = 30.0
_LASAGNA_CACHE = {"at": 0.0, "value": None}


def _lasagna_status() -> dict:
    """The candidate status does not depend on the scroll; a batch of scrolls reads it once."""
    from argus.core import lasagna_candidate

    now = time.monotonic()
    if _LASAGNA_CACHE["value"] is None or now - _LASAGNA_CACHE["at"] > _LASAGNA_TTL_S:
        _LASAGNA_CACHE.update(at=now, value=lasagna_candidate.candidate_status())
    return _LASAGNA_CACHE["value"]


def _products(canonical: str, material: dict) -> list[dict]:
    from argus.core import lasagna_candidate

    lasagna_status = _lasagna_status()
    local = material.get("local_data") == "HELD"
    published = bool(material.get("published_upstream"))
    has_surface = bool((material.get("filters") or {}).get("has_surface"))
    has_render = bool((material.get("filters") or {}).get("has_render"))
    labelled = bool(material.get("labelled"))
    raw_state = "READY" if local else "REMOTE_ONLY" if published else "MISSING_PROVIDER"
    rows = [
        _product("raw_ct", raw_state,
                 "identity-bound bytes are held locally" if local else
                 "the official catalogue publishes an acquisition, but its bytes are not held locally" if published else
                 "no held or published acquisition is registered",
                 producer="raw_ct_acquisition",
                 next_action="open a bounded acquisition plan" if published and not local else None),
        _product("surface_prediction", "READY" if has_surface else
                 "PRODUCIBLE" if raw_state in {"READY", "REMOTE_ONLY"} else "MISSING_PROVIDER",
                 "a registered surface product exists" if has_surface else
                 "registered Villa surface providers can produce a candidate after CT retrieval" if published or local else
                 "surface production cannot be planned without CT",
                 producer="vc3d_geometry_toolchain"),
        _product("lasagna_fields",
                 "PRODUCIBLE" if lasagna_status["state"] == "REMOTE_ONLY" else "MISSING_PROVIDER",
                 "current Villa predict3d is frozen behind a remote Linux/GPU job plan; no selected-material output exists yet"
                 if lasagna_status["state"] == "REMOTE_ONLY" else
                 "no selected-material Lasagna bundle or intact candidate producer is registered",
                 producer="villa/lasagna@%s" % lasagna_candidate.CANDIDATE_SHA,
                 next_action="supply the pinned checkpoint and create /api/providers/lasagna/plan"),
        _product("fiber_prediction", "MISSING_PROVIDER",
                 "no identity-bound fiber prediction volume is registered for this material",
                 producer="fiber model", next_action="select a pinned fiber checkpoint and produce an identity-bound volume"),
        _product("fiber_tracks", "MISSING_PROVIDER",
                 "no identity-bound fiber annotation or track artifact is registered",
                 producer="VC3D fiber annotation/tracer"),
        _product("patches", "MISSING_PROVIDER", "no identity-bound patch set is registered",
                 producer="surface patch extractor"),
        _product("umbilicus", "MISSING_PROVIDER", "no identity-bound umbilicus or scroll specification is registered",
                 producer="VC3D or governed external geometry editor"),
        _product("registration_transforms", "PRODUCIBLE",
                 "the frame contract is available; each ordered acquisition pair still needs a registered transform",
                 producer="argus.core.physical_frame"),
        _product("outer_shell", "MISSING_PROVIDER", "no verified CT-fitted outer shell is registered",
                 producer="surface geometry provider"),
        _product("surface_mesh", "READY" if has_surface else "PRODUCIBLE" if published or local else "MISSING_PROVIDER",
                 "registered geometry exists" if has_surface else "geometry can be produced after its inputs resolve",
                 producer="vc3d_geometry_toolchain"),
        _product("flattened_surface", "READY" if has_render else "PRODUCIBLE" if has_surface else "MISSING_PROVIDER",
                 "a registered flat/rendered product exists" if has_render else "an accepted mesh is required before flattening",
                 producer="vc_flatten"),
        _product("human_labels", "READY" if labelled else "SCIENTIFICALLY_BLOCKED",
                 "human labels are registered" if labelled else "no independent human labels are registered for qualification"),
    ]
    overrides = _PRODUCT_OVERRIDES.get(canonical)
    if overrides:
        by = {row["product"]: row for row in rows}
        source = _PRODUCT_OVERRIDE_EVIDENCE[canonical]
        for product, values in overrides.items():
            by[product].update(evidence=source, **values)
    return rows


def _detectors(scroll: str, families: list[str]) -> list[dict]:
    rows = []
    for model in official_models.MODELS:
        is_ink = "ink" in model.labels
        stage = "ink_3d" if is_ink else ("surface_prediction" if "surface" in model.repo_id else "geometry_cue")
        model_key = model.repo_id.rsplit("/", 1)[-1]
        exposed = scroll in _MODEL_DIRECT_EXPOSURE.get(model_key, set())
        native_families = ["2.4um/78keV"] if model.repo_id.endswith("fiber_ink_4class_selfdistill") else []
        if exposed:
            verdict = "APPARATUS_ONLY"
            why = "the checkpoint was trained on PHercParis4 at about 2.4 um; it cannot establish generalization on this scroll"
        elif model.semantic_state != official_models.DECLARED_CLASS_MEANING_UNTESTED:
            verdict = "SCIENTIFICALLY_BLOCKED"
            why = model.what_it_does_not_establish
        else:
            verdict = "CANDIDATE"
            why = model.what_it_does_not_establish
        rows.append({
            "id": model.repo_id,
            "stage": stage,
            "verdict": verdict,
            "revision": model.revision,
            "weight_sha256": model.weight_sha256,
            "licence": model.licence,
            "semantic_state": model.semantic_state,
            "selected_scroll_exposed": exposed,
            "selected_acquisition_families": families,
            "native_acquisition_families": native_families,
            "acquisition_compatibility": (
                "NATIVE_EXPOSED_APPARATUS" if exposed else
                "CROSS_SCROLL_AND_ACQUISITION" if native_families else
                "UNKNOWN_NATIVE_ACQUISITION"
            ),
            "eligible_for_automatic_routing": False,
            "why": why,
        })
    for candidate in science_candidates.inventory(scroll)["candidates"]:
        if candidate["stage"] != "ink_3d":
            continue
        native = candidate.get("native_families") or []
        exposure = candidate["selected_scroll_exposure"]
        rows.append({
            "id": candidate["id"], "stage": "ink_3d",
            "verdict": ("APPARATUS_ONLY" if exposure.startswith("EXPOSED") else
                        "CANDIDATE_CONTROL_REQUIRED"),
            "revision": candidate["revision"],
            "weight_sha256": (candidate.get("artifact") or {}).get("sha256"),
            "licence": candidate["license"],
            "semantic_state": candidate["lifecycle"],
            "selected_scroll_exposure": exposure,
            "selected_acquisition_families": families,
            "native_acquisition_families": native,
            "acquisition_compatibility": (
                "EXACT_FAMILY_REQUIRES_RUNTIME_CONTROL"
                if any(native_family.split("um", 1)[0] in family for native_family in native for family in families)
                else "REQUIRES_EXPLICIT_RESAMPLING_OR_MATCHED_ACQUISITION"
            ),
            "eligible_for_automatic_routing": False,
            "why": candidate["detail"],
            "evidence": candidate["evidence"],
        })
    return rows


def build(scroll: str, *, shelf: dict | None = None) -> dict:
    """`shelf` lets a caller that already composed the shelf (one scroll_status batch) share it."""
    try:
        canonical = scroll_ids.resolve(scroll)
    except KeyError as exc:
        return {"schema": SCHEMA, "read_only": True, "selected_scroll": scroll,
                "canonical_scroll": None, "state": "IDENTITY_CONFLICT", "route": [],
                "code": "IDENTITY_UNKNOWN",
                "detectors": [], "why": str(exc), "claim_ceiling": "nothing was selected or run"}
    try:
        shelf = shelf if shelf is not None else scroll_shelf.compose_available()
        material = next((row for row in shelf.get("scrolls", [])
                         if row.get("scroll") == canonical), None)
        shelf_error = None
    except scroll_shelf.ShelfError as exc:
        material, shelf_error = None, str(exc)
    if material is None:
        return {"schema": SCHEMA, "read_only": True, "selected_scroll": scroll,
                "canonical_scroll": canonical, "state": "UNKNOWN", "route": [],
                "code": "SHELF_UNAVAILABLE" if shelf_error else "NOT_ON_SHELF",
                "detectors": [], "why": shelf_error or "the canonical scroll is absent from the material shelf",
                "claim_ceiling": "nothing was selected or run"}

    products = _products(canonical, material)
    products_by_id = {row["product"]: row for row in products}
    local = material.get("local_data") == "HELD"
    published = bool(material.get("published_upstream"))
    has_surface = bool((material.get("filters") or {}).get("has_surface"))
    has_render = bool((material.get("filters") or {}).get("has_render"))
    labelled = bool(material.get("labelled"))
    families = list(material.get("acquisition_families") or [])
    ct_state = products_by_id["raw_ct"]["state"]
    surface = products_by_id["surface_prediction"]
    mesh = products_by_id["surface_mesh"]
    flattened = products_by_id["flattened_surface"]
    route = [
        _stage("raw_ct", "Raw CT", ct_state,
               "an identity-bound local acquisition is held" if local else
               "an official acquisition is published and can be retrieved under a bounded plan" if published else
               "no held or published acquisition is registered", "raw_ct_acquisition"),
        _stage("surface_prediction", "Find the sheet", surface["state"], surface["why"],
               surface["producer"]),
        _stage("mesh_tracing", "Build and correct 3-D", mesh["state"], mesh["why"],
               mesh["producer"]),
        _stage("flatten", "Flatten to 2-D", flattened["state"], flattened["why"],
               flattened["producer"]),
        _stage("ink", "Detect ink", "SCIENTIFICALLY_BLOCKED",
               "detector candidates are inventoried below, but no detector is qualified across scrolls; a run may produce evidence, not a reading"),
        _stage("review", "Verify evidence", "READY" if labelled else "SCIENTIFICALLY_BLOCKED",
               "human labels exist for controls" if labelled else "independent human verification is required for candidate glyph evidence"),
        _stage("text", "Transcribe and translate", "SCIENTIFICALLY_BLOCKED",
               "text work may begin only from independently accepted glyph evidence", "transcription_review"),
    ]
    first_gap = next((row for row in route if row["state"] != "READY"), None)
    return {
        "schema": SCHEMA,
        "read_only": True,
        "selected_scroll": scroll,
        "canonical_scroll": canonical,
        "state": "READY_FOR_INSPECTION" if local or has_surface or has_render else "RETRIEVAL_REQUIRED" if published else "MISSING_INPUT",
        "material": {"shelf": material.get("shelf"), "local_data": material.get("local_data"),
                     "published_upstream": published, "acquisitions": material.get("acquisitions") or [],
                     "acquisition_families": families, "furthest_stage": material.get("furthest_stage"),
                     "prizes": material.get("prizes") or [], "labelled": labelled,
                     "label_representations": material.get("label_representations") or 0,
                     "label_authority": material.get("label_authority"),
                     "physical_segments": material.get("physical_segments") or 0,
                     "operator_fence": material.get("operator_fence"),
                     "attested_store": material.get("attested_store"),
                     "display": material.get("display") or canonical},
        "route": route,
        "products": products,
        "first_gap": first_gap,
        "detectors": _detectors(canonical, families),
        "next": first_gap["why"] if first_gap else "inspect the complete evidence chain",
        "claim_ceiling": "readiness only; no stage was run and no detector output is called ink or text",
    }
