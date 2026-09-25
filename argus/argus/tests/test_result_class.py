"""The discovery gate is the one rule in this project that must not be reachable by accident."""
from __future__ import annotations

import pytest

from argus.core import result_class as rc


def make(**kw):
    base = dict(target="T", target_class="UNREAD_SCROLL", exposure_basis="UNSEEN_PROVEN",
                detector="d", detector_cross_scroll_qualified=True, acquisition="9.362um")
    base.update(kw)
    return rc.ResultClass(**base)



def test_all_three_conditions_are_required_for_discovery():
    assert make().may_claim_discovery is True
    assert make(target_class="LABELLED_FRAGMENT").may_claim_discovery is False
    assert make(exposure_basis="HELD_OUT_BY_FOLD").may_claim_discovery is False
    assert make(detector_cross_scroll_qualified=False).may_claim_discovery is False


def test_ink_found_is_the_same_gate_under_a_softer_word():
    for kw in ({}, {"detector_cross_scroll_qualified": False},
               {"exposure_basis": "EXPOSURE_UNKNOWN"}):
        r = make(**kw)
        assert r.may_claim_ink_found == r.may_claim_discovery


def test_unknown_exposure_is_not_clean_exposure():
    assert make(exposure_basis="EXPOSURE_UNKNOWN").may_claim_discovery is False
    assert any("unknown" in s for s in make(exposure_basis="EXPOSURE_UNKNOWN").not_established())


def test_no_result_this_system_can_currently_build_may_claim_discovery():
    r = rc.example_fragment_control(auc=0.5, detector_sha256="a" * 64)
    assert r.may_claim_discovery is False
    assert r.presentation == "KNOWN_DOMAIN_HELD_OUT_CONTROL"



@pytest.mark.parametrize("kw,expect", [
  ({"target_class": "SYNTHETIC"}, "DEMONSTRATION_ONLY"),
  ({"exposure_basis": "TRAINED_ON"}, "CALIBRATION_ONLY"),
  ({"target_class": "LABELLED_FRAGMENT", "exposure_basis": "HELD_OUT_BY_FOLD"},
   "KNOWN_DOMAIN_HELD_OUT_CONTROL"),
  ({}, "QUALIFIED_EVIDENCE"),
  ({"detector_cross_scroll_qualified": False}, "EXPLORATORY_CANDIDATE"),
])
def test_presentation_is_derived_from_the_evidence(kw, expect):
    assert make(**kw).presentation == expect


def test_trained_on_beats_every_other_flattering_reading():
    assert make(exposure_basis="TRAINED_ON").presentation == "CALIBRATION_ONLY"


def test_every_presentation_has_a_banner_that_is_not_empty():
    for p in rc.PRESENTATION:
        assert rc.BANNERS[p].strip()



@pytest.mark.parametrize("phrase", [
  "we found ink on this scroll",
  "first letters from a prize target",
  "the model discovered text",
  "revealed text in the unread scroll",
  "WE READ THE SCROLL",
])
def test_a_discovery_phrasing_raises_rather_than_returning_false(phrase):
    r = rc.example_fragment_control(auc=0.5, detector_sha256="a" * 64)
    with pytest.raises(rc.ResultClassError) as e:
        r.assert_not_discovery(phrase)
    assert "KNOWN_DOMAIN_HELD_OUT_CONTROL" in str(e.value)


def test_an_honest_phrasing_passes():
    r = rc.example_fragment_control(auc=0.5, detector_sha256="a" * 64)
    r.assert_not_discovery("a labelled fragment read out-of-sample; this proves the pipeline")


def test_the_same_phrasing_is_permitted_once_the_gate_actually_passes():
    make().assert_not_discovery("we read the scroll")



@pytest.mark.parametrize("kw", [
  {"target_class": "PROBABLY_A_SCROLL"},
  {"exposure_basis": "PROBABLY_FINE"},
  {"target": ""},
  {"detector": ""},
])
def test_a_result_that_cannot_say_what_it_is_refuses_to_exist(kw):
    with pytest.raises(rc.ResultClassError):
        make(**kw)


def test_the_record_carries_the_banner_and_the_verdict_together():
    d = rc.example_fragment_control(auc=0.5, detector_sha256="a" * 64).as_dict()
    assert d["contract"] == rc.RESULT_CLASS_ID
    assert d["may_claim_discovery"] is False
    assert d["banner"] == rc.BANNERS["KNOWN_DOMAIN_HELD_OUT_CONTROL"]
    assert d["not_established"]


def test_a_held_out_fold_is_not_a_held_out_domain():
    n = make(target_class="LABELLED_FRAGMENT", exposure_basis="HELD_OUT_BY_FOLD",
             detector_cross_scroll_qualified=False).not_established()
    assert any("FOLD" in s for s in n)
    assert any("cross-scroll" in s for s in n)



def test_the_bridge_reports_zero_gates_passed_and_says_an_unread_search_is_shut():
    b = rc.bridge_status()
    assert b["gates_passed"] == 0
    assert len(b["gates"]) == 4
    assert any("NOT OPEN" in str(v) for v in b.values())
    assert "FORBIDDEN" in b["ink_found_claim"]


def test_every_bridge_gate_says_what_it_needs_and_what_it_blocks():
    for g in rc.FIRST_LETTERS_BRIDGE:
        for k in ("gate", "what", "needs", "currently", "blocks"):
            assert g[k].strip(), (g["gate"], k)
