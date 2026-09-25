"""The eight sabotage tests for purpose-aware lineage."""
from __future__ import annotations

import unittest

from argus.core.lineage import (
    LineageRefusal, Node, evaluate, exclusion_buffer, regions_disjoint)

HI = ("PHercExample", "20000101000002")
OK = ("PHercExample", "20000101000001")

FL = "first_letters_ruling"
GP = "grand_prize"

TRAIN_R = [(0, 1000, 0, 1000)]
SUBMIT_R = [(5000, 6000, 5000, 6000)]
BUF = 64.0


def _checkpoint_trained_on_hires():
    hv = Node("volume", "2403", volume=HI)
    feats = Node("features", "train-feats", edges=[("DERIVED_FROM", hv)])
    labels = Node("labels", "train-labels", edges=[("DERIVED_FROM", hv)])
    return Node("checkpoint", "ckpt", edges=[("TRAINED_FROM", feats),
                                             ("SUPERVISED_BY", labels)])


def _eligible_inference_chain(ckpt, *, extra_pred_edges=(), mesh_from_hires=False):
    ev = Node("volume", "9362", volume=OK)
    if mesh_from_hires:
        hv = Node("volume", "2403", volume=HI)
        mesh = Node("mesh", "mesh", edges=[("GEOMETRY_FROM", hv)])
    else:
        mesh = Node("mesh", "mesh", edges=[("GEOMETRY_FROM", ev)])
    infer = Node("features", "infer-feats", edges=[("DERIVED_FROM", mesh),
                                                   ("READ_AT_INFERENCE", ev)])
    pred = Node("prediction", "pred",
                edges=[("DERIVED_FROM", infer), ("MODEL_USED", ckpt), *extra_pred_edges])
    return Node("submission", "sub", edges=[("DERIVED_FROM", pred)])


class TestSabotage(unittest.TestCase):

    def test_1_train_on_2403_infer_on_9362_is_ALLOWED(self):
        sub = _eligible_inference_chain(_checkpoint_trained_on_hires())
        d = evaluate(sub, policy=FL, train_regions=TRAIN_R, submit_regions=SUBMIT_R,
                     buffer_px=BUF)
        self.assertEqual(d["verdict"], "ALLOW_FIRST_LETTERS")
        self.assertIn(HI, d["training_volumes"])
        self.assertNotIn(HI, d["evidence_volumes"],
                         "training ancestry must not leak into the evidence closure")

    def test_2_prediction_opens_ONE_2403_file_is_REJECTED(self):
        hv = Node("volume", "2403-peek", volume=HI)
        sub = _eligible_inference_chain(_checkpoint_trained_on_hires(),
                                        extra_pred_edges=[("READ_AT_INFERENCE", hv)])
        d = evaluate(sub, policy=FL, train_regions=TRAIN_R, submit_regions=SUBMIT_R,
                     buffer_px=BUF)
        self.assertEqual(d["verdict"], "REJECT")
        self.assertIn(HI, d["ineligible_in_evidence"])

    def test_3_submitted_mesh_derived_from_2403_is_REJECTED(self):
        sub = _eligible_inference_chain(_checkpoint_trained_on_hires(), mesh_from_hires=True)
        d = evaluate(sub, policy=FL, train_regions=TRAIN_R, submit_regions=SUBMIT_R,
                     buffer_px=BUF)
        self.assertEqual(d["verdict"], "REJECT")
        self.assertIn(HI, d["ineligible_in_evidence"])

    def test_4_physically_overlapping_train_and_submit_areas_are_REJECTED(self):
        sub = _eligible_inference_chain(_checkpoint_trained_on_hires())
        d = evaluate(sub, policy=FL, train_regions=[(0, 1000, 0, 1000)],
                     submit_regions=[(500, 1500, 500, 1500)], buffer_px=BUF)
        self.assertFalse(d["allowed"])
        self.assertFalse(d["disjointness"]["proven"])

    def test_5_same_checkpoint_with_proven_disjoint_regions_is_ALLOWED(self):
        sub = _eligible_inference_chain(_checkpoint_trained_on_hires())
        d = evaluate(sub, policy=FL, train_regions=TRAIN_R, submit_regions=SUBMIT_R,
                     buffer_px=BUF)
        self.assertEqual(d["verdict"], "ALLOW_FIRST_LETTERS")
        self.assertTrue(d["disjointness"]["proven"])

    def test_6a_missing_overlap_proof_REFUSES(self):
        sub = _eligible_inference_chain(_checkpoint_trained_on_hires())
        d = evaluate(sub, policy=FL, train_regions=None, submit_regions=None, buffer_px=None)
        self.assertEqual(d["verdict"], "REFUSE")
        self.assertFalse(d["allowed"])

    def test_6b_unknown_lineage_edge_REFUSES(self):
        ck = _checkpoint_trained_on_hires()
        bad = Node("prediction", "p", edges=[("BORROWED_VIBES_FROM", ck)])
        sub = Node("submission", "s", edges=[("DERIVED_FROM", bad)])
        with self.assertRaises(LineageRefusal):
            evaluate(sub, policy=FL, train_regions=TRAIN_R, submit_regions=SUBMIT_R,
                     buffer_px=BUF)

    def test_7_identical_construction_under_GRAND_PRIZE_is_REJECTED(self):
        sub = _eligible_inference_chain(_checkpoint_trained_on_hires())
        d = evaluate(sub, policy=GP, train_regions=TRAIN_R, submit_regions=SUBMIT_R,
                     buffer_px=BUF)
        self.assertEqual(d["verdict"], "REJECT")
        self.assertIn(HI, d["impermissible_in_training"])

    def test_8_compressed_or_cached_2403_inference_input_is_still_2403(self):
        hv = Node("volume", "2403", volume=HI)
        compressed = Node("volume", "2403-volcomp-q8", edges=[("DERIVED_FROM", hv)])
        cached = Node("features", "cached-resampled", edges=[("DERIVED_FROM", compressed)])
        sub = _eligible_inference_chain(_checkpoint_trained_on_hires(),
                                        extra_pred_edges=[("READ_AT_INFERENCE", cached)])
        d = evaluate(sub, policy=FL, train_regions=TRAIN_R, submit_regions=SUBMIT_R,
                     buffer_px=BUF)
        self.assertEqual(d["verdict"], "REJECT")
        self.assertIn(HI, d["ineligible_in_evidence"])


class TestExclusionBuffer(unittest.TestCase):
    def test_every_term_is_required(self):
        with self.assertRaises(LineageRefusal):
            exclusion_buffer(receptive_field_radius_px=32, resampling_support_px=2,
                             max_augmentation_displacement_px=8,
                             registration_uncertainty_px=None)

    def test_the_buffer_is_the_sum_of_its_declared_terms(self):
        b = exclusion_buffer(receptive_field_radius_px=32, resampling_support_px=2,
                             max_augmentation_displacement_px=8,
                             registration_uncertainty_px=16)
        self.assertEqual(b["total_px"], 58.0)
        self.assertEqual(len(b["why_each"]), 4)

    def test_regions_touching_within_the_buffer_are_not_disjoint(self):
        r = regions_disjoint([(0, 100, 0, 100)], [(120, 200, 0, 100)], 64.0)
        self.assertFalse(r["proven"], "120 - 100 = 20 px apart, inside a 64 px buffer")

    def test_regions_beyond_the_buffer_are_disjoint(self):
        r = regions_disjoint([(0, 100, 0, 100)], [(200, 300, 0, 100)], 64.0)
        self.assertTrue(r["proven"])


if __name__ == "__main__":
    unittest.main()
