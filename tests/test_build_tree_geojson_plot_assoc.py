"""Tests for build-time tree -> plot association (Ask-1).

A tree's plot link is graded geometrically against the plot registry:
  inside  (point-in-polygon)      -> auto-associate
  near    (<= PLOT_NEAR_METRES)   -> flag, never auto-link
  none                            -> unassociated
The sheet's explicit Plot ID is an OVERRIDE that always wins.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_tree_geojson as bt

# ~0.01 deg square around (-52.58, -3.29) (~1.1 km sides).
RING = [
    [-52.585, -3.295],
    [-52.575, -3.295],
    [-52.575, -3.285],
    [-52.585, -3.285],
    [-52.585, -3.295],
]


class TestPointInRing(unittest.TestCase):
    def test_center_is_inside(self):
        self.assertTrue(bt._point_in_ring(-52.58, -3.29, RING))

    def test_far_point_is_outside(self):
        self.assertFalse(bt._point_in_ring(-53.5, -3.29, RING))


class TestDistance(unittest.TestCase):
    def test_inside_point_has_zero_distance(self):
        # point-to-edge of a point inside the ring is > 0; 'inside' is decided first.
        self.assertGreater(bt._point_to_ring_m(-52.58, -3.29, RING), 0)

    def test_near_point_distance_is_small(self):
        # ~30 m east of the east edge (lng -52.575).
        d = bt._point_to_ring_m(-52.5747, -3.29, RING)
        self.assertLess(d, 60)


class TestAssociate(unittest.TestCase):
    rings = (("PLOTX", RING),)

    def test_inside(self):
        r = bt.associate_plot(-52.58, -3.29, self.rings)
        self.assertEqual(r["plot_assoc"], "inside")
        self.assertEqual(r["plot_id"], "PLOTX")

    def test_near_is_flagged_not_linked(self):
        r = bt.associate_plot(-52.5747, -3.29, self.rings)  # ~30 m outside
        self.assertEqual(r["plot_assoc"], "near")
        self.assertIsNone(r["plot_id"])  # NOT auto-linked
        self.assertEqual(r["nearest_plot_id"], "PLOTX")
        self.assertIsNotNone(r["plot_dist_m"])

    def test_none(self):
        r = bt.associate_plot(-51.0, -3.29, self.rings)
        self.assertEqual(r["plot_assoc"], "none")
        self.assertIsNone(r["plot_id"])

    def test_explicit_override_wins(self):
        r = bt.associate_plot(-51.0, -3.29, self.rings, explicit="MANUAL")
        self.assertEqual(r["plot_id"], "MANUAL")
        self.assertEqual(r["plot_assoc"], "none")

    def test_no_rings_yields_no_assoc_fields_from_caller(self):
        # associate_plot itself over an empty registry -> 'none'
        r = bt.associate_plot(-52.58, -3.29, [])
        self.assertEqual(r["plot_assoc"], "none")
        self.assertIsNone(r["plot_id"])


class TestLoadPlotRings(unittest.TestCase):
    def test_reads_polygons_only(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [RING]},
                    "properties": {"plot_id": "A"},
                },
                {"type": "Feature", "geometry": None, "properties": {"plot_id": "B"}},
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-52.5, -3.2]},
                    "properties": {"plot_id": "C"},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plots.geojson")
            with open(p, "w") as fh:
                json.dump(fc, fh)
            rings = bt.load_plot_rings(p)
        self.assertEqual([pid for pid, _ in rings], ["A"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(bt.load_plot_rings("/nonexistent/plots.geojson"), [])


if __name__ == "__main__":
    unittest.main()
