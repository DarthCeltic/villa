"""COMPOSITE and UNKNOWN are real answers and must never be coerced into a binary."""
from __future__ import annotations

import pytest

from argus.core import surface_contract as SC


def good(**over):
    base = dict(
      physical_scroll="PHercFixture1", segment_id="seg-A", surface_id="seg-A_surface_a",
      ct_volume_id="20260101000000-seg-A", tifxyz_path="/data/seg-A.tifxyz",
      normal_orientation="OUTWARD_FROM_SCROLL_CENTRE", face=SC.RECTO,
      face_established_by="INSPECTED_AGAINST_CT", recto_shaved_or_missing="NO",
      pitch_um=9.362, energy_kev=113, pyramid_level=0,
      source_licence="CC BY-NC 4.0", redistribution_permitted="NO",
      parent_geometry="vc3d segment seg-A", generation_command="vc3d render --segment seg-A",
      hashes={"tifxyz": "a" * 64},
    )
    base.update(over)
    return SC.SurfaceDeclaration(**base)


def test_a_fully_declared_surface_passes():
    r = SC.check(good())
    assert r["ok"], r["problems"]
    assert r["usable_where_a_single_face_is_required"]


def test_composite_is_accepted_and_recorded():
    r = SC.check(good(face=SC.COMPOSITE, face_established_by="INSPECTED_AGAINST_CT"))
    assert r["ok"], r["problems"]
    assert r["face"] == SC.COMPOSITE
    assert not r["usable_where_a_single_face_is_required"]
    assert "rounded" in r["why_not_if_not"]


def test_unknown_is_accepted_and_recorded():
    r = SC.check(good(face=SC.UNKNOWN, face_established_by="UNDECLARED"))
    assert r["ok"], r["problems"]
    assert not r["usable_where_a_single_face_is_required"]


def test_composite_is_never_coerced_to_a_binary():
    """THE CORE RULE."""
    for face in (SC.COMPOSITE, SC.UNKNOWN):
        with pytest.raises(SC.SurfaceRefusal) as e:
            SC.require_single_face(good(face=face, face_established_by="UNDECLARED"),
                                   used_for="detector orientation selection")
        assert "not coerced" in str(e.value)


def test_a_single_face_claim_needs_provenance():
    r = SC.check(good(face=SC.RECTO, face_established_by="UNDECLARED"))
    assert not r["ok"]
    assert any("how that was established" in p for p in r["problems"])


def test_an_asserted_label_is_accepted_but_recorded_as_asserted():
    """Weak provenance is still provenance."""
    r = SC.check(good(face_established_by="ASSERTED_WITHOUT_EVIDENCE"))
    assert r["ok"], r["problems"]


def test_normal_offset_cannot_establish_the_opposite_face():
    r = SC.check(good(face=SC.VERSO, generation_command="derive --method NORMAL_OFFSET -0.3mm"))
    assert not r["ok"]
    assert any("SAME face at a different depth" in p for p in r["problems"])


def test_offset_is_allowed_when_the_surface_does_not_claim_a_single_face():
    """The refusal is about the LABEL, not about the operation."""
    r = SC.check(good(face=SC.UNKNOWN, face_established_by="UNDECLARED",
                      generation_command="derive --method NORMAL_OFFSET -0.3mm"))
    assert r["ok"], r["problems"]


def test_missing_identity_is_refused_with_the_reason_named():
    r = SC.check(good(ct_volume_id=""))
    assert not r["ok"]
    assert any("cannot be scored against anything" in p for p in r["problems"]), "the reason is named"


def test_acquisition_must_be_declared():
    for over in ({"pitch_um": None}, {"energy_kev": None}, {"pyramid_level": None}):
        r = SC.check(good(**over))
        assert not r["ok"], over


def test_an_undeclared_licence_is_a_refusal_not_a_presumption():
    r = SC.check(good(source_licence=""))
    assert not r["ok"]
    assert any("never a presumption" in p for p in r["problems"])


def test_redistribution_unknown_is_allowed_and_means_it_stays_here():
    r = SC.check(good(redistribution_permitted="UNKNOWN"))
    assert r["ok"], r["problems"]


def test_normals_must_declare_their_direction():
    r = SC.check(good(normal_orientation="whatever"))
    assert not r["ok"]
    assert any("depth sweep" in p for p in r["problems"])


def test_shaved_recto_must_be_answered():
    r = SC.check(good(recto_shaved_or_missing=""))
    assert not r["ok"]
    r2 = SC.check(good(recto_shaved_or_missing="UNKNOWN"))
    assert r2["ok"], r2["problems"]


def test_hashes_are_required():
    r = SC.check(good(hashes={}))
    assert not r["ok"]
    assert any("was scored" in p for p in r["problems"])


def test_every_required_field_appears_in_the_request_template():
    t = SC.request_template()
    assert set(t["required_fields"]) == set(SC.REQUIRED)
    assert t["face"]["composite_and_unknown_are_real_answers"] is True


def test_the_offset_note_does_not_claim_the_objection_is_proven():
    """It is a community claim, not reproduced."""
    note = SC.request_template()["note_on_offset"]
    assert "has not been reproduced" in note
    assert "not a label" in note


def test_require_raises_with_every_problem_not_just_the_first():
    with pytest.raises(SC.SurfaceRefusal) as e:
        SC.require(good(ct_volume_id="", pitch_um=None, source_licence=""))
    assert str(e.value).count("- ") >= 3
