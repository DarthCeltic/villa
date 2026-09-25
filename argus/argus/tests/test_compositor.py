"""Tests for valid-core compositing, including the villa PR #1646 padding fixture."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core.compositor import (Tile, composite, coverage, normalize_valid,
                                   plan_tiles, valid_mask)

H, W, PATCH, HALO = 200, 173, 64, 8


def _local_detector(img):
    """A shift-equivariant 3x3 op: receptive field 1, far inside any halo we use."""
    p = np.pad(img.astype(np.float64), 1, mode="edge")
    acc = np.zeros_like(img, dtype=np.float64)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            acc += p[dy:dy + img.shape[0], dx:dx + img.shape[1]]
    return acc / 9.0


class TestPlan(unittest.TestCase):
    def test_cores_partition_the_output_exactly(self):
        cov = coverage(plan_tiles(H, W, PATCH, HALO), H, W)
        self.assertTrue((cov == 1).all(),
                        "every pixel must be written exactly once; found counts %s"
                        % sorted(set(cov.ravel().tolist())))

    def test_no_core_escapes_the_output(self):
        for t in plan_tiles(H, W, PATCH, HALO):
            self.assertGreaterEqual(t.y0, 0)
            self.assertGreaterEqual(t.x0, 0)
            self.assertLessEqual(t.y0 + t.h, H)
            self.assertLessEqual(t.x0 + t.w, W)

    def test_every_core_pixel_sits_a_full_halo_from_the_read_edge(self):
        """The guarantee itself: interior pixels are never border pixels of their patch."""
        for t in plan_tiles(H, W, PATCH, HALO):
            cy, cx, ch, cw = t.core_in_read
            self.assertGreaterEqual(cy, HALO)
            self.assertGreaterEqual(cx, HALO)
            self.assertGreaterEqual(t.rh - (cy + ch), HALO)
            self.assertGreaterEqual(t.rw - (cx + cw), HALO)

    def test_padding_appears_only_at_the_volume_boundary(self):
        for t in plan_tiles(H, W, PATCH, HALO):
            touches_edge = (t.ry0 < 0 or t.rx0 < 0
                            or t.ry0 + t.rh > H or t.rx0 + t.rw > W)
            self.assertEqual(t.has_padding, touches_edge)

    def test_a_halo_that_leaves_no_core_is_refused(self):
        with self.assertRaises(ValueError):
            plan_tiles(H, W, 16, 8)


class TestPhaseInvariance(unittest.TestCase):
    """Composite the same image under shifted tilings; the result must not move."""

    def setUp(self):
        rng = np.random.default_rng(1646)
        self.img = rng.random((H, W))
        self.ref = _local_detector(self.img)

    def _valid_core(self, patch, halo):
        def run(tile: Tile, mask):
            buf = np.zeros((tile.rh, tile.rw))
            ys, xs = tile.ry0 + tile.vy0, tile.rx0 + tile.vx0
            buf[tile.vy0:tile.vy0 + tile.vh, tile.vx0:tile.vx0 + tile.vw] = \
                self.img[ys:ys + tile.vh, xs:xs + tile.vw]
            return _local_detector(buf)
        return composite(H, W, patch, halo, run)

    def test_valid_core_output_is_identical_across_tile_sizes(self):
        a = self._valid_core(64, 8)
        b = self._valid_core(48, 8)
        c = self._valid_core(80, 12)
        self.assertTrue(np.allclose(a, b, atol=1e-12),
                        "tiling at 64 and 48 disagree by %.3g" % np.abs(a - b).max())
        self.assertTrue(np.allclose(a, c, atol=1e-12),
                        "tiling at 64 and 80 disagree by %.3g" % np.abs(a - c).max())

    def test_valid_core_matches_whole_image_inference_in_the_interior(self):
        got = self._valid_core(64, 8)
        self.assertTrue(np.allclose(got[1:-1, 1:-1], self.ref[1:-1, 1:-1], atol=1e-12))

    def test_the_naive_compositor_fails_the_same_check(self):
        """Paired sabotage: without a halo the artefact is real and detectable."""
        def naive(patch):
            out = np.zeros((H, W))
            for y in range(0, H, patch):
                for x in range(0, W, patch):
                    h, w = min(patch, H - y), min(patch, W - x)
                    out[y:y + h, x:x + w] = _local_detector(self.img[y:y + h, x:x + w])
            return out
        a, b = naive(64), naive(48)
        self.assertFalse(np.allclose(a, b, atol=1e-12),
                         "the naive compositor must DIFFER across tilings, else this "
                         "suite cannot detect the artefact it claims to remove")
        self.assertGreater(np.abs(naive(64) - self.ref).max(), 1e-6)


class TestPaddingNormalization(unittest.TestCase):
    """villa PR #1646: reader padding must not enter the normalization statistics."""

    def test_padding_zeros_shift_the_statistics_when_included(self):
        rng = np.random.default_rng(0)
        real = rng.uniform(100, 200, size=(32, 32))
        buf = np.zeros((32, 64))
        buf[:, :32] = real
        mask = np.zeros((32, 64), dtype=bool)
        mask[:, :32] = True

        correct = normalize_valid(buf, mask)
        naive = normalize_valid(buf, np.ones_like(mask))
        got = correct[:, :32]
        bad = naive[:, :32]
        self.assertGreater(np.abs(got - bad).max(), 0.1,
                           "if these agree, the fixture cannot detect the bug it exists for")
        self.assertGreater(got.max() - got.min(), 0.5,
                           "excluding padding must preserve the real dynamic range")
        self.assertLess(bad.max() - bad.min(), got.max() - got.min(),
                        "including padding compresses the real signal, which is the defect")

    def test_short_depth_padding_is_excluded_too(self):
        rng = np.random.default_rng(1)
        vol = np.zeros((5, 16, 32), dtype=np.float64)
        vol[:, :, :16] = rng.uniform(50, 150, size=(5, 16, 16))
        mask = np.zeros((16, 32), dtype=bool)
        mask[:, :16] = True
        out = normalize_valid(vol, mask)
        self.assertEqual(out.shape, vol.shape)
        self.assertGreater(out[:, :, :16].max() - out[:, :, :16].min(), 0.5)

    def test_a_window_of_pure_padding_is_refused_not_normalized(self):
        with self.assertRaises(ValueError):
            normalize_valid(np.zeros((8, 8)), np.zeros((8, 8), dtype=bool))

    def test_mask_shape_mismatch_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_valid(np.zeros((8, 8)), np.zeros((4, 4), dtype=bool))

    def test_valid_mask_marks_exactly_the_real_data(self):
        for t in plan_tiles(H, W, PATCH, HALO):
            self.assertEqual(int(valid_mask(t).sum()), t.vh * t.vw)


class TestContract(unittest.TestCase):
    def test_a_wrong_shaped_patch_result_is_refused(self):
        with self.assertRaises(ValueError):
            composite(64, 64, 32, 8, lambda t, m: np.zeros((3, 3)))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__(__name__))
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    total = res.testsRun
    ok = total - len(res.failures) - len(res.errors)
    print("selftest: %d/%d passed" % (ok, total))
    raise SystemExit(0 if ok == total else 1)
