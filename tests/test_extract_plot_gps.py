"""Unit tests for extract_plot_gps.py (pure functions + fixture-media run).

Tests the GPS parsing, DMS->decimal conversion, convex hull, and ring building
against exiftool-tagged fixture images (created with exiftool at test time).
Requires exiftool on PATH; skips if missing.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import extract_plot_gps as ep  # noqa: E402

HAVE_EXIFTOOL = (
    subprocess.run(["which", "exiftool"], capture_output=True, text=True).returncode
    == 0
)


class TestDmsToDecimal(unittest.TestCase):
    def test_south_west(self):
        self.assertAlmostEqual(
            ep.dms_to_decimal("3 deg 17' 45.96\" S"), -3.29610, places=4
        )
        self.assertAlmostEqual(
            ep.dms_to_decimal("52 deg 34' 59.39\" W"), -52.58316, places=4
        )

    def test_north_east(self):
        self.assertAlmostEqual(ep.dms_to_decimal("1 deg 0' 0\" N"), 1.0, places=6)
        self.assertAlmostEqual(ep.dms_to_decimal("10 deg 0' 0\" E"), 10.0, places=6)

    def test_plain_decimal(self):
        self.assertAlmostEqual(ep.dms_to_decimal("-3.29610"), -3.29610, places=5)

    def test_bad(self):
        self.assertIsNone(ep.dms_to_decimal(""))
        self.assertIsNone(ep.dms_to_decimal(None))
        self.assertIsNone(ep.dms_to_decimal("abc"))


class TestConvexHull(unittest.TestCase):
    def test_rectangle(self):
        pts = [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)]
        hull = ep.convex_hull(pts)
        # All 4 corners on hull; interior point dropped
        self.assertEqual(len(hull), 4)
        self.assertIn((0, 0), hull)
        self.assertIn((1, 1), hull)
        self.assertNotIn((0.5, 0.5), hull)

    def test_collinear(self):
        pts = [(0, 0), (1, 0), (2, 0)]
        hull = ep.convex_hull(pts)
        self.assertEqual(len(hull), 2)

    def test_single(self):
        self.assertEqual(ep.convex_hull([(1, 2)]), [(1, 2)])


class TestBuildRing(unittest.TestCase):
    def test_closed_ring_lnglat_order(self):
        pts = [(lat0, lng0), (lat1, lng1), (lat2, lng2), (lat3, lng3)] = [
            (3.0, 52.0),
            (3.1, 52.0),
            (3.1, 52.1),
            (3.0, 52.1),
        ]
        ring = ep.build_ring(pts)
        self.assertEqual(ring[0], ring[-1])  # closed
        # [lng, lat] order
        self.assertEqual(ring[0], [52.0, 3.0])

    def test_single_point(self):
        # A single point produces a degenerate 1-element ring; main() refuses
        # <3 distinct points with a clear error (see test_guard).
        ring = ep.build_ring([(3.0, 52.0)])
        self.assertEqual(ring, [[52.0, 3.0]])


class TestExtractGpsPoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path("/tmp/extract/fixtures")
        cls.fixture_dir.mkdir(exist_ok=True)
        # Minimal 1x1 JPEG (base64 of a tiny valid JPEG)
        import base64

        tiny_jpg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
        )
        pts = [
            ("3 17 45.96 S", "52 34 59.39 W"),  # -3.29610, -52.58316
            ("3 17 45.60 S", "52 34 59.16 W"),  # -3.29600, -52.58310
            ("3 17 45.78 S", "52 34 58.80 W"),  # -3.29605, -52.58300
            ("3 17 46.14 S", "52 34 59.52 W"),  # -3.29615, -52.58320
        ]
        cls.paths = []
        for i, (lat, lng) in enumerate(pts):
            p = cls.fixture_dir / f"corner_{i}.jpg"
            p.write_bytes(tiny_jpg)
            subprocess.run(
                [
                    "exiftool",
                    f"-GPSLatitude={lat}",
                    f"-GPSLatitudeRef={lat.split()[-1]}",
                    f"-GPSLongitude={lng}",
                    f"-GPSLongitudeRef={lng.split()[-1]}",
                    str(p),
                ],
                capture_output=True,
                text=True,
            )
            # remove exiftool backup
            (cls.fixture_dir / f"corner_{i}.jpg_original").unlink(missing_ok=True)
            cls.paths.append(str(p))

    @unittest.skipUnless(HAVE_EXIFTOOL, "exiftool not installed")
    def test_reads_gps_from_fixtures(self):
        points = ep.extract_gps_points(self.paths)
        self.assertEqual(len(points), 4)
        lats = [p[0] for p in points]
        lngs = [p[1] for p in points]
        self.assertTrue(all(lat < 0 for lat in lats))  # all S
        self.assertTrue(all(lng < 0 for lng in lngs))  # all W
        self.assertAlmostEqual(min(lats), -3.29615, places=5)
        self.assertAlmostEqual(max(lats), -3.29600, places=5)

    @unittest.skipUnless(HAVE_EXIFTOOL, "exiftool not installed")
    def test_hull_from_fixture_points(self):
        points = ep.extract_gps_points(self.paths)
        ring = ep.build_ring(points)
        self.assertEqual(ring[0], ring[-1])
        self.assertGreaterEqual(len(ring) - 1, 3)  # at least a triangle


if __name__ == "__main__":
    unittest.main()
