"""Tests for the leave-one-scroll-out weight gate, on synthetic scroll ids and planted scores."""
import unittest

import numpy as np

from argus.core.scroll_generalization import (GateRefusal, HeldOutEvaluation, WeightIdentity,
                                              evaluate)

TRAIN_A, TRAIN_B = "PHerc9101", "PHerc9102"
HOLD_A, HOLD_B, HOLD_C = "PHerc9201", "PHerc9202", "PHerc9203"


def synth(n, auc_target, seed):
    """Scores/labels whose AUC lands near a target, so a scenario can be built on purpose."""
    rng = np.random.default_rng(seed)
    y = rng.random(n) < 0.3
    sep = (auc_target - 0.5) * 4.0
    s = rng.normal(0, 1, n) + y * sep
    return s, y


W = WeightIdentity(weight_id="w1", sha256="a" * 64,
                   trained_on_scrolls=(TRAIN_A, TRAIN_B))


class TestLeaveOneScrollOutGate(unittest.TestCase):

    def _evals(self, spec, weights=W):
        return [HeldOutEvaluation(scroll=s, weights=weights, scores=sc, labels=lb)
                for s, (sc, lb) in spec.items()]

    def test_a_detector_at_chance_on_every_holdout_is_rejected(self):
        spec = {HOLD_A: synth(4000, 0.50, 1),
                HOLD_B: synth(4000, 0.52, 2),
                HOLD_C: synth(4000, 0.50, 3)}
        out = evaluate(self._evals(spec))
        self.assertEqual(out["verdict"], "WEIGHTS_REJECTED")
        self.assertEqual(len(out["failing_scrolls"]), 3)
        self.assertIn("may not be used for target inference", out["consequence"])

    def test_one_weak_scroll_sinks_an_otherwise_strong_set(self):
        """No averaging: a single failing scroll rejects the weights."""
        spec = {HOLD_A: synth(4000, 0.94, 4),
                HOLD_B: synth(4000, 0.92, 5),
                HOLD_C: synth(4000, 0.56, 6)}
        out = evaluate(self._evals(spec))
        self.assertEqual(out["verdict"], "WEIGHTS_REJECTED")
        self.assertEqual(out["worst_scroll"], HOLD_C)
        self.assertEqual(out["failing_scrolls"], [HOLD_C])

    def test_genuine_transfer_qualifies(self):
        spec = {HOLD_A: synth(6000, 0.95, 7),
                HOLD_B: synth(6000, 0.93, 8),
                HOLD_C: synth(6000, 0.94, 9)}
        out = evaluate(self._evals(spec))
        self.assertEqual(out["verdict"], "WEIGHTS_QUALIFIED")
        self.assertGreaterEqual(out["worst_auc"], 0.80)
        self.assertGreaterEqual(out["worst_ci95"][0], 0.70)

    def test_a_contaminated_holdout_is_refused_at_construction(self):
        """A scroll in the training manifest is not a holdout, and never becomes one."""
        s, y = synth(2000, 0.99, 10)
        with self.assertRaises(GateRefusal) as cm:
            HeldOutEvaluation(scroll=TRAIN_B, weights=W, scores=s, labels=y)
        self.assertIn("training manifest", str(cm.exception))

    def test_undeclared_training_manifest_is_refused(self):
        """Empty exposure is not clean exposure."""
        with self.assertRaises(GateRefusal):
            WeightIdentity(weight_id="w", sha256="b" * 64, trained_on_scrolls=())

    def test_unidentified_weights_are_refused(self):
        with self.assertRaises(GateRefusal):
            WeightIdentity(weight_id="", sha256="c" * 64, trained_on_scrolls=("A",))

    def test_too_few_scrolls_is_a_refusal_not_a_pass(self):
        """Transfer on two scrolls is an anecdote; it must not read as generalization."""
        spec = {HOLD_A: synth(4000, 0.95, 11), HOLD_B: synth(4000, 0.94, 12)}
        with self.assertRaises(GateRefusal) as cm:
            evaluate(self._evals(spec))
        self.assertIn("anecdote", str(cm.exception))

    def test_empty_evidence_is_a_refusal(self):
        with self.assertRaises(GateRefusal):
            evaluate([])

    def test_duplicate_scroll_is_refused(self):
        s, y = synth(3000, 0.9, 13)
        e = HeldOutEvaluation(scroll=HOLD_A, weights=W, scores=s, labels=y)
        with self.assertRaises(GateRefusal):
            evaluate([e, e, e])

    def test_single_class_holdout_is_refused_not_scored(self):
        s, _ = synth(3000, 0.9, 14)
        good = self._evals({HOLD_A: synth(3000, 0.95, 15),
                            HOLD_B: synth(3000, 0.94, 16)})
        bad = HeldOutEvaluation(scroll=HOLD_C, weights=W, scores=s,
                                labels=np.zeros(3000, dtype=bool))
        with self.assertRaises(GateRefusal):
            evaluate(good + [bad])

    def test_windows_of_one_scroll_are_not_three_scrolls(self):
        """Six windows cut from one scroll are one holdout, not six."""
        from argus.core.scroll_generalization import canonical_scroll
        spec = {"9201_w%d" % i: synth(3000, 0.95, 30 + i) for i in range(6)}
        with self.assertRaises(GateRefusal) as cm:
            self._evals(spec)
        self.assertIn("window or segment", str(cm.exception))
        for bad in ("9201_w0", "seg-A_control", "PHerc9201_seg3", "9201", "scroll_one"):
            with self.assertRaises(GateRefusal):
                canonical_scroll(bad)
        for good in (HOLD_A, "PHerc9202B", HOLD_C, "PHercParis99"):
            self.assertEqual(canonical_scroll(good), good)

    def test_the_training_manifest_cannot_be_declared_with_window_names(self):
        """Exposure declared as windows would let a scroll hide from the contamination check."""
        with self.assertRaises(GateRefusal):
            WeightIdentity(weight_id="w", sha256="e" * 64,
                           trained_on_scrolls=(TRAIN_A + "_w001",))

    def test_the_verdict_is_deterministic(self):
        spec = {HOLD_A: synth(4000, 0.93, 17),
                HOLD_B: synth(4000, 0.88, 18),
                HOLD_C: synth(4000, 0.91, 19)}
        a = evaluate(self._evals(spec))
        b = evaluate(self._evals(spec))
        self.assertEqual(a["verdict"], b["verdict"])
        self.assertEqual(a["worst_ci95"], b["worst_ci95"])

    def test_result_carries_the_metric_rule_id(self):
        spec = {HOLD_A: synth(3000, 0.9, 20),
                HOLD_B: synth(3000, 0.9, 21),
                HOLD_C: synth(3000, 0.9, 22)}
        self.assertEqual(evaluate(self._evals(spec))["metric_rule_id"], "argus-metric-v1")


if __name__ == "__main__":
    unittest.main()
