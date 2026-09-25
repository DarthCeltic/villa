"""Tests that the three exposure concepts stay separate and that UNKNOWN never passes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core.exposure import (AssetAvailability, Channel, ChannelFinding, Eligibility,
                                 ModelExposure, OperatorExposure, State,
                                 usable_as_model_heldout)


def all_clean(scroll, model="dinovol"):
    m = ModelExposure(scroll, model)
    for c in Channel:
        m.channels[c] = ChannelFinding(State.CLEAN, "checked", "test")
    return m


def full_assets(scroll):
    return AssetAvailability(scroll, labels=True, raw_ct=True, mesh_or_tifxyz=True,
                             axis_order="ZYX", physical_pitch_um=(2.4, 2.4, 2.4),
                             coverage_mask=True, supervision_mask=True)


class TestUnknownNeverPasses(unittest.TestCase):
    def test_a_fresh_exposure_record_is_all_unknown(self):
        m = ModelExposure("PHerc0841", "dinovol")
        self.assertEqual(len(m.unknown_channels), len(list(Channel)))
        self.assertEqual(m.eligibility(), Eligibility.INDETERMINATE)

    def test_unknown_is_not_treated_as_clean(self):
        m = ModelExposure("PHerc0841", "dinovol")
        m.channels[Channel.RAW_VOLUME_PRETRAINING] = ChannelFinding(
            State.CLEAN, "absent from the declared 11-volume pretraining set", "model card")
        self.assertEqual(m.eligibility(), Eligibility.INDETERMINATE,
                         "one checked channel must not qualify the whole scroll")
        self.assertFalse(m.may_be_called_fully_unseen())

    def test_fully_unseen_requires_every_channel_checked_and_clean(self):
        self.assertTrue(all_clean("PHerc0841").may_be_called_fully_unseen())

    def test_a_verdict_without_evidence_is_refused(self):
        with self.assertRaises(ValueError):
            ChannelFinding(State.CLEAN, "   ")
        with self.assertRaises(ValueError):
            ChannelFinding(State.EXPOSED, "")
        ChannelFinding(State.UNKNOWN, "")

    def test_an_indeterminate_scroll_cannot_be_a_heldout(self):
        m = ModelExposure("PHerc1667", "dinovol")
        ok, why = usable_as_model_heldout(m, full_assets("PHerc1667"))
        self.assertFalse(ok)
        self.assertIn("unverified channels", why)


class TestConceptsStaySeparate(unittest.TestCase):
    def test_local_labels_do_not_imply_model_exposure(self):
        """A label store on this disk says nothing about what the model trained on."""
        a = full_assets("PHerc0841")
        m = all_clean("PHerc0841")
        self.assertTrue(a.labels)
        self.assertEqual(m.exposed_channels, [])
        ok, _ = usable_as_model_heldout(m, a)
        self.assertTrue(ok, "asset availability must not contaminate a clean scroll")

    def test_operator_exposure_does_not_make_a_scroll_model_exposed(self):
        m = all_clean("PHerc0841")
        o = OperatorExposure("PHerc0841", seen=True,
                             where=["review-A", "review-B", "review-C"])
        self.assertEqual(m.exposed_channels, [])
        self.assertEqual(m.eligibility(operator_seen=True),
                         Eligibility.MODEL_UNSEEN_OPERATOR_SEEN)
        ok, label = usable_as_model_heldout(m, full_assets("PHerc0841"), o)
        self.assertTrue(ok)
        self.assertEqual(label, "MODEL_UNSEEN_OPERATOR_SEEN")

    def test_operator_exposure_must_say_where(self):
        with self.assertRaises(ValueError):
            OperatorExposure("PHerc0841", seen=True)

    def test_model_exposure_downgrades_to_development_only(self):
        m = all_clean("PHerc0009B")
        m.channels[Channel.RAW_VOLUME_PRETRAINING] = ChannelFinding(
            State.EXPOSED, "listed in the declared pretraining volumes", "model card")
        self.assertEqual(m.eligibility(), Eligibility.DEVELOPMENT_ONLY)
        self.assertFalse(m.may_be_called_fully_unseen())
        ok, why = usable_as_model_heldout(m, full_assets("PHerc0009B"))
        self.assertFalse(ok)
        self.assertIn("raw_volume_pretraining", why)


class TestProjection(unittest.TestCase):
    def test_labels_without_raw_ct_cannot_produce_a_cube(self):
        a = AssetAvailability("PHerc1667", labels=True, raw_ct=False)
        self.assertEqual(a.projection_state(),
                         "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE")

    def test_labels_and_ct_without_a_coordinate_map_still_cannot(self):
        a = AssetAvailability("PHerc1667", labels=True, raw_ct=True,
                              mesh_or_tifxyz=False, coordinate_map=False,
                              axis_order="ZYX", physical_pitch_um=(2.4, 2.4, 2.4))
        self.assertEqual(a.projection_state(),
                         "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE")

    def test_missing_pitch_or_axis_order_blocks_the_projection(self):
        base = dict(labels=True, raw_ct=True, mesh_or_tifxyz=True)
        self.assertEqual(AssetAvailability("PHerc0841", axis_order="ZYX",
                                           **base).projection_state(),
                         "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE")
        self.assertEqual(AssetAvailability("PHerc0841", physical_pitch_um=(2.4, 2.4, 2.4),
                                           **base).projection_state(),
                         "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE")

    def test_a_complete_bridge_is_constructible(self):
        self.assertEqual(full_assets("PHerc0841").projection_state(),
                         "RAW_CT_PAIR_CONSTRUCTIBLE")

    def test_an_unprojectable_scroll_is_refused_even_when_model_clean(self):
        """Clean model exposure with no usable raw pair is still refused."""
        m = all_clean("PHerc1667")
        a = AssetAvailability("PHerc1667", labels=True,
                              label_format="aligned-public-level2-zmean4-21slice-v1",
                              raw_ct=False)
        ok, why = usable_as_model_heldout(m, a)
        self.assertFalse(ok)
        self.assertEqual(why, "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE")

    def test_no_labels_is_distinct_from_unprojectable(self):
        self.assertEqual(AssetAvailability("PHerc1299").projection_state(), "NO_LABELS")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    res = unittest.TextTestRunner(verbosity=2).run(
        loader.loadTestsFromModule(__import__(__name__)))
    ok = res.testsRun - len(res.failures) - len(res.errors)
    print("selftest: %d/%d passed" % (ok, res.testsRun))
    raise SystemExit(0 if ok == res.testsRun else 1)
