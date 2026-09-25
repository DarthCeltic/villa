"""The complete ARGUS process contract, from held CT to translated text."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from argus.core import journey, paths

SCHEMA = "argus-process-contract-v1"


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def evaluate_replacement(candidate: dict) -> dict:
    """Return the only safe adoption state for an upstream provider candidate."""
    required = {
        "immutable_revision": "an immutable source revision",
        "input_contract": "the accepted input contract",
        "output_contract": "the emitted artifact contract",
        "coordinate_schema": "the coordinate and orientation schema",
        "license": "a declared licence ruling",
        "synthetic_control": "a passing synthetic control",
        "real_data_control": "a passing same-material real-data control",
    }
    missing = [label for key, label in required.items() if not candidate.get(key)]
    incompatible = list(candidate.get("incompatible") or [])
    if missing or incompatible:
        return {
            "state": "HELD",
            "may_stage_automatically": True,
            "may_promote_automatically": False,
            "missing": missing,
            "incompatible": incompatible,
            "next": "satisfy every contract and control, then request operator promotion",
        }
    return {
        "state": "READY_FOR_OPERATOR_PROMOTION",
        "may_stage_automatically": True,
        "may_promote_automatically": False,
        "missing": [],
        "incompatible": [],
        "next": "operator reviews the differential receipt and promotes a new immutable pin",
    }


def _acquisition_receipt(scroll: str | None) -> dict:
    module_present = _module("argus.core.target_acquisition_packet")
    out = {
        "packet_type_built": module_present,
        "generic_fetch_ui": False,
        "state": "PACKET_REQUIRED",
        "detail": (
            "ARGUS can bind identity, ROI, byte ceilings, controls and a dry run in a target "
            "acquisition packet. Sources exposes a generic zero-fetch plan; execution remains "
            "on the separate governed command service."
        ),
    }
    def key(value):
        return str(value or "").strip().upper().replace("_", "").replace("-", "")

    def names(doc):
        """Yield declared physical identities from any packet-shaped receipt."""
        if isinstance(doc, dict):
            for name, value in doc.items():
                if name in {"scroll", "physical_scroll", "target_scroll"}:
                    yield value
                yield from names(value)
        elif isinstance(doc, list):
            for value in doc:
                yield from names(value)

    if not scroll:
        return out
    for root in paths.artifact_roots():
        candidates = sorted(root.glob("*_packet/RECEIPT*.json"))
        for candidate in reversed(candidates):
            try:
                doc = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if key(scroll) not in {key(value) for value in names(doc)}:
                continue
            table = doc.get("gate_table") or {}
            failed = [name for name, state in table.items() if state != "PASS"]
            out.update(
                state="NOT_AUTHORISED" if failed else "PACKET_PASSED",
                receipt=str(candidate),
                failed_gates=failed,
                fetched_bytes=doc.get("fetched_bytes"),
                detail=(
                    "An acquisition packet for this scroll was evaluated and refused; no fetch occurred."
                    if failed else "The recorded acquisition packet for this scroll passed every declared gate."
                ),
            )
            return out
    return out


def _providers() -> list[dict]:
    """Compatibility projection of the provider registry into the process contract."""
    from argus.core import provider_registry

    rows = provider_registry.inventory()["providers"]
    out = []
    for row in rows:
        lifecycle = row["lifecycle"]
        if row["id"] == "blender" and lifecycle == "ADAPTER_READY":
            lifecycle = "ADAPTER_READY_AVAILABLE"
        elif row["id"] == "blender" and lifecycle == "NOT_INSTALLED":
            lifecycle = "ADAPTER_READY_NOT_INSTALLED"
        out.append({"id": row["id"], "stage": row["stage"], "role": row["role"],
                    "lifecycle": lifecycle, "path": row.get("executable"),
                    "detail": row["detail"]})
    return out


DERIVED_STAGE_IDS = ("ct_inspection", "normal_orientation", "fibers", "evidence_comparison")

_STAGES = [
    ("raw_ct", "Acquire and bind raw CT", "WIRED_BOUNDED", "the governed acquire.execute door is wired with a zero-fetch approval hash, launch authorization, data lease and identity-bound receipts; real fetch remains explicitly enabled and gated"),
    ("identity", "Identify the physical scroll and volume", "WIRED", "identity is explicit and mismatches refuse rather than substitute"),
    ("profile", "Measure acquisition, fibers, geometry and render quality", "WIRED_BOUNDED", "Library-facing profile handoff is wired; an exact profile receipt and qualified recipe are still required"),
    ("surface_prediction", "Predict sheet surfaces in 3D", "WIRED_BOUNDED", "argus.core.surface_prediction_provider is a complete, tested provider contract: it resolves scroll+acquisition to a pinned checkpoint (surface_m7_nnunet or surface_recto), verifies the checkpoint sha256 against a pinned identity and refuses on domain/spacing/identity mismatch (argus/tests/test_surface_prediction_provider.py); it never calls vesuvius.predict or argus_vesuvius.cli, so inference execution and scientific promotion are still unbuilt"),
    ("mesh_tracing", "Build connected surface geometry", "PARTIAL", "argus/capabilities.json pins vc_grow_seg_from_segments CALLABLE and vc_grow_seg_from_seed REAL_DATA_PROVEN; argus.core.villa_provider_adapter.plan_copy_out_in/run_copy_out_in route the classical Copy Out/In workflow through that same capability, refuse to overwrite the source and re-check its fingerprint after each native pass (argus/tests/test_copy_out_in_provider.py), and argus.core.copy_out_in_defect_control adds a tested local-defect propagation check; correction remains human-in-the-loop"),
    ("topology_repair", "Detect holes, mergers and sheet switches", "HUMAN_GATED", "argus.core.topology_metric (argus/tests/test_topology_metric.py) computes mergers/splits by connected-component labelling under a declared connectivity rule; it is a diagnostic only -- it identifies a topology break, it does not repair one -- so reliable automatic repair remains an upstream bottleneck"),
    ("flatten", "Flatten the recovered sheet", "WIRED_BOUNDED", "typed ARGUS provider.invoke covers vc_flatten and OBJ/TIFXYZ interchange with immutable input/output receipts; every real vc_flatten invocation now attaches a flatten_distortion report (argus.core.surface_metric.flatten_distortion_summary, measured against a real pinned vc_flatten.exe run, not simulated) and argus.core.blender_roundtrip can refuse boundary-vertex coordinate/scale drift on a repair round-trip when ARGUS_BLENDER_EXECUTION_ENABLED=1 (default off); topology and fold controls still gate promotion"),
    ("sample", "Sample CT along the flattened surface", "WIRED", "identity-bound same-material sampling is implemented"),
    ("adapt", "Adapt physical pitch and depth", "WIRED_BOUNDED", "known acquisition transforms are explicit; unknown domains require qualification"),
    ("ink_2d", "Detect ink on the recovered surface", "RUNS_UNQUALIFIED", "mechanics run; scientific and licence gates remain"),
    ("ink_3d", "Localize ink directly in the volume", "CANDIDATE_ONLY", "public 3-D ink candidates are inventoried; none is qualified"),
    ("review", "Review candidates with controls and provenance", "WIRED", "Review Lab and refusal boundaries are implemented"),
    ("transcription", "Assemble independently accepted glyphs", "HUMAN_GATED", "reading board exists and refuses to call itself automatic transcription"),
    ("translation", "Translate an accepted transcription", "HUMAN_GATED", "provenance-bound translation worksheet exists; no automatic engine is promoted"),
    ("evidence", "Export evidence and a reproducible package", "WIRED_BOUNDED", "package builder refuses unsupported claims and does not submit"),
]


def derived_stages(providers: list[dict] | None = None) -> list[dict]:
    """The four journey stages the runner does not walk, each with a state read from its owner."""
    from argus.core import control_evidence_kinds

    providers = _providers() if providers is None else providers
    fiber = [p for p in providers if "fiber" in str(p["stage"])]
    if not fiber:
        fiber_state = "MISSING_PROVIDER"
        fiber_gap = "the provider registry declares no fiber provider, so no fiber product can be planned"
    else:
        qualified = [p for p in fiber if p["lifecycle"] == "QUALIFIED"]
        fiber_state = "WIRED_BOUNDED" if qualified else "CANDIDATE_ONLY"
        fiber_gap = (
            "provider registry coverage: %s. argus.core.fiber_annotations checks VC3D fiber links "
            "and argus.core.fiber_layer previews the one retained fibre output for the scroll it was "
            "produced on; no fiber provider is qualified, a prediction-derived fiber is a proposal "
            "and never ground truth" % ", ".join("%s (%s)" % (p["id"], p["lifecycle"]) for p in fiber)
            if not qualified else
            "qualified fiber provider(s): %s" % ", ".join(p["id"] for p in qualified))
    truth = control_evidence_kinds.ground_truth_holdings()
    normal_gap = (
        "argus.core.normal_orientation makes a normal field consistent by adjacency without ever "
        "consulting a model, and argus.core.signed_normal resolves the interior side with an "
        "orientation_status: a sign from centre-direction geometry alone is ORIENTATION_UNCONFIRMED "
        "whatever its confidence, only an independent cue or a human verification makes it "
        "CONFIRMED, and a disagreeing cue is ORIENTATION_CONFLICT. No per-scroll orientation "
        "receipt store exists, so a one-sided composite stays off by default")
    rows = [
        ("ct_inspection", "Inspect the CT", "WIRED_BOUNDED",
         "read-only volume and plane viewers inspect identity-bound local or bounded-streamed CT; "
         "inspection records nothing, never fetches bytes on its own and shows nothing for a scroll "
         "whose bytes are not held"),
        ("normal_orientation", "Orient the sheet normals", "PARTIAL", normal_gap),
        ("fibers", "Recover fibers on the surface", fiber_state, fiber_gap),
        ("evidence_comparison", "Compare evidence and controls",
         "WIRED_BOUNDED" if truth else "PARTIAL",
         "argus.core.control_evidence_kinds keeps a published 3-D prediction "
         "(%s) apart from %s; ARGUS holds %d independent ground-truth role(s), so agreement with a "
         "published prediction is a development control and never confirmation"
         % (control_evidence_kinds.PUBLISHED_3D_PREDICTION_LOCALIZED_LIKELY_INK,
            control_evidence_kinds.INDEPENDENT_PHYSICAL_GROUND_TRUTH, len(truth))),
    ]
    return [{"id": sid, "label": label, "state": state, "gap": gap, "runner": False,
             "journey_step": journey.step_for_stage(sid).id}
            for sid, label, state, gap in rows]


def contract_stages() -> list[dict]:
    """The fifteen runner stages, without touching a provider or a receipt (cheap, stable)."""
    return [{"id": sid, "label": label, "state": state, "gap": gap,
             "journey_step": journey.step_for_stage(sid).id}
            for sid, label, state, gap in _STAGES]


def derive(scroll: str | None = None) -> dict:
    stage_rows = contract_stages()
    providers = _providers()
    complete = all(s["state"] == "WIRED" for s in stage_rows)
    return {
        "schema": SCHEMA,
        "scroll": scroll,
        "complete": complete,
        "headline": (
            "ARGUS does not yet close raw CT to translation. Identity-bound sampling and review "
            "are strong; governed retrieval, provider-complete geometry, qualified detection "
            "and translation remain open."
        ),
        "counts": {
            "total": len(stage_rows),
            "wired": sum(s["state"] == "WIRED" for s in stage_rows),
            "open": sum(s["state"] != "WIRED" for s in stage_rows),
            "by_state": {
                state: sum(1 for s in stage_rows if s["state"] == state)
                for state in sorted({s["state"] for s in stage_rows})
            },
        },
        "stages": stage_rows,
        "derived_stages": derived_stages(providers),
        "providers": providers,
        "retrieval": _acquisition_receipt(scroll),
        "auto_configuration": {
            "fingerprint_built": _module("argus.core.profiler"),
            "qualified_recipe_registry_built": _module("argus.core.profiler"),
            "library_workflow_wired": True,
            "known_scroll": "recommend only a qualified recipe inside the declared distance ceiling",
            "unknown_scroll": "return NO_MATCH and open a bounded qualification lane; never guess the nearest settings",
        },
        "replacement_policy": {
            "automatic": ["discover", "pin immutable candidate", "stage", "run synthetic control", "run same-material real-data control", "produce differential receipt"],
            "operator_only": "promote the tested candidate to a new current pin",
            "existing_projects": "retain their immutable provider revisions",
            "contract": "input, output, coordinates, licence, synthetic control and real-data control must all pass",
        },
        "provider_adapters": {
            "villa": {
                "module": "argus.core.villa_provider_adapter",
                "action": "provider.invoke",
                "supported_capabilities": ["vc_grow_seg_from_segments", "vc_flatten",
                                            "vc_obj2tifxyz", "vc_tifxyz2obj",
                                            "vc_render_tifxyz", "vc_project_tifxyz",
                                            "vc_gen_normalgrids", "vc_calc_surface_metrics",
                                            "vc_ngrids", "vc_atlas_constraints_export",
                                            "vc_render_video", "vesuvius.zarr_tasks", "vesuvius.compute_st",
                                            "vesuvius.voxelize_obj",
                                            "vesuvius.predict", "vesuvius.blend_logits",
                                            "vesuvius.blend_and_finalize", "vesuvius.finalize_outputs",
                                            "vesuvius.refine_labels", "vesuvius.train",
                                            "vc_diffuse_winding",
                                            "vesuvius.surface_preflight"],
                "state": "WIRED_BOUNDED",
                "boundary": "typed argv, pinned ledger entry, identity binding, output separation and differential receipt; no automatic scientific promotion",
            }
        },
        "final_authority": {
            "authority_id": "belisarius",
            "authority_name": "Final human disposition",
            "action": "vigiles.final_decision",
            "module": "argus.core.belisarius_gate",
            "run_file": "belriouse_run.json",
            "state": "HUMAN_GATED",
            "decision_options": ["YES", "NO"],
            "boundary": "YES requires a non-REFUSED VIGILES receipt; NO is always recordable; neither changes scientific certification",
        },
        "hecate_needs": [],
        "blender_role": "optional geometry interchange, inspection and repair provider; not a replacement for VC3D tracing or vc_flatten flattening",
    }
