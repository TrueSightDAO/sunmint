"""Unit tests for the request_txid emitted by build_tree_geojson.

Governor directive (thread 35944, 2026-09-28): the public trees index -- which
feeds My Trees / Monitor Tree -- must carry the submission's request transaction
id so a viewer can SEE and FILTER trees by it. The value is sheet col V
("request_transaction_id"), the 344-char signed request signature.

The key is emitted only when present (empty cells must NOT add a null key, so the
published index stays compact and older rows are unaffected).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_tree_geojson as bt  # noqa: E402

HEADER = [
    "Telegram Update ID", "Telegram Chatroom ID", "Telegram Chatroom Name",
    "Telegram Message ID", "Contributor Name", "Contribution Made", "Status date",
    "Telegram File IDs", "Photo of Tree Planted", "Submitted Name", "Latitude",
    "Longitude", "Status", "Specie", "GitHub Commit URL", "Cost of Tree",
    "Tree Planting Time", "Linked QR Code", "Linked At", "Plot ID",
    "Submission Source", "request_transaction_id", "my digital signature",
]

TXID = "HJzxgxgVOP1iCMAZCJeno/fMooVx54Ywt7VIU6alFpIQfkYT1wFVSVzwkWN3FVIBJt5DgMSTfn0"


class FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def get_all_values(self):
        return self._rows


def _row(tree_id="Edgar_20260903083532_007", txid=TXID):
    r = [""] * len(HEADER)
    r[3] = tree_id
    r[10] = "-3.5229189"
    r[11] = "-51.5749705"
    r[12] = "NEW"
    r[13] = "Cacao - Forestero"
    r[16] = "2026-09-24T13:28:47.742Z"
    r[21] = txid
    return r


class RequestTxid(unittest.TestCase):
    def test_load_trees_carries_txid(self):
        t = bt.load_trees(FakeWS([HEADER, _row()]))[0]
        self.assertEqual(t["request_txid"], TXID)

    def test_absent_txid_is_none(self):
        self.assertIsNone(bt.load_trees(FakeWS([HEADER, _row(txid="")]))[0]["request_txid"])

    def test_column_lookup_by_name_not_position(self):
        # Header located by NAME, so a reordered sheet still resolves correctly.
        swapped = HEADER[:]
        swapped[13], swapped[21] = swapped[21], swapped[13]
        r = _row()
        r[13], r[21] = r[21], r[13]
        self.assertEqual(bt.load_trees(FakeWS([swapped, r]))[0]["request_txid"], TXID)

    def test_legacy_header_without_column_is_none(self):
        # Rows written before col V existed must not break the build.
        legacy = HEADER[:21]
        self.assertIsNone(bt.load_trees(FakeWS([legacy, _row()[:21]]))[0]["request_txid"])

    def test_main_emits_txid_in_props(self):
        ws = FakeWS([HEADER, _row()])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "index.geojson")
            orig_sheet, orig_reg = bt.get_sheet, bt.load_registry
            bt.get_sheet = lambda: ws
            bt.load_registry = lambda *a, **k: {}
            try:
                sys.argv = ["build_tree_geojson.py", "--out", out]
                bt.main()
            finally:
                bt.get_sheet, bt.load_registry = orig_sheet, orig_reg
            props = json.load(open(out))["features"][0]["properties"]
        self.assertEqual(props["request_txid"], TXID)

    def test_main_omits_txid_when_absent(self):
        ws = FakeWS([HEADER, _row(txid="")])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "index.geojson")
            orig_sheet, orig_reg = bt.get_sheet, bt.load_registry
            bt.get_sheet = lambda: ws
            bt.load_registry = lambda *a, **k: {}
            try:
                sys.argv = ["build_tree_geojson.py", "--out", out]
                bt.main()
            finally:
                bt.get_sheet, bt.load_registry = orig_sheet, orig_reg
            props = json.load(open(out))["features"][0]["properties"]
        self.assertNotIn("request_txid", props)


if __name__ == "__main__":
    unittest.main()
