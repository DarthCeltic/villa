"""Null and sabotage controls on synthetic data, through the code this release ships.

Each control removes exactly one thing a detector could be relying on and requires the score to
fall to chance, so a score that survives the removal is evidence about the pipeline and not about
the signal:

* zero input        a detector fed nothing must not score above chance;
* label shuffle     permuted labels give the null distribution the real score has to beat;
* phase sabotage    the amplitude spectrum is kept and the phase (the spatial structure) is randomised;
* depth sabotage    the layers along the sheet normal are permuted, or the window is moved off the sheet.

Everything is synthetic, seeded and offline. Nothing here says anything about a scroll: it shows that
these controls are wired correctly, that they can fail, and that the same seed replays the same bytes.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from argus.cli import cmd_demo as D
from argus.core import depth_composite as DC
from argus.core import metrics

SHUFFLES = 200
REAL_FLOOR = 0.85          # the demo detector on its own fixture must clear this, or the controls prove nothing
CHANCE_BAND = 0.06         # a sabotaged input must land within this of 0.5


def _fixture():
    surface, truth = D.fixture()
    return surface, truth


def _phase_scramble(a: np.ndarray, seed: int) -> np.ndarray:
    """Same amplitude spectrum, random phase: the marginal statistics survive, the structure does not."""
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft2(a)
    noise_phase = np.angle(np.fft.rfft2(rng.standard_normal(a.shape)))
    noise_phase[0, 0] = 0.0                                   # keep the mean
    return np.fft.irfft2(np.abs(spectrum) * np.exp(1j * noise_phase), s=a.shape).astype(np.float32)


# ------------------------------------------------------------------------- zero input

def test_the_real_detector_clears_the_floor_so_the_controls_mean_something():
    surface, truth = _fixture()
    assert metrics.auc(D.detect(surface), truth) > REAL_FLOOR


def test_zero_input_scores_exactly_at_chance_and_cannot_beat_the_real_score():
    surface, truth = _fixture()
    real = metrics.score(D.detect(surface), truth)
    zero = metrics.score(D.detect(np.zeros_like(surface)), truth)
    assert zero["auc"] == 0.5                                  # tied scores share their ranks: no ordering by luck
    assert zero["ap"] == pytest.approx(zero["n_positive"] / zero["n"])   # average precision of a constant is the prevalence
    assert zero["auc"] < real["auc"] and zero["ap"] < real["ap"]


def test_a_constant_detector_output_is_indistinguishable_from_zero_input():
    surface, truth = _fixture()
    for value in (0.0, 0.5, 7.0):
        assert metrics.auc(np.full(surface.shape, value, dtype=np.float32), truth) == 0.5


def test_non_finite_scores_are_refused_rather_than_ranked():
    surface, truth = _fixture()
    bad = D.detect(surface).copy()
    bad[0, 0] = np.nan
    with pytest.raises(metrics.MetricRefusal):
        metrics.auc(bad, truth)


# ------------------------------------------------------------------------- label shuffle

def _null_distribution(scores: np.ndarray, truth: np.ndarray, n: int = SHUFFLES) -> np.ndarray:
    out = []
    for seed in range(n):
        shuffled = np.random.default_rng(seed).permutation(truth.ravel()).reshape(truth.shape)
        out.append(metrics.auc(scores, shuffled))
    return np.asarray(out)


def test_shuffled_labels_centre_on_chance_and_the_real_score_beats_every_shuffle():
    surface, truth = _fixture()
    scores = D.detect(surface)
    real = metrics.auc(scores, truth)
    null = _null_distribution(scores, truth)
    assert abs(float(null.mean()) - 0.5) < 0.01
    assert float(null.max()) < real - 0.2                      # not one of the 200 permutations comes near the real score
    assert float((null >= real).mean()) == 0.0                 # empirical p below 1/200


def test_the_label_shuffle_control_can_fail():
    """A leaked score (the labels themselves) sits at the top of its own null: the control would say so."""
    surface, truth = _fixture()
    leaked = truth.astype(np.float32)
    null = _null_distribution(leaked, truth, n=50)
    assert metrics.auc(leaked, truth) == 1.0
    assert float(null.max()) < 0.6


def test_shuffling_preserves_the_label_count_so_only_the_association_is_removed():
    _, truth = _fixture()
    shuffled = np.random.default_rng(3).permutation(truth.ravel()).reshape(truth.shape)
    assert int(shuffled.sum()) == int(truth.sum()) and not np.array_equal(shuffled, truth)


# ------------------------------------------------------------------------- phase sabotage

def test_phase_scrambled_input_falls_to_chance_while_the_real_input_does_not():
    surface, truth = _fixture()
    real = metrics.auc(D.detect(surface), truth)
    scrambled = [metrics.auc(D.detect(_phase_scramble(surface, seed)), truth) for seed in range(20)]
    assert real > REAL_FLOOR
    assert abs(float(np.mean(scrambled)) - 0.5) < CHANCE_BAND
    assert max(scrambled) < real - 0.2


def test_phase_scrambling_keeps_the_amplitude_spectrum():
    surface, _ = _fixture()
    a = np.abs(np.fft.rfft2(surface))
    b = np.abs(np.fft.rfft2(_phase_scramble(surface, 1)))
    assert np.allclose(a, b, rtol=1e-3, atol=1e-2)


# ------------------------------------------------------------------------- depth sabotage

SHAPE = (48, 96, 96)      # z, y, x
SHEET_Z = 24
LAYERS = list(range(-8, 9))
CENTER = LAYERS.index(0)


def _volume():
    """Noise everywhere, plus a thin slab of signal (the synthetic ink) around the sheet at z = SHEET_Z."""
    _, truth = D.fixture()
    rng = np.random.default_rng(11)
    vol = rng.normal(0.5, 0.12, size=SHAPE).astype(np.float32)
    z = np.arange(SHAPE[0], dtype=np.float32)[:, None, None]
    slab = np.exp(-((z - SHEET_Z) ** 2) / (2 * 1.2 ** 2))
    vol += (0.45 * slab * truth[None, :, :]).astype(np.float32)
    return vol, truth


def _stack(vol, truth):
    yy, xx = np.meshgrid(np.arange(4, 92), np.arange(4, 92), indexing="ij")
    points = np.stack([np.full(yy.shape, float(SHEET_Z)), yy.astype(float), xx.astype(float)], axis=-1)
    normals = np.zeros_like(points)
    normals[..., 0] = 1.0                                     # the sheet normal is the z axis
    sampled = DC.sample_along_normal(vol, points, normals, LAYERS)
    return sampled.stack, truth[4:92, 4:92]


def test_the_depth_composite_at_the_sheet_recovers_the_signal():
    vol, truth = _volume()
    stack, t = _stack(vol, truth)
    image = DC.symmetric_composite(stack, CENTER, 2)
    assert metrics.auc(image, t) > 0.9


def test_shifting_the_layers_along_the_normal_removes_the_signal():
    """A circular shift moves every signal layer out of the composite window: the depth order is sabotaged."""
    vol, truth = _volume()
    stack, t = _stack(vol, truth)
    real = metrics.auc(DC.symmetric_composite(stack, CENTER, 2), t)
    shifted = stack[np.roll(np.arange(stack.shape[0]), 9)]
    assert abs(metrics.auc(DC.symmetric_composite(shifted, CENTER, 2), t) - 0.5) < CHANCE_BAND
    assert real > 0.9


def test_random_permutations_of_the_layers_lose_signal_on_average():
    vol, truth = _volume()
    stack, t = _stack(vol, truth)
    real = metrics.auc(DC.symmetric_composite(stack, CENTER, 2), t)
    aucs = [metrics.auc(DC.symmetric_composite(stack[np.random.default_rng(seed).permutation(stack.shape[0])], CENTER, 2), t)
            for seed in range(20)]
    assert float(np.mean(aucs)) < real - 0.1


def test_a_window_moved_off_the_sheet_reads_chance():
    vol, truth = _volume()
    stack, t = _stack(vol, truth)
    off = DC.symmetric_composite(stack, 2, 2)                 # layers -6..-2 around the sheet: outside the slab
    assert abs(metrics.auc(off, t) - 0.5) < CHANCE_BAND


def test_an_empty_volume_composites_to_a_constant_and_scores_exactly_chance():
    vol, truth = _volume()
    stack, t = _stack(np.zeros_like(vol), truth)
    image = DC.symmetric_composite(stack, CENTER, 2)
    assert float(np.ptp(image)) == 0.0
    assert metrics.auc(image, t) == 0.5


def test_a_window_outside_the_stack_is_refused_not_clipped():
    vol, truth = _volume()
    stack, _ = _stack(vol, truth)
    with pytest.raises(ValueError):
        DC.symmetric_composite(stack, 1, 4)


# ------------------------------------------------------------------------- deterministic replay

def _battery() -> dict:
    surface, truth = _fixture()
    scores = D.detect(surface)
    vol, vtruth = _volume()
    stack, t = _stack(vol, vtruth)
    return {
        "fixture_sha256": hashlib.sha256(surface.tobytes() + truth.tobytes()).hexdigest(),
        "real": metrics.score(scores, truth),
        "zero": metrics.score(D.detect(np.zeros_like(surface)), truth),
        "shuffle_mean": round(float(_null_distribution(scores, truth, n=25).mean()), 12),
        "phase": round(metrics.auc(D.detect(_phase_scramble(surface, 5)), truth), 12),
        "depth": round(metrics.auc(DC.symmetric_composite(stack, CENTER, 2), t), 12),
        "stack_sha256": hashlib.sha256(np.ascontiguousarray(stack).tobytes()).hexdigest(),
    }


def test_the_whole_battery_replays_to_identical_bytes():
    a = json.dumps(_battery(), sort_keys=True)
    b = json.dumps(_battery(), sort_keys=True)
    assert a == b
    assert hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest()


def test_a_different_seed_changes_the_null_so_replay_is_not_a_constant():
    surface, truth = _fixture()
    scores = D.detect(surface)
    one = np.random.default_rng(0).permutation(truth.ravel()).reshape(truth.shape)
    two = np.random.default_rng(1).permutation(truth.ravel()).reshape(truth.shape)
    assert metrics.auc(scores, one) != metrics.auc(scores, two)
