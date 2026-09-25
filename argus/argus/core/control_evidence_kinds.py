"""What KIND of thing a control is, and what it may never be called."""
from __future__ import annotations

SCHEMA = "argus-control-evidence-kinds-v1"

PUBLISHED_3D_PREDICTION_LOCALIZED_LIKELY_INK = "PUBLISHED_3D_PREDICTION_LOCALIZED_LIKELY_INK"
INDEPENDENT_PHYSICAL_GROUND_TRUTH = "INDEPENDENT_PHYSICAL_GROUND_TRUTH"

KINDS = {
    PUBLISHED_3D_PREDICTION_LOCALIZED_LIKELY_INK: {
        "plain": "A published 3-D model prediction of where ink is likely. It is a model's output on "
                 "the same CT, not an observation of the ink.",
        "is_independent_physical_ground_truth": False,
        "independent_of_ct_models": False,
        "can_exercise_the_apparatus": True,
        "can_support_development": True,
        "can_qualify_a_detector": False,
        "can_externally_validate_a_reading": False,
        "may_be_called": ["control", "published prediction", "likely-ink localization"],
        "may_not_be_called": ["ground truth", "independent physical ground truth", "validated ink"],
    },
    INDEPENDENT_PHYSICAL_GROUND_TRUTH: {
        "plain": "A reading fixed by something other than a CT-based model prediction: a physically "
                 "opened fragment, a human transcription of the physical object, or an independent "
                 "expert confirmation.",
        "is_independent_physical_ground_truth": True,
        "independent_of_ct_models": True,
        "can_exercise_the_apparatus": True,
        "can_support_development": True,
        "can_qualify_a_detector": True,
        "can_externally_validate_a_reading": True,
        "may_be_called": ["ground truth", "independent physical ground truth"],
        "may_not_be_called": [],
    },
}

RAW_MEASURED_INPUT = "RAW_MEASURED_INPUT"
GEOMETRY_PROVIDER = "GEOMETRY_PROVIDER"
PREDICTION_DISCOVERY_EVIDENCE = "PREDICTION_DISCOVERY_EVIDENCE"
INDEPENDENT_EVIDENCE = "INDEPENDENT_EVIDENCE"
HUMAN_JUDGMENT = "HUMAN_JUDGMENT"
DERIVED_VISUALIZATION = "DERIVED_VISUALIZATION"
GROUND_TRUTH = "GROUND_TRUTH"

EVIDENCE_ROLES = {
    RAW_MEASURED_INPUT: "CT data as the scanner measured it (possibly resampled onto a surface). Not an interpretation.",
    GEOMETRY_PROVIDER: "A surface or segmentation produced by a geometry tool (a provider's output, not a measurement).",
    PREDICTION_DISCOVERY_EVIDENCE: "A model's prediction or discovery signal. It suggests where to look; it is not an observation of ink.",
    INDEPENDENT_EVIDENCE: "Evidence that does not come from the model under test (e.g. a second, independent method). Shown only when present.",
    HUMAN_JUDGMENT: "A person's review, annotation or verdict, attributed to that person.",
    DERIVED_VISUALIZATION: "A picture computed from other data (projection, contrast, composite, overlay). Never raw data.",
    GROUND_TRUTH: "A reading fixed by independent physical evidence. Shown only when genuinely present.",
}

VIEW_EVIDENCE_ROLES = {
    "local": [DERIVED_VISUALIZATION],
    "mesh": [GEOMETRY_PROVIDER],
    "windings": [GEOMETRY_PROVIDER, DERIVED_VISUALIZATION],
    "surface": [DERIVED_VISUALIZATION],
    "ct": [RAW_MEASURED_INPUT],
    "planes": [RAW_MEASURED_INPUT],
    "rawvolume": [RAW_MEASURED_INPUT],
    "segmentation": [GEOMETRY_PROVIDER],
    "ink": [PREDICTION_DISCOVERY_EVIDENCE],
    "overlay": [DERIVED_VISUALIZATION, PREDICTION_DISCOVERY_EVIDENCE],
    "fibers": [PREDICTION_DISCOVERY_EVIDENCE],
    "hecate": [PREDICTION_DISCOVERY_EVIDENCE],
}

ROLE_EVIDENCE_KIND = {
    "PARIS4_TITLE_CONTROL": PUBLISHED_3D_PREDICTION_LOCALIZED_LIKELY_INK,
}

RULE = ("PUBLISHED_3D_PREDICTION_LOCALIZED_LIKELY_INK is not INDEPENDENT_PHYSICAL_GROUND_TRUTH. "
        "Agreement with a published prediction never becomes physical ground truth.")


class PromotionRefused(ValueError):
    """Raised when code tries to treat a prediction-kind control as physical ground truth."""


def kind_for_role(role: str | None) -> str | None:
    return ROLE_EVIDENCE_KIND.get(role) if role else None


def assert_not_promoted(claimed_kind: str, actual_kind: str) -> None:
    """Refuse a claim that a control is of a kind it is not (the only direction that matters here)."""
    if claimed_kind == INDEPENDENT_PHYSICAL_GROUND_TRUTH and actual_kind != INDEPENDENT_PHYSICAL_GROUND_TRUTH:
        raise PromotionRefused(
            "%s cannot be presented as %s. %s" % (actual_kind, claimed_kind, RULE))


def roles_for_view(view: str) -> list[str]:
    """The evidence roles a Workbench view carries; an unknown view claims none (never a guess)."""
    return list(VIEW_EVIDENCE_ROLES.get(view, []))


def ground_truth_holdings() -> list:
    """What ARGUS holds as INDEPENDENT_PHYSICAL_GROUND_TRUTH."""
    return [role for role, kind in ROLE_EVIDENCE_KIND.items() if kind == INDEPENDENT_PHYSICAL_GROUND_TRUTH]


def payload() -> dict:
    from argus.core import control_matrix as CM
    controls = []
    for role, kind in sorted(ROLE_EVIDENCE_KIND.items()):
        controls.append({
            "role": role,
            "scroll": CM.ROLE_BOUND_SCROLL.get(role),
            "evidence_kind": kind,
            "is_independent_physical_ground_truth": KINDS[kind]["is_independent_physical_ground_truth"],
            "not": INDEPENDENT_PHYSICAL_GROUND_TRUTH,
            "label_authority_note": "ARGUS records no independent physical ground truth for "
                                    "this control",
        })
    return {"schema": SCHEMA, "read_only": True, "rule": RULE, "kinds": KINDS, "controls": controls,
            "roles": EVIDENCE_ROLES, "view_roles": VIEW_EVIDENCE_ROLES,
            "ground_truth_present": ground_truth_holdings()}
