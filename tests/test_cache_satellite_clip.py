"""Unit tests for the plot-anchored 10 m clip helpers in cache_satellite_scenes.

These cover the pure-logic pieces added by PR10 (view-bounds padding, stretch,
folder naming) and the offline behaviour of _fetch_plot_scenes.  The heavy
rasterio/numpy render path is exercised only when the optional geospatial stack
is importable; the module is designed to import and run without it.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cache_satellite_scenes as cs


class TestViewBounds(unittest.TestCase):
    def test_tiny_plot_is_padded_to_min_view(self):
        # RM-P1: ~56 m x 67 m -> must be padded up to >= CLIP_MIN_VIEW_M per side.
        w, s, e, n = -52.5832, -3.2963, -52.5827, -3.2957
        vb = cs._clip_view_bounds([w, s, e, n])
        lat = (s + n) / 2.0
        m_lat = (vb[3] - vb[1]) * 111320.0
        m_lng = (
            (vb[2] - vb[0])
            * 111320.0
            * abs(__import__("math").cos(__import__("math").radians(lat)))
        )
        self.assertGreaterEqual(min(m_lat, m_lng), cs.CLIP_MIN_VIEW_M - 1)
        self.assertLessEqual(max(m_lat, m_lng), cs.CLIP_MAX_VIEW_M + 1)

    def test_plot_centre_is_preserved(self):
        bb = [-52.5832, -3.2963, -52.5827, -3.2957]
        vb = cs._clip_view_bounds(bb)
        self.assertAlmostEqual((vb[0] + vb[2]) / 2, (bb[0] + bb[2]) / 2, places=6)
        self.assertAlmostEqual((vb[1] + vb[3]) / 2, (bb[1] + bb[3]) / 2, places=6)

    def test_large_plot_is_capped(self):
        # A huge plot must not blow past CLIP_MAX_VIEW_M.
        bb = [-52.7, -3.5, -52.4, -3.2]
        vb = cs._clip_view_bounds(bb)
        m_lat = (vb[3] - vb[1]) * 111320.0
        self.assertLessEqual(m_lat, cs.CLIP_MAX_VIEW_M + 1)


class TestStretch(unittest.TestCase):
    def test_stretch_maps_to_uint8(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not available")
        a = np.array([[0, 10, 20, 30, 40, 50]], dtype="float32")
        out = cs._stretch(a, 2.0, 98.0)
        self.assertEqual(out.dtype, np.uint8)
        self.assertEqual(int(out.max()), 255)
        self.assertEqual(int(out.min()), 0)

    def test_flat_band_does_not_divide_by_zero(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not available")
        out = cs._stretch(np.full((4, 4), 7.0, dtype="float32"), 2.0, 98.0)
        self.assertEqual(out.shape, (4, 4))


class TestFolderName(unittest.TestCase):
    def test_slash_is_sanitised(self):
        self.assertEqual(cs._plot_dirname("a/b"), "plot_a_b")
        self.assertEqual(cs._plot_dirname("RM-P1"), "plot_RM-P1")


class TestFetchPlotScenesOffline(unittest.TestCase):
    """_fetch_plot_scenes must degrade gracefully when STAC returns nothing."""

    def test_no_features_returns_empty(self):
        orig = cs.query_stac
        cs.query_stac = lambda *a, **k: []
        try:
            with tempfile.TemporaryDirectory() as d:
                out, used, cb = cs._fetch_plot_scenes(
                    {"id": "X", "bbox": [0, 0, 0.001, 0.001]},
                    [0, 0, 0.001, 0.001],
                    None,
                    None,
                    cs.CLOUD_MAX,
                    d,
                    10,
                    10,
                    None,
                )
            self.assertEqual(out, [])
            self.assertEqual(used, 0)
            self.assertIsNone(cb)
        finally:
            cs.query_stac = orig


class TestMergePreservesNewest(unittest.TestCase):
    def test_merge_dedupes_and_sorts(self):
        a = [{"date": "2026-01-01"}, {"date": "2020-01-01"}]
        b = [{"date": "2026-01-01"}, {"date": "2024-01-01"}]
        m = cs.merge_scenes(a, b, 10)
        self.assertEqual(
            [s["date"] for s in m], ["2026-01-01", "2024-01-01", "2020-01-01"]
        )


if __name__ == "__main__":
    unittest.main()
