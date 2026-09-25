"""Tests for the #1708 surface-consistency arm and its matched-smoothing control."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core.surface_consistency import (ADAPTER, FROZEN_RADIUS, enhance,
                                            matched_smoothing, three_arm_compare)


def sheet_volume(n=48, thickness=3, signal=0.10, seed=0, hot=120):
    """A planar sheet through a noisy volume: in-sheet signal plus off-sheet speckle."""
    rng = np.random.default_rng(seed)
    vol = rng.random((n, n, n))
    z0 = n // 2
    labels = np.zeros((n, n, n), dtype=bool)
    lo, hi = z0 - thickness // 2, z0 + thickness // 2 + 1
    vol[lo:hi] += signal
    labels[lo:hi] = True
    for _ in range(hot):
        z, y, x = rng.integers(0, n, 3)
        if not labels[z, y, x]:
            vol[z, y, x] = 1.0
    return np.clip(vol, 0, 1), labels


class TestOperator(unittest.TestCase):
    def test_radius_is_frozen_at_two(self):
        self.assertEqual(FROZEN_RADIUS, 2)

    def test_enhance_is_pure_and_never_overwrites_raw(self):
        vol, _ = sheet_volume()
        before = vol.copy()
        out = enhance(vol)
        self.assertTrue(np.array_equal(vol, before), "raw must survive untouched")
        self.assertIsNot(out, vol)

    def test_matched_smoothing_is_pure_too(self):
        vol, _ = sheet_volume()
        before = vol.copy()
        matched_smoothing(vol)
        self.assertTrue(np.array_equal(vol, before))

    def test_both_operators_preserve_shape_and_stay_in_range(self):
        vol, _ = sheet_volume()
        for f in (enhance, matched_smoothing):
            out = f(vol)
            self.assertEqual(out.shape, vol.shape)
            self.assertGreaterEqual(out.min(), 0.0)
            self.assertLessEqual(out.max(), 1.0)

    def test_a_2d_input_is_refused(self):
        for f in (enhance, matched_smoothing):
            with self.assertRaises(ValueError):
                f(np.zeros((8, 8)))

    def test_enhance_takes_the_max_of_three_plane_means(self):
        """On a volume varying along one axis only, the in-plane mean must dominate."""
        vol = np.zeros((16, 16, 16))
        vol[8] = 1.0
        out = enhance(vol)
        self.assertAlmostEqual(out[8, 8, 8], 1.0, places=9,
                               msg="the in-sheet plane mean is 1.0 and must be the max")
        self.assertLess(out[0, 8, 8], 0.5)


class TestThreeArms(unittest.TestCase):
    def test_the_harness_can_tell_the_three_arms_apart(self):
        """Without separability, any verdict from this harness is meaningless."""
        vol, labels = sheet_volume()
        r = three_arm_compare(vol, labels)
        aucs = {k: v["auc"] for k, v in r["arms"].items()}
        self.assertEqual(len(set(round(a, 6) for a in aucs.values())), 3,
                         "the three arms must produce distinguishable scores, got %s" % aucs)

    def test_all_three_arms_are_scored_on_identical_points(self):
        vol, labels = sheet_volume()
        r = three_arm_compare(vol, labels)
        ns = {v["n"] for v in r["arms"].values()}
        self.assertEqual(len(ns), 1, "arms scored on different supports are not comparable")

    def test_raw_is_reported_and_marked_preserved(self):
        vol, labels = sheet_volume()
        r = three_arm_compare(vol, labels)
        self.assertIn("raw", r["arms"])
        self.assertTrue(r["raw_preserved"])

    def test_a_gain_over_raw_alone_is_not_called_geometric(self):
        """The verdict must distinguish blur from geometry, not conflate them."""
        vol, labels = sheet_volume()
        r = three_arm_compare(vol, labels)
        a = r["arms"]
        if a["surface_consistency"]["auc"] <= a["matched_smoothing"]["auc"]:
            self.assertEqual(r["verdict"], "GAIN_EXPLAINED_BY_SMOOTHING")
        else:
            self.assertEqual(r["verdict"], "GEOMETRIC_GAIN")

    def test_sabotage_an_isotropic_blob_yields_no_geometric_gain(self):
        """With no sheet, surface-consistency must not be credited with geometry."""
        rng = np.random.default_rng(3)
        vol = rng.random((32, 32, 32))
        labels = vol > 0.85
        r = three_arm_compare(vol, labels)
        self.assertNotEqual(r["verdict"], "GEOMETRIC_GAIN",
                            "a structureless volume must not produce a geometric verdict")

    def test_a_degenerate_label_set_is_indeterminate_not_a_pass(self):
        vol, _ = sheet_volume()
        r = three_arm_compare(vol, np.ones(vol.shape, dtype=bool))
        self.assertEqual(r["verdict"], "INDETERMINATE")

    def test_the_result_denies_being_ink_evidence(self):
        vol, labels = sheet_volume()
        r = three_arm_compare(vol, labels)
        self.assertIn("cannot confer qualification", r["not_evidence_of"])


class TestAdapterPin(unittest.TestCase):
    def test_the_external_adapter_is_pinned_to_a_full_commit(self):
        self.assertEqual(len(ADAPTER["commit"]), 40)
        self.assertTrue(all(c in "0123456789abcdef" for c in ADAPTER["commit"]))

    def test_the_adapter_is_optional(self):
        self.assertTrue(ADAPTER["optional"])
        self.assertIn("run identically with the adapter absent", ADAPTER["note"])

    def test_the_licence_is_recorded(self):
        self.assertEqual(ADAPTER["licence"], "MIT")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    res = unittest.TextTestRunner(verbosity=2).run(
        loader.loadTestsFromModule(__import__(__name__)))
    ok = res.testsRun - len(res.failures) - len(res.errors)
    print("selftest: %d/%d passed" % (ok, res.testsRun))
    raise SystemExit(0 if ok == res.testsRun else 1)
