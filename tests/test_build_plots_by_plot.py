"""Unit tests for build_plots_geojson.emit_per_plot (derived per-plot layer)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_plots_geojson as bp  # noqa: E402


def _feature(pid, farm="f1"):
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        },
        "properties": {
            "plot_id": pid,
            "farm_id": farm,
            "name": pid,
            "hectares": 1.0,
            "status": "approx",
        },
    }


class TestEmitPerPlot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_writes_one_file_per_plot(self):
        feats = [_feature("RM-P1"), _feature("RM-P2"), _feature("SA-P1")]
        bp.emit_per_plot(feats, self.tmp, "2026-09-01T00:00:00Z")
        files = sorted(os.listdir(self.tmp))
        self.assertEqual(files, ["RM-P1.geojson", "RM-P2.geojson", "SA-P1.geojson"])

    def test_each_file_is_single_feature_collection(self):
        bp.emit_per_plot([_feature("RM-P1")], self.tmp, "T")
        with open(os.path.join(self.tmp, "RM-P1.geojson")) as f:
            doc = json.load(f)
        self.assertEqual(doc["type"], "FeatureCollection")
        self.assertEqual(len(doc["features"]), 1)
        self.assertEqual(doc["features"][0]["properties"]["plot_id"], "RM-P1")
        self.assertEqual(doc["generated_at"], "T")

    def test_prunes_stale_files(self):
        stale = os.path.join(self.tmp, "OLD-PLOT.geojson")
        with open(stale, "w") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
        bp.emit_per_plot([_feature("RM-P1")], self.tmp, "T")
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "RM-P1.geojson")))

    def test_sanitizes_plot_id_in_filename(self):
        bp.emit_per_plot([_feature("PAULO LA DO SITIO/P1")], self.tmp, "T")
        self.assertTrue(
            os.path.exists(os.path.join(self.tmp, "PAULO_LA_DO_SITIO_P1.geojson"))
        )

    def test_skips_features_without_plot_id(self):
        bp.emit_per_plot([{"type": "Feature", "properties": {}}], self.tmp, "T")
        self.assertEqual(os.listdir(self.tmp), [])


if __name__ == "__main__":
    unittest.main()
