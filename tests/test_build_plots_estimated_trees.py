"""Tests for expected_trees + counted_trees on plots (Ask-2).

expected_trees: standard cacao grid (default 3.0 x 3.0 m => 1,111 trees/ha).
NEVER auto-estimated for mature / enrichment / research / nursery / infrastructure.
counted_trees: observed trees/index.geojson points inside the plot polygon.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_plots_geojson as bp

# ~0.01 deg square (~1.1 km sides ~ 125 ha).
RING = [
    [-52.585, -3.295],
    [-52.575, -3.295],
    [-52.575, -3.285],
    [-52.585, -3.285],
    [-52.585, -3.295],
]


class TestRingArea(unittest.TestCase):
    def test_area_is_positive(self):
        a = bp.ring_area_ha(RING)
        self.assertIsNotNone(a)
        self.assertGreater(a, 0)

    def test_degenerate_ring_is_none(self):
        self.assertIsNone(bp.ring_area_ha([[-52.58, -3.29], [-52.58, -3.29]]))


class TestEstimateExpectedTrees(unittest.TestCase):
    def test_default_3m_grid_on_1ha(self):
        # 1 ha / 9 m^2 = 1,111 trees
        self.assertEqual(bp.estimate_expected_trees("restoration", 1.0, None), 1111)

    def test_spacing_override(self):
        # 2.0 x 2.0 m on 1 ha = 2,500
        self.assertEqual(
            bp.estimate_expected_trees("restoration", 1.0, None, 2.0), 2500
        )

    def test_falls_back_to_polygon_area(self):
        self.assertEqual(bp.estimate_expected_trees("restoration", None, 1.0), 1111)

    def test_gated_types_are_never_estimated(self):
        for t in ["mature", "enrichment", "research", "nursery", "infrastructure"]:
            self.assertIsNone(bp.estimate_expected_trees(t, 5.0, 5.0), t)

    def test_no_area_is_none(self):
        self.assertIsNone(bp.estimate_expected_trees("restoration", None, None))

    def test_blank_type_is_estimated(self):
        # blank = unclassified, but the spacing grid still applies if we have area
        self.assertEqual(bp.estimate_expected_trees("", 1.0, None), 1111)


class TestCountedTrees(unittest.TestCase):
    def test_counts_points_inside(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-52.58, -3.29]},
                    "properties": {},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-51.0, -3.29]},
                    "properties": {},
                },
                {"type": "Feature", "geometry": None, "properties": {}},
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "trees.geojson")
            with open(p, "w") as fh:
                json.dump(fc, fh)
            pts = bp.load_tree_points(p)
        self.assertEqual(len(pts), 2)
        n = sum(1 for (x, y) in pts if bp._point_in_ring(x, y, RING))
        self.assertEqual(n, 1)

    def test_unreadable_index_is_none_not_empty(self):
        self.assertIsNone(bp.load_tree_points("/nonexistent/trees.geojson"))


class TestPipeline(unittest.TestCase):
    """plot_props emits the Ask-2 fields only when present (no fabricated defaults)."""

    def test_props_include_derived_when_set(self):
        p = {
            "plot_id": "X",
            "farm_id": None,
            "name": "X",
            "hectares": 1.0,
            "status": "proposed",
            "plot_type": "restoration",
            "boundary_authority": "approx",
            "owner": None,
            "region": None,
            "verified_at": None,
            "media": None,
            "notes": None,
            "_polygon_ha": 0.99,
            "_spacing_m": None,
            "_expected_trees": 1111,
            "_counted_trees": 5,
        }
        props = bp.plot_props(p)
        self.assertEqual(props["expected_trees"], 1111)
        self.assertEqual(props["counted_trees"], 5)
        self.assertEqual(props["polygon_ha"], 0.99)
        self.assertNotIn("tree_spacing_m", props)  # None -> dropped

    def test_gated_plot_omits_expected_but_keeps_counted(self):
        p = {
            "plot_id": "Y",
            "farm_id": None,
            "name": "Y",
            "hectares": 3.0,
            "status": "planted",
            "plot_type": "mature",
            "boundary_authority": "walk-approx",
            "owner": None,
            "region": None,
            "verified_at": None,
            "media": None,
            "notes": None,
            "_polygon_ha": 2.9,
            "_spacing_m": None,
            "_expected_trees": None,
            "_counted_trees": 12,
        }
        props = bp.plot_props(p)
        self.assertNotIn("expected_trees", props)
        self.assertEqual(props["counted_trees"], 12)


if __name__ == "__main__":
    unittest.main()
