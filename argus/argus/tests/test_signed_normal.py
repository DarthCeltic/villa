"""Signed-normal resolver: sign is an estimate with a confidence, and only winding can earn TRUSTED."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core import signed_normal as sn
from argus.core.signed_normal import SignDecision


def _winding_samples(n=200, slope=0.2, seed=0, noise=0.01):
    """w rises along +x; +normal = +x, so w_plus > w_minus and the true sign is -1."""
    rng = np.random.default_rng(seed)
    d = 0.5
    x = rng.uniform(-5, 5, n)
    w_plus = slope * (x + d) + rng.normal(0, noise, n)
    w_minus = slope * (x - d) + rng.normal(0, noise, n)
    return w_plus, w_minus


def _trusted(sign=1, fold=0.0, method=sn.WINDING_FIELD, confirmed=True):
    return SignDecision(sign=sign, method=method, confidence_level=sn.TRUSTED,
                        agreement_fraction=0.99, n_valid=100, mean_winding_delta=0.2,
                        fold_region_fraction=fold,
                        independent_evidence=[sn.MANUAL_VERIFICATION] if confirmed else [])


def test_winding_monotone_gradient_is_trusted_and_matches_gradient():
    wp, wm = _winding_samples()
    dec = sn.sign_from_winding(wp, wm)
    assert dec.confidence_level == sn.TRUSTED
    assert dec.sign == -1
    assert dec.method == sn.WINDING_FIELD
    assert dec.agreement_fraction >= sn.TRUSTED_MIN_AGREEMENT
    assert dec.mean_winding_delta >= sn.TRUSTED_MIN_MEAN_DELTA
    assert not dec.ambiguity
    flipped = sn.sign_from_winding(wm, wp)
    assert flipped.sign == 1 and flipped.confidence_level == sn.TRUSTED


def test_winding_noisy_55_45_is_uncertain_and_ambiguous():
    n = 100
    w_minus = np.zeros(n)
    w_plus = np.where(np.arange(n) < 55, -0.3, 0.3)
    dec = sn.sign_from_winding(w_plus, w_minus)
    assert dec.confidence_level == sn.UNCERTAIN
    assert dec.ambiguity is True
    assert dec.agreement_fraction == pytest.approx(0.55)
    assert dec.sign == 1


def test_winding_tied_votes_have_no_sign():
    dec = sn.sign_from_winding(np.array([-1.0, 1.0] * 10), np.zeros(20))
    assert dec.sign is None
    assert dec.confidence_level == sn.UNCERTAIN and dec.ambiguity


def test_winding_too_many_nans_is_refused():
    wp, wm = _winding_samples(n=100)
    wp[:80] = np.nan
    dec = sn.sign_from_winding(wp, wm)
    assert dec.confidence_level == sn.REFUSED
    assert dec.sign is None
    assert dec.n_valid == 20


def test_winding_too_few_absolute_samples_is_refused():
    dec = sn.sign_from_winding(np.array([0.0, 0.1]), np.array([1.0, 1.1]))
    assert dec.confidence_level == sn.REFUSED


def test_winding_weak_gradient_cannot_be_trusted():
    wp, wm = _winding_samples(slope=0.02, noise=0.0)
    dec = sn.sign_from_winding(wp, wm, min_delta=0.001)
    assert dec.agreement_fraction == 1.0
    assert dec.mean_winding_delta < sn.TRUSTED_MIN_MEAN_DELTA
    assert dec.confidence_level == sn.UNCERTAIN


def test_winding_high_fold_fraction_blocks_trusted():
    wp, wm = _winding_samples()
    dec = sn.sign_from_winding(wp, wm, fold_region_fraction=0.3)
    assert dec.confidence_level == sn.UNCERTAIN
    assert dec.fold_region_fraction == 0.3


def test_winding_shape_mismatch_raises():
    with pytest.raises(ValueError):
        sn.sign_from_winding(np.zeros(4), np.zeros(5))


def _grid_pairs(h, w):
    idx = np.arange(h * w).reshape(h, w)
    return np.concatenate([
        np.stack([idx[:, :-1].ravel(), idx[:, 1:].ravel()], axis=1),
        np.stack([idx[:-1, :].ravel(), idx[1:, :].ravel()], axis=1),
    ])


def test_neighbors_consistent_field_capped_at_uncertain():
    consistency, dec = sn.sign_from_neighbors(np.ones(100), _grid_pairs(10, 10))
    assert consistency == 1.0
    assert dec.method == sn.NEIGHBOR_AGREEMENT
    assert dec.confidence_level == sn.UNCERTAIN
    assert dec.sign == 1


def test_neighbors_noisy_field_is_hint_only_and_too_few_pairs_refused():
    rng = np.random.default_rng(1)
    noisy = rng.choice([-1.0, 1.0], size=100)
    consistency, dec = sn.sign_from_neighbors(noisy, _grid_pairs(10, 10))
    assert consistency < sn.TRUSTED_MIN_AGREEMENT
    assert dec.confidence_level == sn.HINT_ONLY and dec.ambiguity
    _, tiny = sn.sign_from_neighbors(np.array([1.0, np.nan, 1.0]), np.array([[0, 1], [1, 2]]))
    assert tiny.confidence_level == sn.REFUSED


def test_neighbors_out_of_range_pairs_raise():
    with pytest.raises(ValueError):
        sn.sign_from_neighbors(np.ones(4), np.array([[0, 9]]))


def _cylinder(n=300, seed=3, inward=True):
    rng = np.random.default_rng(seed)
    theta = rng.uniform(0, 2 * np.pi, n)
    z = rng.uniform(0, 50, n)
    r = 20.0
    pts = np.stack([r * np.cos(theta), r * np.sin(theta), z], axis=1)
    radial_out = np.stack([np.cos(theta), np.sin(theta), np.zeros(n)], axis=1)
    umb = np.stack([np.zeros(51), np.zeros(51), np.arange(51.0)], axis=1)
    return pts, (-radial_out if inward else radial_out), umb


def test_umbilicus_never_above_hint_only_even_when_perfectly_consistent():
    pts, nrm, umb = _cylinder(inward=True)
    dec = sn.sign_from_umbilicus(pts, nrm, umb)
    assert dec.method == sn.UMBILICUS_RADIAL
    assert dec.confidence_level == sn.HINT_ONLY
    assert dec.sign == 1
    assert dec.agreement_fraction == 1.0
    assert any("does not solve sign" in r for r in dec.reasons)
    out = sn.sign_from_umbilicus(pts, -nrm, umb)
    assert out.sign == -1 and out.confidence_level == sn.HINT_ONLY


def test_umbilicus_too_few_points_refused_and_single_point_axis_ok():
    pts, nrm, umb = _cylinder(n=3)
    assert sn.sign_from_umbilicus(pts, nrm, umb).confidence_level == sn.REFUSED
    pts = np.tile([[10.0, 0.0, 0.0]], (20, 1))
    nrm = np.tile([[-1.0, 0.0, 0.0]], (20, 1))
    dec = sn.sign_from_umbilicus(pts, nrm, np.zeros(3))
    assert dec.sign == 1 and dec.confidence_level == sn.HINT_ONLY


def test_combine_prefers_trusted_winding_and_records_dissent():
    winding = _trusted(sign=1)
    umb = SignDecision(sign=-1, method=sn.UMBILICUS_RADIAL, confidence_level=sn.HINT_ONLY,
                       agreement_fraction=0.95, n_valid=200)
    out = sn.combine([umb, winding])
    assert out.sign == 1 and out.method == sn.WINDING_FIELD
    assert out.confidence_level == sn.TRUSTED
    assert any("disagrees" in h["what"] for h in out.history)
    assert winding.history == []


def test_combine_weak_agreement_never_becomes_trusted():
    weak = [
        SignDecision(sign=1, method=sn.UMBILICUS_RADIAL, confidence_level=sn.HINT_ONLY, agreement_fraction=0.99, n_valid=500),
        SignDecision(sign=1, method=sn.NEIGHBOR_AGREEMENT, confidence_level=sn.UNCERTAIN, agreement_fraction=0.99, n_valid=500),
        SignDecision(sign=1, method=sn.LOCAL_WINDING_CONSTRAINT, confidence_level=sn.UNCERTAIN, agreement_fraction=0.95, n_valid=500),
    ]
    out = sn.combine(weak)
    assert out.confidence_level != sn.TRUSTED
    assert out.confidence_level == sn.UNCERTAIN
    assert out.sign == 1
    only_umbilicus = sn.combine(weak[:1])
    assert only_umbilicus.confidence_level == sn.HINT_ONLY


def test_combine_weak_disagreement_is_ambiguous_and_refused_when_empty():
    a = SignDecision(sign=1, method=sn.NEIGHBOR_AGREEMENT, confidence_level=sn.UNCERTAIN, n_valid=10)
    b = SignDecision(sign=-1, method=sn.UMBILICUS_RADIAL, confidence_level=sn.HINT_ONLY, n_valid=10)
    out = sn.combine([a, b])
    assert out.ambiguity and out.confidence_level == sn.UNCERTAIN
    refused = SignDecision(sign=None, method=sn.WINDING_FIELD, confidence_level=sn.REFUSED)
    assert sn.combine([refused]).confidence_level == sn.REFUSED
    assert sn.combine([]).confidence_level == sn.REFUSED


def test_combine_conflicting_trusted_decisions_cancel():
    out = sn.combine([_trusted(sign=1), _trusted(sign=-1, method=sn.MANUAL)])
    assert out.confidence_level == sn.UNCERTAIN and out.sign is None and out.ambiguity


def test_manual_flip_appends_history_inverts_sign_and_does_not_mutate():
    original = _trusted(sign=1)
    before = original.to_dict()
    flipped = sn.apply_manual_verification(original, by="alice", verified=True, flip=True,
                                           note="checked in viewer", when="2026-01-01T00:00:00+00:00")
    assert original.to_dict() == before
    assert flipped is not original and flipped.history is not original.history
    assert flipped.sign == -1
    assert flipped.method == sn.MANUAL
    assert flipped.confidence_level == sn.TRUSTED
    assert len(flipped.history) == 1
    entry = flipped.history[0]
    assert entry["who"] == "alice" and entry["prior_sign"] == 1 and entry["flipped"] is True
    assert entry["prior_method"] == sn.WINDING_FIELD
    again = sn.apply_manual_verification(flipped, by="sam", verified=True)
    assert len(again.history) == 2 and again.history[0] == entry and again.sign == -1


def test_manual_unverified_is_never_trusted():
    out = sn.apply_manual_verification(_trusted(), by="alice", verified=False)
    assert out.confidence_level == sn.UNCERTAIN and out.ambiguity
    hint = SignDecision(sign=1, method=sn.UMBILICUS_RADIAL, confidence_level=sn.HINT_ONLY)
    assert sn.apply_manual_verification(hint, by="alice", verified=False).confidence_level == sn.HINT_ONLY
    with pytest.raises(ValueError):
        sn.apply_manual_verification(_trusted(), by=" ", verified=True)
    unsigned = SignDecision(sign=None, method=sn.WINDING_FIELD, confidence_level=sn.REFUSED)
    with pytest.raises(ValueError):
        sn.apply_manual_verification(unsigned, by="alice", verified=True)


def test_fold_report_detects_planted_flipped_patch():
    field = np.ones((40, 40))
    field[10:20, 12:24] = -1.0
    rep = sn.fold_region_report(field)
    assert rep.n_regions == 1
    assert len(rep.region_sizes) == 1
    assert rep.region_sizes[0] >= 10 * 12
    assert rep.region_sizes[0] <= 12 * 14
    assert 0.0 < rep.fold_fraction < 0.2
    assert rep.fold_mask[15, 18] and not rep.fold_mask[0, 0]


def test_fold_report_two_patches_clean_field_and_nans():
    field = np.ones((50, 50))
    field[5:10, 5:10] = -1.0
    field[30:38, 30:40] = -1.0
    rep = sn.fold_region_report(field)
    assert rep.n_regions == 2
    assert rep.region_sizes == sorted(rep.region_sizes, reverse=True)
    clean = sn.fold_region_report(np.ones((10, 10)))
    assert clean.n_regions == 0 and clean.fold_fraction == 0.0 and clean.region_sizes == []
    nans = sn.fold_region_report(np.full((5, 5), np.nan))
    assert np.isnan(nans.fold_fraction) and nans.n_valid_vertices == 0


def test_fold_report_grid_shape_and_external_mask():
    flat = np.ones(30)
    ext = np.zeros((5, 6), dtype=bool)
    ext[1:3, 1:3] = True
    rep = sn.fold_region_report(flat, ext, grid_shape=(5, 6))
    assert rep.n_regions == 1 and rep.region_sizes == [4]
    with pytest.raises(ValueError):
        sn.fold_region_report(flat, grid_shape=(4, 6))
    with pytest.raises(ValueError):
        sn.fold_region_report(flat)


def test_performance_by_region_reports_inside_and_outside_separately():
    score = np.full((10, 10), 0.8)
    mask = np.zeros((10, 10), dtype=bool)
    mask[:2, :] = True
    score[mask] = 0.2
    score[9, 9] = np.nan
    res = sn.performance_by_region(score, mask)
    assert res["fold_mean"] == pytest.approx(0.2)
    assert res["nonfold_mean"] == pytest.approx(0.8)
    assert res["n_fold"] == 20 and res["n_nonfold"] == 79
    assert res["reported_separately"] is True
    assert sn.performance_by_region(score, np.zeros_like(mask))["fold_mean"] is None


@pytest.mark.parametrize("decision, expected", [
    (_trusted(fold=0.0), True),
    (_trusted(fold=0.02), True),
    (_trusted(fold=0.05), False),
    (_trusted(fold=0.5), False),
    (_trusted(fold=0.0, confirmed=False), False),
    (SignDecision(sign=1, method=sn.WINDING_FIELD, confidence_level=sn.TRUSTED, fold_region_fraction=0.0,
                  independent_evidence=[sn.MANUAL_VERIFICATION], orientation_conflict=True), False),
    (SignDecision(sign=1, method=sn.MANUAL, confidence_level=sn.TRUSTED, fold_region_fraction=None,
                  independent_evidence=[sn.MANUAL_VERIFICATION]), False),
    (SignDecision(sign=None, method=sn.WINDING_FIELD, confidence_level=sn.TRUSTED, fold_region_fraction=0.0), False),
    (SignDecision(sign=1, method=sn.WINDING_FIELD, confidence_level=sn.UNCERTAIN, fold_region_fraction=0.0), False),
    (SignDecision(sign=1, method=sn.UMBILICUS_RADIAL, confidence_level=sn.HINT_ONLY, fold_region_fraction=0.0), False),
    (SignDecision(sign=1, method=sn.WINDING_FIELD, confidence_level=sn.REFUSED, fold_region_fraction=0.0), False),
    (SignDecision(sign=1, method=sn.WINDING_FIELD, confidence_level=sn.TRUSTED, fold_region_fraction=0.0, ambiguity=True), False),
])
def test_interior_only_allowed_matrix(decision, expected):
    ok, reason = sn.interior_only_allowed(decision)
    assert ok is expected
    assert isinstance(reason, str) and reason


def test_to_dict_is_json_safe():
    import json

    dec = sn.apply_manual_verification(_trusted(), by="r", verified=True, flip=True, when="t")
    d = dec.to_dict()
    json.dumps(d)
    d["history"].append({"x": 1})
    assert len(dec.history) == 1
    nan_dec = SignDecision(sign=None, method=sn.MANUAL, confidence_level=sn.REFUSED,
                           agreement_fraction=float("nan"))
    assert nan_dec.to_dict()["agreement_fraction"] is None


def test_a_bare_winding_sign_is_trusted_but_orientation_is_unconfirmed():
    wp, wm = _winding_samples()
    dec = sn.sign_from_winding(wp, wm)
    assert dec.confidence_level == sn.TRUSTED
    assert dec.orientation_status == sn.ORIENTATION_UNCONFIRMED
    ok, reason = sn.interior_only_allowed(dec)
    assert ok is False and "ORIENTATION_UNCONFIRMED" in reason
    rec = sn.recommended_composite(dec)
    assert rec["composite"] == "SYMMETRIC" and rec["orientation_status"] == sn.ORIENTATION_UNCONFIRMED
    assert rec["max_one_sided_depth_layers"] == sn.ONE_SIDED_MAX_DEPTH_LAYERS == 4


def test_manual_verification_confirms_orientation_and_a_flip_discards_prior_evidence():
    wp, wm = _winding_samples()
    dec = sn.sign_from_winding(wp, wm)
    ok = sn.apply_manual_verification(dec, by="r", verified=True, when="t")
    assert ok.orientation_status == sn.ORIENTATION_CONFIRMED and sn.interior_only_allowed(ok)[0] is True
    assert sn.recommended_composite(ok)["composite"] == "ONE_SIDED_INTERIOR"
    cued = sn.apply_independent_cue(dec, cue="PHOTOMETRIC", agrees=True, by="r", when="t")
    assert cued.orientation_status == sn.ORIENTATION_CONFIRMED
    flipped = sn.apply_manual_verification(cued, by="r", verified=False, flip=True, when="t")
    assert flipped.independent_evidence == [] and flipped.orientation_status == sn.ORIENTATION_UNCONFIRMED
    assert sn.interior_only_allowed(flipped)[0] is False


def test_an_unverified_manual_touch_never_confirms_orientation():
    dec = sn.apply_manual_verification(_trusted(confirmed=False), by="r", verified=False, when="t")
    assert dec.orientation_status == sn.ORIENTATION_UNCONFIRMED and dec.confidence_level == sn.UNCERTAIN


def test_a_conflicting_independent_cue_is_a_conflict_not_a_confirmation():
    dec = _trusted(confirmed=True)
    bad = sn.apply_independent_cue(dec, cue="FIBER_DIRECTION", agrees=False, by="r", when="t")
    assert bad.orientation_status == sn.ORIENTATION_CONFLICT
    assert bad.confidence_level == sn.HINT_ONLY and bad.independent_evidence == []
    assert sn.interior_only_allowed(bad)[0] is False
    assert bad.history[-1]["agrees"] is False
    resolved = sn.apply_manual_verification(bad, by="r", verified=True, when="t")
    assert resolved.orientation_status == sn.ORIENTATION_CONFIRMED


def test_independent_cue_input_is_validated():
    dec = _trusted(confirmed=False)
    with pytest.raises(ValueError):
        sn.apply_independent_cue(dec, cue="RADIAL", agrees=True, by="r")
    with pytest.raises(ValueError):
        sn.apply_independent_cue(dec, cue="PHOTOMETRIC", agrees=True, by=" ")
    with pytest.raises(ValueError):
        sn.apply_independent_cue(SignDecision(sign=None, method=sn.WINDING_FIELD, confidence_level=sn.REFUSED),
                                 cue="PHOTOMETRIC", agrees=True, by="r")


def test_combine_never_manufactures_confirmation_and_keeps_conflicts():
    a = _trusted(confirmed=False)
    b = sn.apply_independent_cue(_trusted(sign=1, confirmed=False, method=sn.LOCAL_WINDING_CONSTRAINT),
                                 cue="PHOTOMETRIC", agrees=True, by="r", when="t")
    out = sn.combine([a, b])
    assert out.sign == 1 and out.orientation_status == sn.ORIENTATION_CONFIRMED
    assert sn.combine([a, _trusted(confirmed=False)]).orientation_status == sn.ORIENTATION_UNCONFIRMED
    conflicted = sn.apply_independent_cue(_trusted(confirmed=True), cue="PHOTOMETRIC", agrees=False, by="r", when="t")
    assert sn.combine([a, conflicted]).orientation_status == sn.ORIENTATION_CONFLICT


def test_to_dict_carries_the_orientation_status():
    d = _trusted(confirmed=False).to_dict()
    assert d["orientation_status"] == sn.ORIENTATION_UNCONFIRMED and d["independent_evidence"] == []
