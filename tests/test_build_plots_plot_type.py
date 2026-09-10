"""Regression tests: the `plot_type` tag survives the sheet -> geojson pipeline.

Covers the ways a tag record can go missing (see SUNMINT_PLOTS_REGISTRY.md):
 1. the column is read and emitted when the header is present;
 2. a missing/renamed header is warned about, not silently dropped;
 3. an unrecognized token is warned about but still emitted (loud, not silent);
 4. a blank cell stays ABSENT from the geojson (never a fabricated default);
 5. every token in the single-column vocabulary (incl. `maturing`) is accepted.

Design note: `Plot Type` is a SINGLE column with 7 values, including `maturing`
(the walk-observed middle stage). There is deliberately **no** separate stage
column -- the sheet is authoritative and it uses one column (thread 24326).
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
    "Boundary Authority",
    "Plot Type",
    "Owner",
    "Region",
    "Verified At",
    "Media",
    "Notes",
    "Coordinates",
    "Latitude",
    "Longitude",
]

# Plot Type is index 6; empty string for the blank case.
ROW = [
    "U-06-07",
    "raimundo-geniza-para",
    "Sitio Raimundo & Geniza Plot 1 (restoration)",
    "0.32",
    "proposed",
    "approx",
    "restoration",
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
    r[6] = plot_type
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
        header = [h for h in HEADER if h != "Plot Type"]
        row = [c for i, c in enumerate(ROW) if i != 6]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plots = bp.load_plots(_FakeWS([header, row]))
        self.assertEqual(plots[0]["plot_type"], None)
        self.assertIn("plot_type", buf.getvalue())


class TestSingleColumnVocabulary(unittest.TestCase):
    """`maturing` is one of the 7 values of the SINGLE `Plot Type` column."""

    def test_maturing_is_a_valid_plot_type(self):
        self.assertIn("maturing", bp.VALID_PLOT_TYPES)

    def test_no_separate_stage_axis(self):
        # Guard against re-introducing the split stage column this repo dropped.
        self.assertFalse(hasattr(bp, "VALID_PLOT_STAGES"))
        self.assertNotIn("plot_stage", bp.FIELD_COLUMNS)

    def test_each_valid_token_is_accepted_without_warning(self):
        for token in sorted(bp.VALID_PLOT_TYPES):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                plots = bp.load_plots(_FakeWS(_row_with(token)))
            self.assertEqual(plots[0]["plot_type"], token)
            self.assertNotIn("unrecognized plot_type", buf.getvalue())

    def test_maturing_row_emits_maturing(self):
        props = bp.plot_props(bp.load_plots(_FakeWS(_row_with("maturing")))[0])
        self.assertEqual(props["plot_type"], "maturing")
        self.assertNotIn("plot_stage", props)


if __name__ == "__main__":
    unittest.main()
