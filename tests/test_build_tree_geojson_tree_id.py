"""Unit tests for build_tree_geojson.pick_tree_id (col A vs col D).

Regression for the tree_id off-by-one (OPEN_FOLLOWUPS.md, thread 35189): the
public trees/index.geojson keyed tree_id on col A "Telegram Update ID", but the
canonical id is col D "Telegram Message ID" -- they differ by +1 on every
Edgar-direct submission row. Values below are the REAL rows read from the
SunMint Tree Planting tab on 2026-09-24.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_tree_geojson as bt  # noqa: E402

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
]


class FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def get_all_values(self):
        return self._rows


def _row(col_a, col_d, status="NEW", lat="-3.094461", lng="-52.095119", qr=""):
    r = [""] * len(HEADER)
    r[0] = col_a
    r[3] = col_d
    r[8] = (
        "https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/images/20260902_bomsucesso_tree02.jpg"
    )
    r[10] = lat
    r[11] = lng
    r[12] = status
    r[13] = "Cacau - Hybrid"
    r[16] = "2026-09-02T18:47:03-03:00"
    r[17] = qr
    return r


class TestPickTreeId(unittest.TestCase):
    def test_prefers_edgar_message_id_over_update_id(self):
        # real row 38 (tree02): A=..._004 but canonical (col D) is ..._003
        self.assertEqual(
            bt.pick_tree_id("Edgar_20260903083523_004", "Edgar_20260903083523_003"),
            "Edgar_20260903083523_003",
        )

    def test_legacy_numeric_rows_keep_col_a(self):
        self.assertEqual(bt.pick_tree_id("469027268", "171"), "469027268")

    def test_edgar_shaped_survives_when_in_col_a_only(self):
        self.assertEqual(
            bt.pick_tree_id("Edgar_20260903083523_003", ""),
            "Edgar_20260903083523_003",
        )

    def test_empty_yields_none(self):
        self.assertIsNone(bt.pick_tree_id("", ""))


class TestLoadTreesUsesCanonicalId(unittest.TestCase):
    def test_bomsucesso_family_keys_on_col_d(self):
        # rows 37/38/39 -- the whole family is col D, not col A (+1)
        rows = [
            HEADER,
            _row("Edgar_20260903083411_002", "Edgar_20260903083411_001"),
            _row(
                "Edgar_20260903083523_004",
                "Edgar_20260903083523_003",
                status="LINKED",
                qr="2024OSCAR_CB_20260620_1",
            ),
            _row("Edgar_20260903083528_006", "Edgar_20260903083528_005"),
        ]
        trees = bt.load_trees(FakeWS(rows))
        got = sorted(t["id"] for t in trees)
        self.assertEqual(
            got,
            [
                "Edgar_20260903083411_001",
                "Edgar_20260903083523_003",
                "Edgar_20260903083528_005",
            ],
        )
        # and the canonical id now matches its linked QR / cert ledger_ref
        t02 = [t for t in trees if t["id"].endswith("_003")][0]
        self.assertEqual(t02["qr_code"], "2024OSCAR_CB_20260620_1")


if __name__ == "__main__":
    unittest.main()
