"""Guards on what a forward/reverse pair is allowed to claim."""
from __future__ import annotations

import pytest

from argus.core import orientation_semantics as OS


def test_a_recto_verso_name_is_refused_without_physical_proof():
    with pytest.raises(OS.OrientationSemanticsViolation):
        OS.refuse_physical_face_claim("recto_verso_test")
    with pytest.raises(OS.OrientationSemanticsViolation):
        OS.refuse_physical_face_claim("opposite_face_auc")


def test_proof_must_be_stated_not_asserted():
    """A caller that HAS physical proof must write down what it is, so it can be judged."""
    assert OS.refuse_physical_face_claim(
        "recto_auc", physical_proof="independent fibre-direction registration, fixture receipt")


def test_sanctioned_names_pass():
    for n in OS.SANCTIONED_NAMES:
        assert OS.refuse_physical_face_claim(n) == n


def test_per_pixel_comparison_refuses_without_a_registered_mapping():
    """The trap: subtracting reverse from forward pixelwise looks obviously right and is only valid if the same index means the same material in both."""
    p = OS.OrientationPair(forward={"auc": 0.7}, reverse={"auc": 0.5})
    with pytest.raises(OS.OrientationSemanticsViolation):
        p.per_pixel_difference(lambda f, r: 0)


def test_per_pixel_comparison_allowed_once_a_mapping_is_declared():
    p = OS.OrientationPair(forward={"a": 2}, reverse={"a": 1},
                           registered_mapping="identity, proven by a fixture co-registration")
    assert p.per_pixel_difference(lambda f, r: f["a"] - r["a"]) == 1


def test_the_four_quantities_stay_separate():
    rec = OS.OrientationPair(forward={"auc": 0.7}, reverse={"auc": 0.5},
                             union={"auc": 0.72},
                             components_per_cm2={"value": 3.1}).as_record()
    for k in ("forward", "reverse", "either_direction_union", "components_per_cm2"):
        assert k in rec
    assert rec["measures"] == "depth_order_sensitivity"
    assert "recto" in rec["NOT"].lower()


def test_asymmetry_inside_the_null_band_is_not_a_finding():
    v = OS.asymmetry_verdict(0.71, 0.69, null_band=0.05)
    assert v["exceeds_null"] is False
    v2 = OS.asymmetry_verdict(0.80, 0.50, null_band=0.05)
    assert v2["exceeds_null"] is True
    assert "recto" in v2["forbidden_interpretation"].lower()


def test_null_band_is_required_not_defaulted():
    """A default null band would be a threshold chosen in this module instead of measured in the experiment."""
    import inspect
    sig = inspect.signature(OS.asymmetry_verdict)
    assert sig.parameters["null_band"].default is inspect.Parameter.empty
