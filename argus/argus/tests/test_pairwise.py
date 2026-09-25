"""Pairwise ranking: playful to answer, severe to promote."""
from __future__ import annotations

import random

import pytest

from argus.core.pairwise import (
    ABSTENTIONS, CHOICES, Comparison, PairwiseError, consistency, fit_bradley_terry,
    ORDERING_CHOICES, most_informative_pairs, present, promotion_blockers, resolve,
    reviewer_reliability,
    win_probability,
)


def cmp(a, b, choice, rid="r1", cls="COMMUNITY", pair="p", reason=None):
    if reason is None:
        reason = "SHEET_SWITCH" if choice in ORDERING_CHOICES else ""
    return Comparison(pair_id=pair, a=a, b=b, choice=choice, reviewer_id=rid,
                      reviewer_class=cls, duration_s=8.0, utc="2026-09-09T23:50:00Z",
                      reason=reason)



def test_the_reviewer_is_shown_nothing_persuasive():
    p = present("p1", "cand_a", "cand_b", seed=7, is_control=True, expected="cand_a")
    v = p.visible()
    assert v["model_confidence"] == "withheld"
    assert v["votes_so_far"] == "withheld"
    assert v["running_winner"] == "withheld"
    assert v["candidate_source"] == "withheld"
    assert v["is_control"] == "not disclosed"
    assert "cand_a" not in str(v["choices"])


def test_the_offered_choices_are_the_simple_five():
    v = present("p1", "a", "b", seed=1).visible()
    assert v["choices"] == ["A_BETTER", "B_BETTER", "EQUIVALENT", "NEITHER", "UNSURE"]


def test_side_placement_is_randomised_but_reproducible():
    sides = {present("p", "a", "b", seed=s).left for s in range(24)}
    assert sides == {"a", "b"}, "one candidate always appeared on the same side"
    assert present("p", "a", "b", seed=7).left == present("p", "a", "b", seed=7).left


def test_a_side_answer_is_resolved_into_candidate_terms():
    p = present("p", "a", "b", seed=3)
    c = resolve(p, "A_BETTER", reviewer_id="r1", reviewer_class="COMMUNITY",
                duration_s=5.0, utc="2026-09-09T00:00:00Z", reason="WRONG_DEPTH")
    assert {c.a, c.b} == {"a", "b"}
    assert c.a == p.left



def test_a_consistently_preferred_candidate_ranks_highest():
    comps = [cmp("good", "bad", "A_BETTER", rid="r%d" % i) for i in range(6)]
    f = fit_bradley_terry(comps)
    assert f["strengths"]["good"] > f["strengths"]["bad"]
    assert win_probability(f["strengths"], "good", "bad") > 0.8


def test_the_fit_is_order_independent():
    """The property Elo does not have, and the reason it was not used."""
    comps = [cmp("a", "b", "A_BETTER"), cmp("b", "c", "A_BETTER"),
             cmp("a", "c", "A_BETTER"), cmp("c", "a", "B_BETTER"),
             cmp("b", "a", "B_BETTER")]
    first = fit_bradley_terry(comps)["strengths"]
    for seed in (1, 2, 3, 4):
        sh = comps[:]
        random.Random(seed).shuffle(sh)
        other = fit_bradley_terry(sh)["strengths"]
        for k in first:
            assert abs(first[k] - other[k]) < 1e-6, (k, seed)


def test_ties_are_counted_as_half_a_win_each():
    f = fit_bradley_terry([cmp("a", "b", "EQUIVALENT") for _ in range(8)])
    assert abs(f["strengths"]["a"] - f["strengths"]["b"]) < 1e-6
    assert f["ties_counted_as"] == "half a win to each side"


@pytest.mark.parametrize("ab", sorted(ABSTENTIONS))
def test_abstentions_carry_no_ordering_weight(ab):
    f = fit_bradley_terry([cmp("a", "b", ab) for _ in range(5)])
    assert f["n_comparisons"] == 0
    assert f["strengths"] == {}


def test_an_all_abstention_set_says_so_rather_than_inventing_a_ranking():
    f = fit_bradley_terry([cmp("a", "b", "UNSURE")])
    assert "no ordering information" in f["note"]


def test_the_estimate_states_what_it_is_and_is_not():
    f = fit_bradley_terry([cmp("a", "b", "A_BETTER")])
    assert "people consistently prefer" in f["claim"]
    assert "physically correct" in f["not_a_claim"]
    assert f["claim"] != f["not_a_claim"]


def test_a_candidate_cannot_be_compared_with_itself():
    with pytest.raises(PairwiseError, match="cannot be compared with itself"):
        cmp("a", "a", "A_BETTER")


def test_an_unknown_choice_is_refused():
    with pytest.raises(PairwiseError, match="unknown choice"):
        cmp("a", "b", "SWIPE_RIGHT")



