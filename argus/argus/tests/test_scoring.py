"""Canonical scoring service: sklearn parity plus the sabotage fixtures."""
import unittest

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from argus.core.metrics import auc, average_precision
from argus.core.scoring import (AGGREGATIONS, Population, ScoringRefusal, score)


def planted(n, sep, seed, dp=None):
    rng = np.random.default_rng(seed)
    y = rng.random(n) < 0.3
    s = rng.normal(0, 1, n) + y * sep
    if dp is not None:
        s = np.round(s, dp)
    return s, y


class TestReferenceParity(unittest.TestCase):
    """Agreement with scikit-learn on planted fixtures, including heavy ties."""

    def test_auc_matches_sklearn(self):
        for seed, sep, dp in ((1, 1.5, None), (2, 0.4, None), (3, 2.5, 1), (4, 1.0, 2)):
            s, y = planted(5000, sep, seed, dp)
            self.assertAlmostEqual(auc(s, y), roc_auc_score(y, s), places=12,
                                   msg="seed=%d dp=%s" % (seed, dp))

    def test_auc_matches_sklearn_with_extreme_ties(self):
        rng = np.random.default_rng(9)
        y = rng.random(4000) < 0.3
        s = np.round(rng.normal(0, 1, 4000) * 0.3, 1)
        self.assertLess(len(np.unique(s)), 40)
        self.assertAlmostEqual(auc(s, y), roc_auc_score(y, s), places=12)

    def test_average_precision_matches_sklearn(self):
        for seed, sep in ((5, 1.5), (6, 0.6)):
            s, y = planted(4000, sep, seed)
            self.assertAlmostEqual(average_precision(s, y),
                                   average_precision_score(y, s), places=6)


class TestSabotageFixtures(unittest.TestCase):
    """Each case is an input that could yield a plausible wrong number."""

    def _p(self, s, y, valid=None):
        return [Population(scores=s, labels=y, valid=valid, group="g")]

    def test_bool_uint8_and_int64_labels_agree(self):
        s, y = planted(3000, 1.2, 10)
        a = score(self._p(s, y), aggregation="pooled", ci=False)["auc"]
        b = score(self._p(s, y.astype(np.uint8)), aggregation="pooled", ci=False)["auc"]
        c = score(self._p(s, y.astype(np.int64)), aggregation="pooled", ci=False)["auc"]
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_minus_one_sentinel_labels_are_refused(self):
        """A -1 sentinel must never be read as a negative class."""
        s, y = planted(1000, 1.0, 11)
        lab = y.astype(np.int64)
        lab[:50] = -1
        with self.assertRaises(ScoringRefusal) as cm:
            score(self._p(s, lab), aggregation="pooled")
        self.assertIn("outside {0,1}", str(cm.exception))

    def test_nan_scores_without_a_mask_are_refused(self):
        s, y = planted(1000, 1.0, 12)
        s[10] = np.nan
        with self.assertRaises(ScoringRefusal) as cm:
            score(self._p(s, y), aggregation="pooled")
        self.assertIn("no explicit `valid` mask", str(cm.exception))

    def test_nan_scores_with_a_mask_are_excluded_and_coverage_reported(self):
        s, y = planted(2000, 1.4, 13)
        v = np.ones(2000, bool)
        s[:100] = np.nan
        v[:100] = False
        out = score(self._p(s, y, v), aggregation="pooled", ci=False)
        self.assertEqual(out["n"], 1900)
        self.assertAlmostEqual(out["coverage"], 0.95, places=6)

    def test_constant_scores_give_exactly_half(self):
        y = np.zeros(1000, bool); y[:300] = True
        out = score(self._p(np.full(1000, 0.7), y), aggregation="pooled", ci=False)
        self.assertAlmostEqual(out["auc"], 0.5, places=12)

    def test_inverted_labels_invert_the_auc(self):
        s, y = planted(3000, 1.6, 14)
        a = score(self._p(s, y), aggregation="pooled", ci=False)["auc"]
        b = score(self._p(s, ~y), aggregation="pooled", ci=False)["auc"]
        self.assertAlmostEqual(a + b, 1.0, places=12)

    def test_empty_mask_is_refused(self):
        s, y = planted(500, 1.0, 15)
        with self.assertRaises(ScoringRefusal) as cm:
            score(self._p(s, y, np.zeros(500, bool)), aggregation="pooled")
        self.assertIn("selects nothing", str(cm.exception))

    def test_single_class_population_is_refused(self):
        s = np.linspace(0, 1, 400)
        with self.assertRaises(ScoringRefusal) as cm:
            score(self._p(s, np.zeros(400, bool)), aggregation="pooled")
        self.assertIn("single-class", str(cm.exception))

    def test_shape_mismatch_is_refused(self):
        with self.assertRaises(ScoringRefusal):
            score(self._p(np.zeros(10), np.zeros(9, bool)), aggregation="pooled")


class TestAggregationSemantics(unittest.TestCase):

    def test_aggregation_has_no_default(self):
        s, y = planted(1000, 1.0, 16)
        with self.assertRaises(TypeError):
            score([Population(s, y)])

    def test_unknown_aggregation_is_refused(self):
        s, y = planted(1000, 1.0, 17)
        with self.assertRaises(ScoringRefusal):
            score([Population(s, y)], aggregation="average")

    def test_macro_and_pooled_differ_and_both_are_labelled(self):
        """A big group and a tiny one: macro and pooled must not be confused."""
        big = Population(*planted(9000, 0.3, 18), group="big")
        tiny = Population(*planted(200, 3.0, 19), group="tiny")
        pooled = score([big, tiny], aggregation="pooled", ci=False)
        macro = score([big, tiny], aggregation="macro", ci=False)
        self.assertNotAlmostEqual(pooled["auc"], macro["auc"], places=3)
        self.assertEqual(pooled["aggregation"], "pooled")
        self.assertEqual(macro["aggregation"], "macro")
        self.assertIn("per_group", macro)
        self.assertIn("never be compared against a pooled number", macro["macro_note"])

    def test_every_result_carries_full_context(self):
        s, y = planted(4000, 1.2, 20)
        out = score([Population(s, y, group="g")], aggregation="segment")
        for k in ("auc", "ap", "prevalence", "lift", "n", "n_positive", "coverage",
                  "ci95", "service_id", "metric_rule_id", "aggregation", "tie_rule",
                  "nan_policy", "label_policy"):
            self.assertIn(k, out)
        self.assertEqual(len(out["ci95"]), 2)
        self.assertLess(out["ci95"][0], out["auc"])

    def test_all_declared_aggregations_are_accepted(self):
        s, y = planted(1500, 1.0, 21)
        for a in AGGREGATIONS:
            self.assertIn("auc", score([Population(s, y, group="g")], aggregation=a,
                                       ci=False))


if __name__ == "__main__":
    unittest.main()
