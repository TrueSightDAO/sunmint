"""The tree index holds POINTS: blank-K/L rows (pseudo slots / un-located) are
EXCLUDED, not emitted with null geometry.

Pseudo slots are SunMint spacing-estimate placeholders with blank K/L
(SUNMINT_PSEUDO_TREE_QR_PLAN). Promotion writes K/L onto the SAME row, so a
promoted slot reappears on the next rebuild -- no re-link. Regression: the
builder previously emitted blank-coordinate rows as geometry:null features.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_tree_geojson as bt

HEADER = [
    "Telegram Update ID",
    "Telegram Chatroom ID",
    "Telegram Chatroom Name",
    "Telegram Message ID",
    "Contributor Name",
    "Contribution Made",
    "Status date",
    "Telegram File IDs",
    "Photo of Tree Planted",
    "Submitted Name",
    "Latitude",
    "Longitude",
    "Status",
    "Specie",
    "GitHub Commit URL",
    "Cost of Tree",
    "Tree Planting Time",
    "Linked QR Code",
    "Linked At",
    "Plot ID",
    "Submission Source",
]


class FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def get_all_values(self):
        return self._rows


def _row(tree_id, lat, lng, plot_id="RM-P2", status="NEW"):
    r = [""] * len(HEADER)
    r[3] = tree_id
    r[8] = "https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/images/t.jpg"
    r[10] = lat
    r[11] = lng
    r[12] = status
    r[13] = "Cacao - Forestero"
    r[16] = "2026-09-24T13:28:47.742Z"
    r[19] = plot_id
    return r


class SkipBlankCoordinates(unittest.TestCase):
    def test_pseudo_row_is_excluded(self):
        ws = FakeWS([HEADER, _row("PS-RM-P2-1", "", "", status="NEW")])
        self.assertEqual(bt.load_trees(ws), [])

    def test_pseudo_row_with_whitespace_coords_is_excluded(self):
        ws = FakeWS([HEADER, _row("PS-RM-P2-1", "  ", None)])
        self.assertEqual(bt.load_trees(ws), [])

    def test_real_row_is_kept(self):
        ws = FakeWS(
            [HEADER, _row("Edgar_20260903083532_007", "-3.5229189", "-51.5749705")]
        )
        trees = bt.load_trees(ws)
        self.assertEqual(len(trees), 1)
        self.assertEqual(trees[0]["id"], "Edgar_20260903083532_007")
        self.assertEqual(trees[0]["plot_id"], "RM-P2")

    def test_promoted_row_reappears_real(self):
        # Same id before (blank) and after (K/L written in place) promotion.
        ws = FakeWS([HEADER, _row("PS-RM-P2-9", "-3.52", "-51.57", status="LINKED")])
        trees = bt.load_trees(ws)
        self.assertEqual([t["id"] for t in trees], ["PS-RM-P2-9"])

    def test_mixed_batch_keeps_only_located(self):
        ws = FakeWS(
            [
                HEADER,
                _row("Edgar_20260903083411_001", "-3.29", "-52.58"),
                _row("PS-RM-P2-1", "", ""),
                _row("Edgar_20260903083528_005", "-3.30", "-52.59"),
            ]
        )
        got = sorted(t["id"] for t in bt.load_trees(ws))
        self.assertEqual(got, ["Edgar_20260903083411_001", "Edgar_20260903083528_005"])


if __name__ == "__main__":
    unittest.main()
