"""Unit tests for build_tree_geojson program attribution (col U / col F).

Locks in the governor directive (thread 35944, 2026-09-26): the trees index must
carry a `program` attribute resolved from the submission origin, EMPTY when that
origin is not a registered program host. The origin is read from the dedicated
col U ("Submission Source"), falling back to parsing col F for rows written
before col U existed.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_tree_geojson as bt  # noqa: E402

REGISTRY = {"cfr.truesight.me": "crf-anapu", "beta.cfr.truesight.me": "crf-anapu"}

HEADER = [
    "Telegram Update ID", "Telegram Chatroom ID", "Telegram Chatroom Name",
    "Telegram Message ID", "Contributor Name", "Contribution Made", "Status date",
    "Telegram File IDs", "Photo of Tree Planted", "Submitted Name", "Latitude",
    "Longitude", "Status", "Specie", "GitHub Commit URL", "Cost of Tree",
    "Tree Planting Time", "Linked QR Code", "Linked At", "Plot ID",
    "Submission Source",
]


class FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def get_all_values(self):
        return self._rows


def _row(tree_id="Edgar_20260903083532_007", f_text="", u_text=""):
    r = [""] * len(HEADER)
    r[3] = tree_id
    r[5] = f_text
    r[8] = "https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/images/tree.jpg"
    r[10] = "-3.5229189"
    r[11] = "-51.5749705"
    r[12] = "NEW"
    r[13] = "Cacao - Forestero"
    r[16] = "2026-09-24T13:28:47.742Z"
    r[20] = u_text
    return r


class SourceParse(unittest.TestCase):
    def test_source_line(self):
        self.assertEqual(
            bt.submission_source("[TREE PLANTING EVENT]\n- Submission Source: https://cfr.truesight.me/"),
            "https://cfr.truesight.me/",
        )

    def test_escaped_newlines(self):
        cell = "[TREE PLANTING EVENT]\\r\\n- Submission Source: https://localhost/\\r\\n----"
        self.assertEqual(bt.submission_source(cell), "https://localhost/")

    def test_generated_using_footer(self):
        self.assertEqual(
            bt.submission_source("This submission was generated using https://sunmint.truesight.me/"),
            "https://sunmint.truesight.me/",
        )

    def test_absent(self):
        self.assertEqual(bt.submission_source("[TREE PLANTING EVENT]\n- Latitude: 1"), "")
        self.assertEqual(bt.submission_source(""), "")

    def test_source_host(self):
        self.assertEqual(bt.source_host("https://cfr.truesight.me/"), "cfr.truesight.me")
        self.assertEqual(bt.source_host("https://beta.cfr.truesight.me/x?y=1"), "beta.cfr.truesight.me")
        self.assertEqual(bt.source_host("autopilot-sophia"), "autopilot-sophia")
        self.assertEqual(bt.source_host(""), "")

    def test_load_registry_lowercases(self):
        reg = bt.load_registry(fetch=lambda url: {"hosts": {"CFR.Truesight.ME": "crf-anapu"}})
        self.assertEqual(reg, {"cfr.truesight.me": "crf-anapu"})

    def test_load_registry_failure_is_empty(self):
        def boom(url):
            raise OSError("network down")

        self.assertEqual(bt.load_registry(fetch=boom), {})


class ProgramAttribution(unittest.TestCase):
    def _tree(self, f_text="", u_text=""):
        ws = FakeWS([HEADER, _row(f_text=f_text, u_text=u_text)])
        return bt.load_trees(ws, REGISTRY)[0]

    def test_program_from_col_u(self):
        t = self._tree(u_text="https://cfr.truesight.me/")
        self.assertEqual(t["program"], "crf-anapu")
        self.assertEqual(t["submission_source"], "https://cfr.truesight.me/")

    def test_program_from_col_f_fallback(self):
        t = self._tree(f_text="[TREE PLANTING EVENT]\n- Submission Source: https://beta.cfr.truesight.me/")
        self.assertEqual(t["program"], "crf-anapu")

    def test_col_u_preferred_over_f(self):
        t = self._tree(f_text="... Submission Source: https://localhost/",
                       u_text="https://cfr.truesight.me/")
        self.assertEqual(t["program"], "crf-anapu")
        self.assertEqual(t["submission_source"], "https://cfr.truesight.me/")

    def test_unregistered_host_is_empty_string(self):
        self.assertEqual(self._tree(u_text="https://localhost/")["program"], "")

    def test_sentinel_is_empty_string(self):
        self.assertEqual(self._tree(u_text="autopilot-sophia")["program"], "")

    def test_no_source(self):
        t = self._tree()
        self.assertEqual(t["program"], "")
        self.assertIsNone(t["submission_source"])

    def test_end_to_end_main(self):
        ws = FakeWS([HEADER, _row(u_text="https://cfr.truesight.me/")])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "index.geojson")
            orig_sheet, orig_reg = bt.get_sheet, bt.load_registry
            bt.get_sheet = lambda: ws
            bt.load_registry = lambda *a, **k: REGISTRY
            try:
                sys.argv = ["build_tree_geojson.py", "--out", out]
                bt.main()
            finally:
                bt.get_sheet, bt.load_registry = orig_sheet, orig_reg
            doc = json.load(open(out))
        props = doc["features"][0]["properties"]
        self.assertEqual(props["program"], "crf-anapu")
        self.assertEqual(props["submission_source"], "https://cfr.truesight.me/")


class EmptyProgramStaysPresent(unittest.TestCase):
    def test_empty_program_key_is_kept(self):
        # '' must survive the `if v is not None` filter -- consumers rely on the key.
        ws = FakeWS([HEADER, _row(u_text="https://localhost/")])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "index.geojson")
            orig_sheet, orig_reg = bt.get_sheet, bt.load_registry
            bt.get_sheet = lambda: ws
            bt.load_registry = lambda *a, **k: REGISTRY
            try:
                sys.argv = ["build_tree_geojson.py", "--out", out]
                bt.main()
            finally:
                bt.get_sheet, bt.load_registry = orig_sheet, orig_reg
            doc = json.load(open(out))
        self.assertIn("program", doc["features"][0]["properties"])
        self.assertEqual(doc["features"][0]["properties"]["program"], "")


if __name__ == "__main__":
    unittest.main()
