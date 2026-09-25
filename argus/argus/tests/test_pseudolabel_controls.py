"""Controls for the experimental pseudo-label provider."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from argus.core import pseudolabel_controls as pc
from argus.core.pseudolabel_controls import (
    Lineage, LineageError, PseudoLabelPromotionRefused, assert_not_ground_truth,
    channel_swap_test, collapse_report, coordinate_overlap, coordinate_overlap_detail,
    cross_scroll_gate, leakage_report, may_be_production, mirror_orientation_equivariance,
    provider_record, status_for)


def _lin(side="recto", scroll="S_A", volume="V1", segment="G1", source=pc.HUMAN, **kw):
    return Lineage(side=side, scroll_id=scroll, volume_id=volume, segment_id=segment,
                   label_source=source, **kw)


def _pseudo(side="verso", scroll="S_A", volume="V1", segment="G1", **kw):
    kw.setdefault("teacher_id", "teacher-1")
    return _lin(side, scroll, volume, segment, pc.PSEUDO_LABEL, **kw)



def test_lineage_key_is_stable_and_separates_recto_from_verso():
    a = _lin("recto", training_sample_ids=("s2", "s1"))
    b = _lin("recto", training_sample_ids=("s1", "s2"))
    assert a.key() == b.key()
    assert a.key() != _lin("verso", training_sample_ids=("s1", "s2")).key()
    assert a.key() != _lin("recto", training_sample_ids=("s1",)).key()


def test_lineage_coerces_samples_to_tuple_and_is_frozen():
    lin = _lin(training_sample_ids=["a", "b"])
    assert lin.training_sample_ids == ("a", "b")
    with pytest.raises(Exception):
        lin.side = "verso"


def test_pseudo_label_without_teacher_is_refused():
    with pytest.raises(LineageError, match="teacher_id"):
        Lineage("verso", "S_A", "V1", "G1", pc.PSEUDO_LABEL)
    assert _pseudo().teacher_id == "teacher-1"


def test_verso_naming_recto_samples_must_say_so():
    with pytest.raises(LineageError, match="recto"):
        _pseudo(training_sample_ids=("recto:seg7", "verso:seg8"))
    declared = _pseudo(training_sample_ids=("recto:seg7",), recto_training_declared=True)
    assert declared.recto_training_declared
    _lin("recto", training_sample_ids=("recto:seg7",))


@pytest.mark.parametrize("kw", [
    {"side": "back"}, {"label_source": "GUESS"}, {"scroll_id": " "}, {"volume_id": ""}, {"segment_id": ""}])
def test_lineage_rejects_malformed_fields(kw):
    base = dict(side="recto", scroll_id="S_A", volume_id="V1", segment_id="G1", label_source=pc.HUMAN)
    base.update(kw)
    with pytest.raises(LineageError):
        Lineage(**base)



@pytest.mark.parametrize("claimed", [pc.OFFICIAL_GROUND_TRUTH, pc.HUMAN])
def test_pseudo_label_claimed_as_real_is_refused(claimed):
    with pytest.raises(PseudoLabelPromotionRefused):
        assert_not_ground_truth(pc.PSEUDO_LABEL, claimed)


def test_honest_provenance_passes_assertion():
    assert_not_ground_truth(pc.PSEUDO_LABEL)
    assert_not_ground_truth(pc.PSEUDO_LABEL, pc.PSEUDO_LABEL)
    assert_not_ground_truth(pc.HUMAN, pc.HUMAN)
    with pytest.raises(LineageError):
        assert_not_ground_truth("GUESS")


def test_may_be_production_lists_every_missing_requirement():
    ok, missing = may_be_production({})
    assert (ok, missing) == (False, list(pc.PROMOTION_REQUIREMENTS))
    ok, missing = may_be_production({"collapse_controls": True, "cross_scroll_evaluation": 0})
    assert ok is False
    assert missing == ["held_out_real_verso_labels", "teacher_student_provenance", "cross_scroll_evaluation"]
    assert may_be_production(None) == (False, list(pc.PROMOTION_REQUIREMENTS))
    assert status_for({"collapse_controls": True}) == "EXPERIMENTAL_RESEARCH_ONLY"


def test_all_requirements_only_reach_operator_review_never_production():
    ev = {k: True for k in pc.PROMOTION_REQUIREMENTS}
    assert may_be_production(ev) == (True, [])
    assert status_for(ev) == "ELIGIBLE_FOR_OPERATOR_REVIEW"
    rec = provider_record()
    assert rec["production_ready"] is False
    assert rec["status"] == "EXPERIMENTAL_RESEARCH_ONLY"



def _teacher_student(seed=0, n=4096):
    rng = np.random.default_rng(seed)
    teacher = rng.uniform(0.0, 1.0, n)
    healthy = np.clip(teacher + rng.normal(0.0, 0.1, n), 0.0, 1.0)
    return teacher, healthy


def test_healthy_correlated_student_is_not_collapsed():
    teacher, student = _teacher_student()
    rep = collapse_report(teacher, student)
    assert rep["collapsed"] is False
    assert rep["degenerate_input"] is False
    assert rep["reasons"] == []
    assert 0.8 < rep["std_ratio"] < 1.2
    assert rep["correlation"] > 0.9
    assert rep["entropy_ratio"] is not None


def test_planted_constant_student_is_collapsed():
    teacher, _ = _teacher_student()
    rep = collapse_report(teacher, np.full_like(teacher, 0.5))
    assert rep["collapsed"] is True
    assert "student_near_constant" in rep["reasons"]
    assert rep["std_ratio"] == 0.0


def test_near_constant_student_with_tiny_noise_is_collapsed():
    teacher, _ = _teacher_student()
    student = 0.3 + np.random.default_rng(1).normal(0.0, 1e-5, teacher.shape)
    assert collapse_report(teacher, student)["collapsed"] is True


def test_student_with_shrunken_variance_is_collapsed():
    teacher, healthy = _teacher_student()
    squashed = 0.5 + 0.02 * (healthy - healthy.mean())
    rep = collapse_report(teacher, squashed)
    assert rep["collapsed"] is True
    assert "student_std_below_tenth_of_teacher" in rep["reasons"]


def test_student_stuck_in_narrow_band_is_collapsed():
    teacher, healthy = _teacher_student()
    student = healthy.copy()
    student[: int(0.99 * student.size)] = 0.2
    rep = collapse_report(teacher, student)
    assert rep["near_constant_fraction"] > pc.NARROW_BAND_FRACTION
    assert rep["collapsed"] is True
    assert "student_concentrated_in_narrow_band" in rep["reasons"]


def test_sparse_ink_teacher_and_faithful_student_are_not_flagged():
    rng = np.random.default_rng(3)
    teacher = np.zeros(20000)
    ink = rng.choice(teacher.size, 600, replace=False)
    teacher[ink] = rng.uniform(0.6, 1.0, ink.size)
    student = np.clip(teacher + rng.normal(0.0, 0.02, teacher.size), 0.0, 1.0)
    rep = collapse_report(teacher, student)
    assert rep["teacher_near_constant_fraction"] > 0.9
    assert rep["collapsed"] is False


def test_all_zero_student_against_sparse_teacher_agrees_but_is_collapsed():
    rng = np.random.default_rng(4)
    teacher = np.zeros(20000)
    teacher[rng.choice(teacher.size, 600, replace=False)] = 0.9
    rep = collapse_report(teacher, np.zeros_like(teacher))
    assert rep["student_teacher_agreement"] > 0.95
    assert rep["student_constant_agreement"] == 1.0
    assert rep["collapsed"] is True


@pytest.mark.parametrize("teacher,student,reason", [
    (np.array([]), np.array([]), "empty_input"),
    (np.full(8, np.nan), np.full(8, np.nan), "no_finite_values"),
    (np.full(8, 0.5), np.linspace(0, 1, 8), "teacher_constant"),
    (np.linspace(0, 1, 8), np.linspace(0, 1, 6), "shape_mismatch"),
    (np.array(["a", "b"]), np.array([0.1, 0.2]), "non_numeric_input"),
])
def test_degenerate_inputs_never_crash_and_fail_closed(teacher, student, reason):
    rep = collapse_report(teacher, student)
    assert rep["degenerate_input"] is True
    assert rep["collapsed"] is True
    assert reason in rep["reasons"]


def test_partial_nan_pairs_are_dropped_and_counted():
    teacher, student = _teacher_student(n=1000)
    teacher[:100] = np.nan
    student[50:150] = np.nan
    rep = collapse_report(teacher, student)
    assert rep["n_pairs"] == 850
    assert rep["nonfinite_fraction"] == pytest.approx(0.15)
    assert rep["collapsed"] is False


def test_out_of_range_outputs_skip_entropy_but_still_report():
    rng = np.random.default_rng(5)
    t = rng.normal(0.0, 1.0, 500)
    rep = collapse_report(t, t * 0.9 + 0.1)
    assert rep["entropy_ratio"] is None
    assert rep["collapsed"] is False



def test_sample_level_leak_by_sample_segment_and_volume():
    train = [_lin("recto", "S_A", "V1", "G1", training_sample_ids=("x1", "x2"))]
    ev = [_lin("verso", "S_B", "V1", "G1", training_sample_ids=("x2", "y9"))]
    rep = leakage_report(train, ev)
    assert rep["sample_overlap"] == ["x2"]
    assert rep["segment_overlap"] == ["G1"]
    assert rep["volume_overlap"] == ["V1"]
    assert rep["sample_level_leak"] is True and rep["leaks"] is True
    assert rep["scroll_level_leak"] is False


def test_scroll_level_leak_is_reported_apart_from_sample_level():
    train = [_lin("recto", "S_A", "V1", "G1", training_sample_ids=("x1",))]
    ev = [_lin("verso", "S_A", "V2", "G2", training_sample_ids=("y1",))]
    rep = leakage_report(train, ev)
    assert rep["sample_level_leak"] is False
    assert rep["scroll_level_leak"] is True
    assert rep["scroll_overlap"] == ["S_A"]
    assert rep["leaks"] is True
    assert rep["cross_scroll_ok"] is False


def test_scroll_spelling_variants_still_count_as_the_same_scroll():
    rep = leakage_report([_lin(scroll="S_A")], [_lin("verso", "s-a", "V2", "G2")])
    assert rep["scroll_level_leak"] is True


def test_cross_scroll_ok_needs_disjoint_scrolls_and_real_eval_labels():
    train = [_lin("recto", "S_A", "V1", "G1")]
    real = [_lin("verso", "S_B", "V2", "G2", pc.OFFICIAL_GROUND_TRUTH)]
    rep = leakage_report(train, real)
    assert rep["leaks"] is False
    assert rep["cross_scroll_ok"] is True

    pseudo_eval = [_pseudo("verso", "S_B", "V2", "G2")]
    rep = leakage_report(train, pseudo_eval)
    assert rep["leaks"] is False
    assert rep["eval_all_real"] is False
    assert rep["cross_scroll_ok"] is False

    mixed = real + pseudo_eval
    assert leakage_report(train, mixed)["cross_scroll_ok"] is False


def test_empty_sides_never_certify_cross_scroll():
    real = [_lin("verso", "S_B", "V2", "G2")]
    assert leakage_report([], real)["cross_scroll_ok"] is False
    assert leakage_report([_lin()], [])["cross_scroll_ok"] is False


def test_coordinate_overlap_exact_fractions():
    a = [(0, 0, 0, 2, 2, 2)]
    b = [(1, 1, 1, 3, 3, 3)]
    assert coordinate_overlap(a, b) == pytest.approx(1 / 8)
    d = coordinate_overlap_detail(a, b)
    assert d["intersection_volume"] == 1.0 and d["volume_a"] == 8.0 and d["volume_b"] == 8.0
    assert d["fraction_of_a"] == pytest.approx(1 / 8)
    assert coordinate_overlap(a, [(5, 5, 5, 6, 6, 6)]) == 0.0
    assert coordinate_overlap(a, [(0, 0, 0, 1, 1, 1)]) == 1.0
    assert coordinate_overlap([(0, 0, 0, 4, 4, 4)], [(0, 0, 0, 2, 4, 4)]) == 1.0
    assert coordinate_overlap(a, [(2, 0, 0, 4, 2, 2)]) == 0.0


def test_coordinate_overlap_does_not_double_count_overlapping_boxes():
    a = [(0, 0, 0, 2, 2, 2), (1, 0, 0, 3, 2, 2)]
    b = [(0, 0, 0, 3, 2, 2)]
    d = coordinate_overlap_detail(a, b)
    assert d["volume_a"] == 12.0
    assert d["fraction_of_b"] == 1.0
    assert coordinate_overlap([], b) == 0.0
    assert coordinate_overlap(a, []) == 0.0


def test_coordinate_overlap_rejects_inverted_or_malformed_boxes():
    with pytest.raises(ValueError):
        coordinate_overlap([(2, 0, 0, 1, 1, 1)], [(0, 0, 0, 1, 1, 1)])
    with pytest.raises(ValueError):
        coordinate_overlap([(0, 0, 0, 1, 1)], [(0, 0, 0, 1, 1, 1)])



def _x(seed=0, shape=(3, 4, 5, 6)):
    return np.random.default_rng(seed).normal(size=shape)


def test_channel_ignoring_model_is_symmetric_collapse():
    rep = channel_swap_test(lambda a: a[2] * 2.0, _x())
    assert rep["symmetric_collapse"] is True
    assert rep["output_changed"] is False
    assert rep["max_abs_diff"] == 0.0
    assert rep["roundtrip_ok"] is True


def test_channel_sensitive_model_is_not_symmetric_collapse():
    rep = channel_swap_test(lambda a: a[0] - 2.0 * a[1], _x())
    assert rep["symmetric_collapse"] is False
    assert rep["output_changed"] is True
    assert rep["max_abs_diff"] > 0.1
    assert rep["roundtrip_ok"] is True


def test_channel_swap_does_not_mutate_input_and_respects_swap_pair():
    x = _x()
    before = x.copy()
    rep = channel_swap_test(lambda a: a[0] + 3.0 * a[2], x, swap=(0, 2))
    assert rep["symmetric_collapse"] is False
    assert np.array_equal(x, before)
    assert channel_swap_test(lambda a: a[0] + a[1], x)["symmetric_collapse"] is True


def test_nondeterministic_model_fails_roundtrip():
    calls = iter(range(100))
    rep = channel_swap_test(lambda a: a[0] + next(calls), _x())
    assert rep["roundtrip_ok"] is False


def test_channel_swap_refuses_untestable_input():
    x = _x()
    x[1] = x[0]
    with pytest.raises(ValueError, match="identical"):
        channel_swap_test(lambda a: a[0], x)
    with pytest.raises(ValueError):
        channel_swap_test(lambda a: a[0], _x(), swap=(0, 0))
    with pytest.raises(ValueError):
        channel_swap_test(lambda a: a[0], _x(), swap=(0, 7))


def test_shape_changing_output_counts_as_changed():
    rep = channel_swap_test(lambda a: a[: 2 if a[0, 0, 0, 0] > a[1, 0, 0, 0] else 1], _x(seed=2))
    assert rep["output_changed"] is True
    assert rep["max_abs_diff"] == float("inf")


def test_mirror_equivariance_holds_for_pointwise_model_and_fails_for_directional_one():
    x = _x()
    pointwise = mirror_orientation_equivariance(lambda a: np.tanh(a) + a * a, x, axis=2)
    assert pointwise["equivariant"] is True
    assert pointwise["max_abs_diff"] == 0.0
    directional = mirror_orientation_equivariance(lambda a: np.cumsum(a, axis=2), x, axis=2)
    assert directional["equivariant"] is False
    assert directional["max_abs_diff"] > 0.1


def test_mirror_equivariance_aligns_axis_when_output_drops_channel_axis():
    x = _x()
    ok = mirror_orientation_equivariance(lambda a: a.mean(axis=0), x, axis=-1)
    assert ok["equivariant"] is True and ok["axis"] == 3
    bad = mirror_orientation_equivariance(lambda a: np.cumsum(a.mean(axis=0), axis=2), x, axis=3)
    assert bad["equivariant"] is False
    with pytest.raises(ValueError):
        mirror_orientation_equivariance(lambda a: a, x, axis=0)



def test_gate_refuses_a_single_scroll_however_good():
    passed, why = cross_scroll_gate({"S_A": 0.99}, floor=0.5)
    assert passed is False
    assert "1 distinct" in why
    assert cross_scroll_gate({"S_A": 0.99}, min_scrolls=2)[0] is False


def test_gate_passes_three_distinct_scrolls_above_floor():
    passed, why = cross_scroll_gate({"S_A": 0.7, "S_B": 0.6, "S_C": 0.9}, floor=0.5)
    assert passed is True
    assert "3 distinct" in why


def test_gate_fails_when_any_needed_scroll_is_at_or_below_floor_or_nan():
    assert cross_scroll_gate({"S_A": 0.7, "S_B": 0.5, "S_C": 0.9}, floor=0.5)[0] is False
    assert cross_scroll_gate({"S_A": 0.7, "S_B": float("nan"), "S_C": 0.9}, floor=0.5)[0] is False
    assert cross_scroll_gate({"S_A": 0.7, "S_B": 0.6, "S_C": 0.9, "S_D": 0.1}, floor=0.5)[0] is True


def test_gate_counts_one_scroll_spelled_twice_once_at_its_lowest_score():
    scores = {"S_A": 0.9, "s-a": 0.2, "S_B": 0.8, "S_C": 0.8}
    assert cross_scroll_gate(scores, floor=0.5)[0] is False
    assert cross_scroll_gate({"S_A": 0.9, "s-a": 0.8, "S_B": 0.8}, floor=0.5)[0] is False


def test_gate_refuses_a_min_scrolls_that_would_admit_a_single_scroll():
    with pytest.raises(ValueError):
        cross_scroll_gate({"S_A": 0.9}, min_scrolls=1)



def test_provider_record_is_experimental_and_never_ground_truth():
    rec = provider_record()
    assert rec["id"] == "EXPERIMENTAL_PSEUDOLABEL_PROVIDER"
    assert rec["lifecycle"] == "EXPERIMENTAL_RESEARCH_ONLY"
    assert rec["status"] == "EXPERIMENTAL_RESEARCH_ONLY"
    assert rec["is_ground_truth"] is False
    assert rec["production_ready"] is False
    assert rec["promotion_requirements"] == [
        "held_out_real_verso_labels", "collapse_controls",
        "teacher_student_provenance", "cross_scroll_evaluation"]
    text = " ".join(rec["runnable_requirements"]).lower()
    assert "held-out" in text and "provenance" in text and "gpu" in text and "cpu-only" in text
    rec["promotion_requirements"].append("x")
    assert len(provider_record()["promotion_requirements"]) == 4


def test_controls_write_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    teacher, student = _teacher_student(n=256)
    collapse_report(teacher, student)
    leakage_report([_lin()], [_lin("verso", "S_B", "V2", "G2")])
    channel_swap_test(lambda a: a[0] - a[1], _x())
    cross_scroll_gate({"S_A": 0.9, "S_B": 0.9, "S_C": 0.9})
    assert list(tmp_path.iterdir()) == []


def test_module_never_branches_on_a_scroll_id():
    src = Path(pc.__file__).read_text(encoding="utf-8")
    assert "PHerc" not in src
