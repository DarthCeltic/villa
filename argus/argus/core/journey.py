"""The ONE definition of the journey a scroll takes from selection to a publication packet."""
from __future__ import annotations

from dataclasses import dataclass

from argus.core import control_evidence_kinds as CEK

SCHEMA = "argus-journey-v1"


@dataclass(frozen=True)
class Step:
    n: int
    id: str
    label: str
    screen: str
    stages: tuple
    provider_families: tuple
    actions: tuple
    evidence_role: str | None
    optional: bool = False
    ui_only: bool = False
    why_no_role: str | None = None


STEPS = (
    Step(1, "select_scroll", "Select a scroll", "/", (), (), (), None, ui_only=True,
         why_no_role="choosing a scroll produces no evidence"),
    Step(2, "exact_identity", "Confirm its exact identity", "/explore", ("identity",), (), (),
         None, why_no_role="identity is a binding, not a measurement"),
    Step(3, "acquisition", "Acquire or stream the CT", "/sources", ("raw_ct", "profile"),
         ("raw_ct",), ("acquire.dry_run", "identity.resolve", "acquire.execute", "preflight"), CEK.RAW_MEASURED_INPUT),
    Step(4, "ct_inspection", "Inspect the CT", "/workbench", ("ct_inspection",), (), (),
         CEK.RAW_MEASURED_INPUT, optional=True),
    Step(5, "surface_prediction", "Predict the sheet surfaces", "/workbench",
         ("surface_prediction",), ("surface_prediction",),
         ("surface.prepare_exact_eligible", "provider.invoke"), CEK.PREDICTION_DISCOVERY_EVIDENCE),
    Step(6, "tracing", "Trace and segment the surface", "/workbench", ("mesh_tracing",),
         ("mesh_tracing", "geometry"), ("seed.grow.execute", "provider.invoke"),
         CEK.GEOMETRY_PROVIDER),
    Step(7, "topology", "Check and repair the topology", "/workbench", ("topology_repair",),
         ("topology_repair",), ("copy_out_in.execute", "blender.launch", "blender.verify_roundtrip"),
         CEK.GEOMETRY_PROVIDER),
    Step(8, "normal_orientation", "Orient the sheet normals", "/workbench",
         ("normal_orientation",), (), (), CEK.GEOMETRY_PROVIDER),
    Step(9, "flatten_render", "Flatten and render the surface", "/workbench",
         ("flatten", "sample", "adapt"), ("flatten", "render"), ("provider.invoke",),
         CEK.DERIVED_VISUALIZATION),
    Step(10, "fibers", "Recover the fibers", "/workbench", ("fibers",),
         ("fiber_ink_segmentation", "fiber_segmentation"), ("provider.invoke",),
         CEK.PREDICTION_DISCOVERY_EVIDENCE, optional=True),
    Step(11, "ink_inference", "Run ink inference", "/workbench", ("ink_2d", "ink_3d"),
         ("ink_2d", "ink_3d"), ("model.qualify", "pipeline.start"),
         CEK.PREDICTION_DISCOVERY_EVIDENCE),
    Step(12, "evidence_comparison", "Compare the evidence", "/evidence",
         ("evidence_comparison",), (), ("evidence.reproduce",), CEK.INDEPENDENT_EVIDENCE),
    Step(13, "candidate_review", "Review the candidates", "/review", ("review",), (),
         ("candidate.open",), CEK.HUMAN_JUDGMENT),
    Step(14, "transcription", "Transcribe accepted glyphs", "/review", ("transcription",),
         ("transcription", "reading"), ("vigiles.final_decision",), CEK.HUMAN_JUDGMENT),
    Step(15, "translation", "Translate the transcription", "/review", ("translation",),
         ("translation",), (), CEK.HUMAN_JUDGMENT),
    Step(16, "export_packet", "Export the publication packet", "/evidence", ("evidence",), (),
         ("evidence.reproduce",), None,
         why_no_role="a packet carries each item's own evidence role and adds none"),
)

