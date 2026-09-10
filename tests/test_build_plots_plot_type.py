"""Regression tests: the `plot_type` tag survives the sheet -> geojson pipeline.

Covers the three ways a tag record can go missing (see SUNMINT_PLOTS_REGISTRY.md):
 1. the column is read and emitted when the header is present;
 2. a missing/renamed header is warned about, not silently dropped;
 3. an unrecognized token is warned about but still emitted (loud, not silent);
 4. a blank cell stays ABSENT from the geojson (never a fabricated default).
"""

import contextlib
import io
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
    "Plot Type",
    "Boundary Authority",
    "Owner",
    "Region",
    "Verified At",
    "Media",
    "Notes",
    "Coordinates",
    "Latitude",
    "Longitude",
]

# Plot Type is column F (index 5); empty string for the unclassified case.
ROW = [
    "U-06-07",
    "raimundo-geniza-para",
    "Sitio Raimundo & Geniza Plot 1 (restoration)",
    "0.32",
    "proposed",
    "restoration",
    "approx",
    "Raimundo & Geniza",
    "Uruara, Para",
    "",
    "",
    "",
    "[[-53.6519,-3.6306],[-53.6521,-3.6297],[-53.6519,-3.6306]]",
    "",
    "",
]


def _row_with(plot_type, header=None):
    r = list(ROW)
    r[5] = plot_type
    return [header or HEADER, r]


class TestPlotTypeSurvives(unittest.TestCase):
    def test_read_and_emitted(self):
        plots = bp.load_plots(_FakeWS(_row_with("restoration")))
        self.assertEqual(len(plots), 1)
        self.assertEqual(plots[0]["plot_type"], "restoration")
        self.assertEqual(bp.plot_props(plots[0])["plot_type"], "restoration")

    def test_blank_plot_type_is_absent_not_defaulted(self):
        plots = bp.load_plots(_FakeWS(_row_with("")))
        self.assertIsNone(plots[0]["plot_type"])
        # Absent from the emitted properties -> no fabricated default.
        self.assertNotIn("plot_type", bp.plot_props(plots[0]))

    def test_unknown_token_warns_but_still_emits(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plots = bp.load_plots(_FakeWS(_row_with("weird-value")))
        self.assertEqual(plots[0]["plot_type"], "weird-value")
        self.assertIn("weird-value", buf.getvalue())
        self.assertIn("unrecognized plot_type", buf.getvalue())

    def test_missing_header_warns_and_does_not_crash(self):
        # Header without any 'Plot Type' column -> generator must warn, not drop silently.
        header = [h for h in HEADER if h != "Plot Type"]
        row = [c for i, c in enumerate(ROW) if i != 5]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plots = bp.load_plots(_FakeWS([header, row]))
        self.assertEqual(plots[0]["plot_type"], None)
        self.assertIn("plot_type", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
