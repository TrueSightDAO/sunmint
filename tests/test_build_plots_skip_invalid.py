"""Regression test: build_plots_geojson skips status=INVALID rows."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_plots_geojson as bp  # noqa: E402


class _FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def get_all_values(self):
        return self._rows


HEADER = [
    "Plot ID",
    "Farm ID",
    "Plot Name",
    "Hectares",
    "Status",
    "Boundary Authority",
    "Owner",
    "Region",
    "Verified At",
    "Media",
    "Notes",
    "Coordinates",
]


class TestLoadPlotsSkipsInvalid(unittest.TestCase):
    def test_invalid_row_is_skipped_valid_row_kept(self):
        rows = [
            HEADER,
            # valid plot — must be kept
            [
                "RM-P1",
                "rancho-maranta",
                "Rancho Maranta 1",
                "2.5",
                "planted",
                "gps_walk",
                "",
                "",
                "",
                "",
                "",
                "[[-3.1,-51.9],[-3.11,-51.9],[-3.1,-51.89]]",
            ],
            # invalid plot — must be skipped
            [
                "UAT-PLOT-1",
                "",
                "Uat Farm 20260901",
                "0",
                "INVALID",
                "approx",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            # lowercase invalid also skipped
            [
                "TDP1",
                "",
                "Test Dispatch",
                "0",
                "invalid",
                "approx",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
        ]
        plots = bp.load_plots(_FakeWS(rows))
        ids = [p["plot_id"] for p in plots]
        self.assertIn("RM-P1", ids)
        self.assertNotIn("UAT-PLOT-1", ids)
        self.assertNotIn("TDP1", ids)
        self.assertEqual(len(plots), 1)

    def test_empty_status_defaults_to_proposed(self):
        rows = [
            HEADER,
            [
                "SA-P1",
                "santa-anna-fazenda",
                "Santa Anna 1",
                "3",
                "",
                "approx",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
        ]
        plots = bp.load_plots(_FakeWS(rows))
        self.assertEqual(len(plots), 1)
        self.assertEqual(plots[0]["status"], "proposed")


if __name__ == "__main__":
    unittest.main()