_BY_ID = {s.id: s for s in STEPS}
_BY_STAGE = {stage: s for s in STEPS for stage in s.stages}


def step_ids() -> tuple:
    return tuple(s.id for s in STEPS)


def step(step_id: str) -> Step:
    try:
        return _BY_ID[step_id]
    except KeyError:
        raise KeyError("unknown journey step %r; the journey names %s" % (step_id, step_ids())) from None


def step_for_stage(stage_id: str) -> Step:
    """The journey step a process-contract stage serves."""
    try:
        return _BY_STAGE[stage_id]
    except KeyError:
        raise KeyError("process-contract stage %r belongs to no journey step" % stage_id) from None


VOCABULARIES = {
    "chain_traversal": {
        "CT": "acquisition", "SURFACE": "tracing", "FLATTEN": "flatten_render",
        "SAMPLE": "flatten_render", "DETECT": "ink_inference", "PACKAGE": "export_packet",
    },
    "ontology": {
        "CT": "acquisition", "Trace": "tracing", "Validate": "topology",
        "Flatten": "flatten_render", "Sample": "flatten_render", "Adapt": "flatten_render",
        "Detect": "ink_inference", "Review": "candidate_review", "Read": "transcription",
        "Export": "export_packet",
    },
    "route_graph": {
        "eligible_ct": "acquisition", "sheet_tracing": "tracing", "surface_geometry": "tracing",
        "flattening": "flatten_render", "ct_to_surface_sampling": "flatten_render",
        "acquisition_pitch_adaptation": "flatten_render", "ink_detector": "ink_inference",
        "uncertainty_and_candidates": "candidate_review", "review_lab": "candidate_review",
        "text_assembly": "transcription", "evidence_export": "export_packet",
        "prize_package": "export_packet",
    },
    "scroll_shelf": {
        "NONE": "select_scroll", "SCAN_HELD": "acquisition", "GEOMETRY": "tracing",
        "RENDER": "flatten_render", "LABELS": "evidence_comparison", "RESULT": "ink_inference",
    },
    "material_readiness": {
        "raw_ct": "acquisition", "surface_prediction": "surface_prediction",
        "mesh_tracing": "tracing", "flatten": "flatten_render", "ink": "ink_inference",
        "review": "candidate_review", "text": "transcription",
    },
    "autopilot": {
        "DISCOVERED": "select_scroll", "PROFILED": "exact_identity",
        "EXPOSURE_CLASSIFIED": "exact_identity", "MEASUREMENTS_COMPLETE": "acquisition",
        "ROUTE_PLANNED": "surface_prediction", "INSTRUMENT_QUALIFIED": "evidence_comparison",
        "READY_FOR_HUNT": "ink_inference", "HUNTED": "ink_inference",
    },
    "route_strip": {
        "CT": "acquisition", "Surface": "tracing", "Flatten": "flatten_render",
        "Sample": "flatten_render", "Detect": "ink_inference", "Review": "candidate_review",
    },
    "pipeline_strip": {
        "ct": "acquisition", "trace": "tracing", "geometry": "topology", "flatten": "flatten_render",
        "render": "flatten_render", "ink": "ink_inference", "decision": "candidate_review",
        "package": "export_packet",
    },
}


def step_for(vocabulary: str, name: str) -> Step:
    """Map another module's stage word to its journey step, or refuse."""
    try:
        return _BY_ID[VOCABULARIES[vocabulary][name]]
    except KeyError:
        raise KeyError("%r is not mapped to a journey step in the %r vocabulary" % (name, vocabulary)) from None


def describe() -> dict:
    return {
        "schema": SCHEMA,
        "steps": [{"n": s.n, "id": s.id, "label": s.label, "screen": s.screen,
                   "stages": list(s.stages), "provider_families": list(s.provider_families),
                   "actions": list(s.actions), "evidence_role": s.evidence_role,
                   "optional": s.optional, "ui_only": s.ui_only} for s in STEPS],
        "vocabularies": {name: dict(words) for name, words in VOCABULARIES.items()},
    }
