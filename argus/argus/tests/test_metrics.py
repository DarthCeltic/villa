"""Tests for the canonical metric, including the exact hazards that motivated it."""
import unittest

import numpy as np

from argus.core.metrics import (RULE_ID, MetricRefusal, auc, average_precision, score)


def fixture():
    """The frozen fixture: ties, a realistic prevalence, and rounded scores."""
    rng = np.random.default_rng(20260909)
    y = (rng.random(4000) < 0.31).astype(np.uint8)
    s = np.clip(0.5 + 0.25 * y + rng.normal(0, 0.3, 4000), 0, 1)
    return np.round(s, 3), y


class TestCanonicalMetric(unittest.TestCase):

    def test_auc_is_stable_on_the_frozen_fixture(self):
        s, y = fixture()
        v = auc(s, y)
        self.assertAlmostEqual(v, 0.717064203, places=9)

    def test_rank_definition_matches_the_pairwise_probability(self):
        """AUC must equal P(pos > neg) + 0.5 * P(pos == neg), computed by brute force."""
        rng = np.random.default_rng(7)
        s = np.round(rng.random(120), 2)
        y = rng.random(120) < 0.4
        pos, neg = s[y], s[~y]
        gt = (pos[:, None] > neg[None, :]).sum()
        eq = (pos[:, None] == neg[None, :]).sum()
        expect = (gt + 0.5 * eq) / (pos.size * neg.size)
        self.assertAlmostEqual(auc(s, y), expect, places=12)

    def test_integer_labels_are_accepted_and_agree_with_bool(self):
        s, y = fixture()
        self.assertEqual(auc(s, y), auc(s, y.astype(bool)))


    def test_non_binary_integer_labels_are_refused(self):
        """The hazard: a label array of 0/1/2 must not be silently reinterpreted."""
        s = np.linspace(0, 1, 30)
        y = np.arange(30) % 3
        with self.assertRaises(MetricRefusal):
            auc(s, y)

    def test_single_class_is_refused_not_scored_as_half(self):
        s = np.linspace(0, 1, 20)
        with self.assertRaises(MetricRefusal):
            auc(s, np.zeros(20, dtype=bool))
        with self.assertRaises(MetricRefusal):
            auc(s, np.ones(20, dtype=bool))

    def test_nan_is_refused_in_scores_and_labels(self):
        s = np.linspace(0, 1, 10)
        y = np.zeros(10, dtype=bool); y[:5] = True
        bad = s.copy(); bad[3] = np.nan
        with self.assertRaises(MetricRefusal):
            auc(bad, y)
        with self.assertRaises(MetricRefusal):
            auc(s, np.where(y, 1.0, np.nan))

    def test_shape_mismatch_and_empty_are_refused(self):
        with self.assertRaises(MetricRefusal):
            auc(np.zeros(10), np.zeros(9, dtype=bool))
        with self.assertRaises(MetricRefusal):
            auc(np.zeros(0), np.zeros(0, dtype=bool))

    def test_the_uint8_complement_hazard_cannot_recur(self):
        """`~y` on uint8 gives 254/255 and `r[y]` indexes by value."""
        s, y = fixture()
        self.assertEqual(y.dtype, np.uint8)
        self.assertAlmostEqual(auc(s, y), auc(s, y.astype(bool)), places=15)
        self.assertGreater(auc(s, y), 0.6)


    def test_average_precision_bounds_and_perfect_separation(self):
        y = np.array([0, 0, 1, 1], dtype=bool)
        self.assertAlmostEqual(average_precision(np.array([0.1, 0.2, 0.8, 0.9]), y), 1.0)
        v = average_precision(*fixture()[::-1][::-1])
        self.assertTrue(0.0 <= v <= 1.0)

    def test_score_bundle_carries_the_rule_id(self):
        s, y = fixture()
        out = score(s, y)
        self.assertEqual(out["rule_id"], RULE_ID)
        self.assertIn("auc", out)
        self.assertIn("ap", out)
        self.assertEqual(out["n"], 4000)


if __name__ == "__main__":
    unittest.main()
