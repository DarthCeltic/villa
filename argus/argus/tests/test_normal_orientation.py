"""Tests for geometric normal orientation and the explicit TIFXYZ validity contract."""
from __future__ import annotations

import unittest

import numpy as np

from argus.core.contracts import Refusal
from argus.core.normal_orientation import (
    OrientationResult, gate_orientation_consistency, global_orientations, orient_normals,
    validity_from_tifxyz)


def _flat_normals(h, w):
    n = np.zeros((h, w, 3))
    n[..., 0] = 1.0
    return n


class TestOrientation(unittest.TestCase):
    def test_an_already_consistent_field_is_left_consistent(self):
        n = _flat_normals(6, 6)
        r = orient_normals(n, np.ones((6, 6), bool))
        self.assertEqual(r.n_components, 1)
        self.assertEqual(r.conflict_edges, 0)
        self.assertTrue(np.allclose(np.abs(r.oriented[..., 0]), 1.0))
        d = np.einsum("ijk,ijk->ij", r.oriented[:, :-1], r.oriented[:, 1:])
        self.assertTrue((d >= 0).all(), "neighbours must have non-negative dot products")

    def test_a_locally_flipped_patch_is_repaired(self):
        n = _flat_normals(6, 6)
        n[:, 3:] *= -1.0
        r = orient_normals(n, np.ones((6, 6), bool))
        d = np.einsum("ijk,ijk->ij", r.oriented[:, :-1], r.oriented[:, 1:])
        self.assertTrue((d >= 0).all(), "the flip must be propagated away, not preserved")
        self.assertEqual(r.conflict_edges, 0)

    def test_a_handedness_flipped_curved_sheet_is_repaired_or_rejected_never_silently_mixed(self):
        h, w = 8, 12
        th = np.linspace(-0.6, 0.6, w)[None, :].repeat(h, 0)
        n = np.stack([np.cos(th), np.sin(th), np.zeros_like(th)], axis=-1)
        n[:, w // 2:] *= -1.0
        r = orient_normals(n, np.ones((h, w), bool))
        acc = r.accepted
        d = np.einsum("ijk,ijk->ij", r.oriented[:, :-1], r.oriented[:, 1:])
        pair = acc[:, :-1] & acc[:, 1:]
        self.assertTrue((d[pair] >= 0).all(),
                        "no accepted neighbour pair may disagree in sign")

    def test_an_irreconcilable_component_is_rejected_whole(self):
        n = np.zeros((1, 3, 3))
        n[0, 0] = [1, 0, 0]
        n[0, 1] = [1, 0, 0]
        n[0, 2] = [-1, 0, 0]
        v = np.ones((1, 3), bool)
        r = orient_normals(n, v)
        self.assertEqual(r.conflict_edges, 0)
        d = np.einsum("ijk,ijk->ij", r.oriented[:, :-1], r.oriented[:, 1:])
        self.assertTrue((d >= 0).all())

    def test_disconnected_components_are_labelled_separately(self):
        n = _flat_normals(4, 7)
        v = np.ones((4, 7), bool)
        v[:, 3] = False
        r = orient_normals(n, v)
        self.assertGreaterEqual(r.n_components, 2)
        left = set(np.unique(r.component[:, :3]))
        right = set(np.unique(r.component[:, 4:]))
        self.assertTrue(left.isdisjoint(right), "the two sides must not share a component")

    def test_a_discontinuous_edge_does_not_propagate_a_sign(self):
        n = np.zeros((1, 2, 3))
        n[0, 0] = [1, 0, 0]
        n[0, 1] = [0, 1, 0]
        r = orient_normals(n, np.ones((1, 2), bool))
        self.assertEqual(r.discontinuous_edges, 1)
        self.assertEqual(r.n_components, 2, "an uninformative edge must not join components")

    def test_invalid_pixels_are_excluded_and_zeroed(self):
        n = _flat_normals(4, 4)
        v = np.ones((4, 4), bool)
        v[0, 0] = False
        r = orient_normals(n, v)
        self.assertFalse(r.accepted[0, 0])
        self.assertTrue(np.allclose(r.oriented[0, 0], 0.0))

    def test_both_global_orientations_are_returned_and_are_opposite(self):
        n = _flat_normals(3, 3)
        r = orient_normals(n, np.ones((3, 3), bool))
        gs = global_orientations(r)
        self.assertEqual(len(gs), 2)
        self.assertTrue(np.allclose(gs[0][1], -gs[1][1]),
                        "the two global orientations must be exact opposites")

    def test_the_field_hash_changes_when_the_field_does(self):
        n = _flat_normals(4, 4)
        a = orient_normals(n, np.ones((4, 4), bool))
        v = np.ones((4, 4), bool)
        v[1, 1] = False
        b = orient_normals(n, v)
        self.assertNotEqual(a.field_sha256, b.field_sha256)
        self.assertEqual(a.field_sha256, orient_normals(n, np.ones((4, 4), bool)).field_sha256)


class TestValidityContract(unittest.TestCase):
    def test_an_explicit_mask_wins(self):
        z = np.zeros((2, 2))
        m = np.array([[True, False], [False, True]])
        out = validity_from_tifxyz(z, z, z, explicit_mask=m)
        self.assertTrue((out == m).all())

    def test_minus_one_sentinel_is_honoured(self):
        z = np.array([[-1.0, 5.0]])
        out = validity_from_tifxyz(z, z, z, sentinel=-1)
        self.assertFalse(out[0, 0])
        self.assertTrue(out[0, 1])

    def test_zero_is_valid_when_zero_is_not_the_sentinel(self):
        z = np.zeros((1, 1))
        out = validity_from_tifxyz(z, z, z, sentinel=-1)
        self.assertTrue(out[0, 0])

    def test_nan_is_invalid_under_the_finiteness_rule(self):
        z = np.array([[np.nan, 1.0]])
        out = validity_from_tifxyz(z, z, z, sentinel=-1)
        self.assertFalse(out[0, 0])
        self.assertTrue(out[0, 1])

    def test_no_mask_no_sentinel_and_no_finiteness_rule_refuses(self):
        z = np.zeros((2, 2))
        with self.assertRaises(ValueError):
            validity_from_tifxyz(z, z, z, sentinel=None, require_finite=False)

    def test_a_mask_of_the_wrong_shape_refuses(self):
        z = np.zeros((2, 2))
        with self.assertRaises(ValueError):
            validity_from_tifxyz(z, z, z, explicit_mask=np.ones((3, 3), bool))


class TestOutOfVolumeIsTheGathersJob(unittest.TestCase):
    def test_validity_does_not_pretend_to_know_the_volume(self):
        z = np.array([[10_000.0]])
        out = validity_from_tifxyz(z, z, z, sentinel=-1)
        self.assertTrue(out[0, 0])




class TestPerComponentPairingSabotage(unittest.TestCase):
    """The failure a single global flip hides: two sheets reversed independently of each other."""

    def _two_sheets_independently_reversed(self):
        h, w = 6, 11
        n = np.zeros((h, w, 3))
        n[..., 0] = 1.0
        v = np.ones((h, w), bool)
        v[:, 5] = False
        n[:, 6:] *= -1.0
        return n, v

    def test_two_disconnected_sheets_are_separate_components(self):
        n, v = self._two_sheets_independently_reversed()
        r = orient_normals(n, v)
        cids = set(int(c) for c in r.component[r.accepted].ravel())
        self.assertEqual(len(cids), 2, "the gap must produce two components, not one")

    def test_each_component_gets_its_OWN_pair_not_one_global_flip(self):
        from argus.core.normal_orientation import component_orientations
        n, v = self._two_sheets_independently_reversed()
        r = orient_normals(n, v)
        comps = component_orientations(r)
        self.assertEqual(len(comps), 2, "both sheets must produce paired readings")
        for c in comps:
            self.assertEqual(len(c["orientations"]), 2)
            a = c["orientations"][0][1]
            b = c["orientations"][1][1]
            self.assertTrue(np.allclose(a[c["mask"]], -b[c["mask"]]),
                            "a component's two orientations must be exact opposites")
            other = ~c["mask"]
            self.assertTrue(np.allclose(a[other], 0.0),
                            "a component's orientation must not carry other components' pixels")

    def test_the_four_sign_assignments_are_reachable_per_component(self):
        from argus.core.normal_orientation import component_orientations
        n, v = self._two_sheets_independently_reversed()
        comps = component_orientations(orient_normals(n, v))
        combos = set()
        for i in (0, 1):
            for j in (0, 1):
                fi = comps[0]["orientations"][i][1]
                fj = comps[1]["orientations"][j][1]
                si = float(np.sign(fi[comps[0]["mask"]][:, 0].mean()))
                sj = float(np.sign(fj[comps[1]["mask"]][:, 0].mean()))
                combos.add((si, sj))
        self.assertEqual(len(combos), 4,
                         "all four independent sign assignments must be reachable; a single "
                         "global flip reaches only two")

    def test_a_global_flip_on_a_multi_component_field_REFUSES(self):
        n, v = self._two_sheets_independently_reversed()
        r = orient_normals(n, v)
        with self.assertRaises(ValueError):
            global_orientations(r)

    def test_a_global_flip_is_still_allowed_on_a_single_component(self):
        n = _flat_normals(4, 4)
        r = orient_normals(n, np.ones((4, 4), bool))
        self.assertEqual(len(global_orientations(r)), 2)


class TestThresholdProvenance(unittest.TestCase):
    def test_the_discontinuity_threshold_declares_itself_provisional(self):
        from argus.core.normal_orientation import THRESHOLD_PROVENANCE
        rec = THRESHOLD_PROVENANCE["DISCONTINUITY_COS"]
        self.assertEqual(rec["status"], "PROVISIONAL_UNCALIBRATED")
        self.assertIsNone(rec["derived_from"])
        self.assertIn("any target surface", rec["must_never_be_tuned_on"])
        self.assertIn("detector output", rec["must_never_be_tuned_on"])

    def test_certification_is_blocked_while_the_threshold_is_provisional(self):
        from argus.core.normal_orientation import certification_blocked_reasons
        reasons = certification_blocked_reasons()
        self.assertTrue(reasons, "a provisional threshold must block certification")
        self.assertTrue(any("DISCONTINUITY_COS" in r for r in reasons))


class TestOrientationConsistencyGate(unittest.TestCase):
    """`gate_orientation_consistency`: the automatic refusal `orient_normals` never had."""

    def test_a_consistent_field_passes_and_returns_no_conflict(self):
        n = _flat_normals(6, 6)
        record = gate_orientation_consistency(n, np.ones((6, 6), bool))
        self.assertEqual(record["verdict"], "PASS")
        self.assertEqual(record["n_rejected_pixels"], 0)
        self.assertEqual(record["rejected_fraction"], 0.0)

    def _index_half_defect_loop(self):
        """A 2x2 'director' loop whose vectors rotate by 180 degrees going once around: the classic construction for a field with NO consistent sign assignment."""
        n = np.zeros((2, 2, 3))
        n[0, 0] = [1.0, 0.0, 0.0]
        n[0, 1] = [0.70710678, 0.70710678, 0.0]
        n[1, 1] = [0.0, 1.0, 0.0]
        n[1, 0] = [-0.70710678, 0.70710678, 0.0]
        return n, np.ones((2, 2), bool)

    def test_the_solver_itself_finds_this_fixture_irreconcilable(self):
        """Sanity check on the fixture, independent of the gate: without this, a gate that never refuses and a fixture that never triggers it could both pass by accident."""
        n, v = self._index_half_defect_loop()
        r = orient_normals(n, v)
        self.assertEqual(len(r.rejected_components), 1)
        self.assertFalse(r.accepted.any())

    def test_an_irreconcilable_field_refuses_automatically(self):
        n, v = self._index_half_defect_loop()
        with self.assertRaises(Refusal) as ctx:
            gate_orientation_consistency(n, v)
        self.assertEqual(ctx.exception.cls, "GEOMETRY")
        record = ctx.exception.evidence
        self.assertEqual(record["verdict"], "REFUSE")
        self.assertEqual(record["n_rejected_pixels"], record["n_valid_pixels"])
        self.assertEqual(record["rejected_fraction"], 1.0)

    def test_a_nonzero_tolerance_admits_a_small_rejected_fraction(self):
        """The default tolerance (0.0) is the conservative choice; a caller may explicitly widen it, and only then does a small rejected fraction pass."""
        n, v = self._index_half_defect_loop()
        record = gate_orientation_consistency(n, v, max_rejected_fraction=1.0)
        self.assertEqual(record["verdict"], "PASS")

    def test_the_gate_carries_the_solver_s_certification_status(self):
        """A PASS here must not read as certified: orient_normals's own discontinuity threshold is provisional, and this gate inherits that rather than resetting it."""
        n = _flat_normals(4, 4)
        record = gate_orientation_consistency(n, np.ones((4, 4), bool))
        self.assertTrue(record["certification_blocked_reasons"])
        self.assertTrue(any("DISCONTINUITY_COS" in r
                            for r in record["certification_blocked_reasons"]))

    def test_the_gate_reuses_the_solver_it_does_not_reimplement(self):
        """REGRESSION GUARD against a duplicate propagation implementation drifting from `orient_normals`: the gate's field hash must match a direct call for the same input."""
        n, v = self._index_half_defect_loop()
        try:
            gate_orientation_consistency(n, v)
        except Refusal as exc:
            gate_hash = exc.evidence["field_sha256"]
        else:
            raise AssertionError("fixture was expected to refuse")
        self.assertEqual(gate_hash, orient_normals(n, v).field_sha256)


if __name__ == "__main__":
    unittest.main()