def test_uncertain_pairs_are_preferred_for_the_next_question():
    f = fit_bradley_terry([cmp("strong", "weak", "A_BETTER") for _ in range(10)]
                          + [cmp("mid1", "mid2", "A_BETTER"), cmp("mid1", "mid2", "B_BETTER")])
    pairs = most_informative_pairs(f["strengths"], ["strong", "weak", "mid1", "mid2"], k=1)
    top = pairs[0]
    assert top["information"] > 0.5
    assert {top["a"], top["b"]} != {"strong", "weak"}



def test_reliability_is_measured_against_controls_not_the_majority():
    p = present("ctrl", "right", "wrong", seed=5, is_control=True, expected="right")
    pres = {"ctrl": p}
    solo = [Comparison(pair_id="ctrl", a="right", b="wrong", choice="A_BETTER",
                       reviewer_id="solo", reviewer_class="SPECIALIST", duration_s=9,
                       utc="t", reason="SHEET_SWITCH")]
    crowd = [Comparison(pair_id="ctrl", a="wrong", b="right", choice="A_BETTER",
                        reviewer_id="crowd", reviewer_class="COMMUNITY", duration_s=3,
                        utc="t", reason="SHEET_SWITCH")]
    assert reviewer_reliability(solo, pres, "solo")["control_accuracy"] == 1.0
    assert reviewer_reliability(crowd, pres, "crowd")["control_accuracy"] == 0.0


def test_reliability_is_none_without_a_control():
    pres = {"p": present("p", "a", "b", seed=1)}
    assert reviewer_reliability([cmp("a", "b", "A_BETTER")], pres,
                                "r1")["control_accuracy"] is None


def test_abstaining_on_a_control_is_not_counted_wrong():
    p = present("ctrl", "right", "wrong", seed=2, is_control=True, expected="right")
    c = [Comparison(pair_id="ctrl", a="right", b="wrong", choice="UNSURE",
                    reviewer_id="r", reviewer_class="COMMUNITY", duration_s=4, utc="t")]
    assert reviewer_reliability(c, {"ctrl": p}, "r")["control_comparisons_scored"] == 0


def test_repeated_pairs_measure_self_consistency():
    comps = [cmp("a", "b", "A_BETTER"), cmp("a", "b", "A_BETTER"),
             cmp("c", "d", "A_BETTER"), cmp("c", "d", "B_BETTER")]
    r = consistency(comps, "r1")
    assert r["repeated_pairs"] == 2
    assert r["self_agreement"] == 0.5



def test_one_reviewer_cannot_promote():
    b = promotion_blockers([cmp("a", "b", "A_BETTER", rid="only")])
    assert any("only one reviewer" in x for x in b)


def test_repeated_swipes_by_one_person_still_cannot_promote():
    b = promotion_blockers([cmp("a", "b", "A_BETTER", rid="only") for _ in range(50)])
    assert any("only one reviewer" in x for x in b)


def test_an_all_ai_set_cannot_promote():
    b = promotion_blockers([cmp("a", "b", "A_BETTER", rid="bot1", cls="AI_AGENT"),
                            cmp("a", "b", "A_BETTER", rid="bot2", cls="AI_AGENT")])
    assert any("all-AI consensus" in x for x in b)


def test_a_set_with_no_preference_cannot_promote():
    b = promotion_blockers([cmp("a", "b", "UNSURE", rid="r1"),
                            cmp("a", "b", "NEITHER", rid="r2")])
    assert any("nothing to promote" in x for x in b)


def test_two_humans_expressing_preferences_clear_these_blockers():
    b = promotion_blockers([cmp("a", "b", "A_BETTER", rid="r1"),
                            cmp("a", "b", "A_BETTER", rid="r2", cls="SPECIALIST")])
    assert b == []


def test_the_preference_ceiling_is_a_standing_policy_not_a_blocker():
    """As a blocker it would make the list never empty, so it could never report clear -- and a check that always fails is ignored exactly as fast as one that never does."""
    from argus.core.pairwise import PREFERENCE_CEILING
    assert "never a label" in PREFERENCE_CEILING
    assert "PAIRWISE_PREFERRED and no further" in PREFERENCE_CEILING
    assert "FROZEN_SUPERVISION" in PREFERENCE_CEILING
    clear = [cmp("a", "b", "A_BETTER", rid="r1"), cmp("a", "b", "A_BETTER", rid="r2")]
    assert promotion_blockers(clear) == [], "the ceiling must not sit in the blocker list"


def test_a_preference_without_a_reason_code_blocks_promotion():
    c = Comparison(pair_id="p", a="a", b="b", choice="EQUIVALENT", reviewer_id="r1",
                   reviewer_class="SPECIALIST", duration_s=3.0, utc="z")
    assert any("without a reason code" in b for b in promotion_blockers(
      [c, cmp("a", "b", "A_BETTER", rid="r2")])) is False, (
      "a tie carries no reason and must not be reported as a missing one")
