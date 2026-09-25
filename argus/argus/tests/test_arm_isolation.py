"""Two arms must have seen the same thing, or the difference between them is not about the arms."""
from __future__ import annotations

import pytest

from argus.core import arm_isolation as AI


def env(arm, cls, **over):
    e = {
      "arm": arm,
      "information_class": cls,
      "input_array_hashes": {"seg1": "a" * 64, "seg2": "b" * 64},
      "fold_membership": {"train": ["PHercFixture1", "PHercFixture2"], "holdout": ["PHercFixture3"]},
      "control_suite": ["permutation_null", "planted_ink", "detection_floor"],
      "presentation_budget": 1000,
      "preprocessing": {"normalisation": "global", "depth": 8},
      "metric": "argus-metric-v1",
    }
    e.update(over)
    return e


A = env("A", "class_a")


def test_matched_arms_with_different_classes_pass():
    """Without this, a harness that refused everything would satisfy every test below."""
    r = AI.assert_isolated([A, env("B", "class_b")])
    assert r["isolated"] is True
    assert r["varying_field"] == "information_class"


@pytest.mark.parametrize("field,bad", [
  ("presentation_budget", 4000),
  ("control_suite", ["permutation_null", "planted_ink"]),
  ("metric", "some-other-auc"),
  ("preprocessing", {"normalisation": "fitted_to_target", "depth": 8}),
  ("input_array_hashes", {"seg1": "a" * 64, "seg2": "c" * 64}),
  ("fold_membership", {"train": ["PHercFixture1"], "holdout": ["PHercFixture3"]}),
])
def test_any_differing_matched_field_refuses_the_comparison(field, bad):
    with pytest.raises(AI.IsolationRefusal) as e:
        AI.assert_isolated([A, env("B", "class_b", **{field: bad})])
    assert field in str(e.value), "the refusal must NAME the field, or it gets overridden"


def test_the_refusal_says_why_that_field_matters():
    """'The arms differ' sends a reader to diff two JSON dumps by eye."""
    with pytest.raises(AI.IsolationRefusal) as e:
        AI.assert_isolated([A, env("B", "class_b", presentation_budget=4000)])
    assert "easiest thing to extend" in str(e.value)


def test_folds_are_compared_on_physical_identity_not_on_strings():
    """An alias or a second pitch must not be able to move a scroll between arms while every string matches — and equally, the same folds spelled differently must not read as a difference."""
    r = AI.assert_isolated([A, env("B", "class_b",
                                   fold_membership={"train": ["phercfixture1", "PHERCFIXTURE2"],
                                                    "holdout": ["phercfixture3"]})])
    assert r["isolated"] is True


def test_a_genuinely_different_holdout_is_still_caught():
    """Without this, case-insensitive comparison could be hiding real fold differences."""
    with pytest.raises(AI.IsolationRefusal, match="fold_membership"):
        AI.assert_isolated([A, env("B", "class_b",
                                   fold_membership={"train": ["PHercFixture1", "PHercFixture2"],
                                                    "holdout": ["PHercFixture4"]})])


def test_reordering_a_set_valued_field_does_not_trip_the_alarm():
    """Membership is the fact; ordering is an artefact of how a runner happened to iterate."""
    r = AI.assert_isolated([A, env("B", "class_b",
                                   control_suite=["detection_floor", "planted_ink",
                                                  "permutation_null"])])
    assert r["isolated"] is True


def test_one_arm_is_refused():
    """Checking one arm against itself is how a harness reports green while guaranteeing nothing."""
    with pytest.raises(AI.IsolationRefusal, match="guaranteeing nothing"):
        AI.assert_isolated([A])


def test_two_arms_with_the_same_information_class_are_refused():
    """If the arms are identical, any difference in their scores is noise read as an effect."""
    with pytest.raises(AI.IsolationRefusal, match="noise being read as an effect"):
        AI.assert_isolated([A, env("B", "class_a")])


@pytest.mark.parametrize("field", AI.MATCHED_FIELDS)
def test_an_undeclared_matched_field_is_refused(field):
    """FAIL-CLOSED, one field at a time."""
    bad = env("B", "class_b")
    del bad[field]
    with pytest.raises(AI.IsolationRefusal) as e:
        AI.assert_isolated([A, bad])
    assert field in str(e.value)


def test_an_undeclared_information_class_is_refused():
    """Without it there is no statement of what the comparison is supposed to measure."""
    bad = env("B", "class_b")
    del bad["information_class"]
    with pytest.raises(AI.IsolationRefusal, match="information_class"):
        AI.assert_isolated([A, bad])


def test_two_arms_sharing_a_name_are_refused():
    with pytest.raises(AI.IsolationRefusal):
        AI.assert_isolated([A, env("A", "class_b")])


def test_the_pass_states_what_it_does_and_does_not_license():
    r = AI.assert_isolated([A, env("B", "class_b")])
    assert "and to nothing else" in r["what_this_licenses"]
    joined = " ".join(r["what_this_does_not_license"])
    assert "noise" in joined
    assert "detected ink" in joined


def test_each_matched_field_is_hashed_separately():
    """One blob hash tells you the arms differed and nothing else, and the next thing anyone does is diff two JSON dumps by eye."""
    r = AI.assert_isolated([A, env("B", "class_b")])
    assert set(r["matched_hashes"]) == set(AI.MATCHED_FIELDS)
    assert len(set(r["matched_hashes"].values())) > 1, "distinct fields must hash distinctly"
