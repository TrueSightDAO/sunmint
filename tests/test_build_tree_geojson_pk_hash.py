"""Unit tests for the per-tree pk_hash emitted by build_tree_geojson.

The public trees/index.geojson must let a page show "my trees" by matching the
viewer's OWN key hash -- WITHOUT the feed ever carrying the raw public key. The
hash is a stable, non-reversible pseudonym:

    pk-<first 12 chars of base64url(SHA-256(base64-decoded SPKI bytes))>

It deliberately mirrors the writers' own hash (tokenomics GAS cfrSubDerivePkHash_,
dapp cfr-anapu/payout-registration-utils derivePkHash) so all three agree.

The expected constant below is a REAL key from the SunMint Tree Planting sheet
(col F "My Digital Signature") and was cross-checked against an independent
implementation:  printf %s "$KEY" | base64 -d | openssl dgst -sha256 -binary
                  | openssl base64 -A | tr '+/' '-_' | tr -d '=' | cut -c1-12
"""

import base64
import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_tree_geojson as bt

# A real SPKI public key copied from the SunMint Tree Planting sheet.
REAL_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA27CL2T7+75YfGbuYnwRubSfjYOxAQdBvzxsj"
    "J+W4a5iPF1r8nR4+Ekwth/dhe4tfr7ESwDxY40sYWC6CzHF271g9tvwu2x0JSfy0zwYj2M4TsmM93o"
    "Kv2JJr9hFoDq5ofnDcyLmtBWB8Q5PPhqCqTuSNsbUQtrJlreFXM+QQ2kzi5CeGk5IgeRB5IFnVDkKJ1"
    "xMJwLEIoRX9Z8Z1CA2mu2dHmEnyYfZHNLojvxz7VBq/tnCDYjJDCQfz5N61zzDftLp/B4intR4yKUgH"
    "p4csx0HNsl3z744SdkjHepHSLJdOBVL/7QYpIed5pRpqkT6Lxs8griF0e7nBxidVoVsQWwIDAQAB"
)
REAL_KEY_HASH = "pk-BJ_vNU_J6F4u"


class TestDerivePkHash(unittest.TestCase):
    def test_matches_independent_implementation(self):
        self.assertEqual(bt.derive_pk_hash(REAL_KEY), REAL_KEY_HASH)

    def test_is_deterministic_and_url_safe(self):
        h = bt.derive_pk_hash(REAL_KEY)
        self.assertEqual(h, bt.derive_pk_hash(REAL_KEY))
        self.assertTrue(h.startswith("pk-"))
        self.assertEqual(len(h), 15)  # 'pk-' + 12
        self.assertIsNone(__import__("re").search(r"[+/=]", h))  # no base64 padding

    def test_absent_and_malformed_yield_none(self):
        for bad in (None, "", "   ", "!!!not-base64!!!", "MIIBIjANBgkqhkiG9w0B"):
            self.assertIsNone(bt.derive_pk_hash(bad), repr(bad))

    def test_never_carries_the_raw_key(self):
        # The published value must not contain any long run of the raw key.
        self.assertNotIn(REAL_KEY[10:60], bt.derive_pk_hash(REAL_KEY))

    def test_hash_is_sha256_of_decoded_bytes(self):
        raw = base64.b64decode(REAL_KEY)
        expect = (
            "pk-"
            + base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
            .decode("ascii")
            .rstrip("=")[:12]
        )
        self.assertEqual(bt.derive_pk_hash(REAL_KEY), expect)


class TestSignatureExtraction(unittest.TestCase):
    def test_reads_key_from_contribution_cell(self):
        text = (
            "[TREE PLANTING EVENT]\n- Latitude: -3.52\n- Longitude: -51.57\n--------\n\n"
            "My Digital Signature: " + REAL_KEY + "\n\nRequest Transaction ID: abc=="
        )
        self.assertEqual(bt.signature_from_contribution(text), REAL_KEY)
        self.assertEqual(
            bt.derive_pk_hash(bt.signature_from_contribution(text)), REAL_KEY_HASH
        )

    def test_dedicated_cell_wins_over_contribution_text(self):
        # A modern writer may stash the key in its own column; that takes priority.
        self.assertEqual(
            bt.signature_from_contribution("no key here", REAL_KEY), REAL_KEY
        )

    def test_missing_signature_is_empty(self):
        self.assertEqual(
            bt.signature_from_contribution("[TREE PLANTING EVENT]\n- Latitude: 1"), ""
        )


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


def _row(tree_id, contribution, status="NEW"):
    r = [""] * len(HEADER)
    r[0] = tree_id
    r[3] = tree_id
    r[5] = contribution
    r[8] = "https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/images/x.jpg"
    r[10] = "-3.094461"
    r[11] = "-52.095119"
    r[12] = status
    r[13] = "Cacau - Hybrid"
    r[16] = "2026-09-02T18:47:03-03:00"
    r[20] = "https://cfr.truesight.me/"
    return r


SIG_ROW = (
    "[TREE PLANTING EVENT]\n- Latitude: -3.094461\n- Longitude: -52.095119\n--------\n\n"
    "My Digital Signature: " + REAL_KEY + "\n\nRequest Transaction ID: x=="
)


class TestLoadTreesEmitsPkHash(unittest.TestCase):
    def test_row_with_signature_gets_its_pk_hash(self):
        trees = bt.load_trees(
            FakeWS([HEADER, _row("Edgar_20260903083523_003", SIG_ROW)])
        )
        self.assertEqual(len(trees), 1)
        self.assertEqual(trees[0]["pk_hash"], REAL_KEY_HASH)

    def test_row_without_signature_has_no_pk_hash(self):
        trees = bt.load_trees(
            FakeWS(
                [
                    HEADER,
                    _row(
                        "Edgar_20260903083411_001",
                        "[TREE PLANTING EVENT]\n- Latitude: 1",
                    ),
                ]
            )
        )
        self.assertIsNone(trees[0]["pk_hash"])

    def test_feature_properties_carry_only_the_hash(self):
        # End-to-end shape: features expose pk_hash, never the raw key.
        trees = bt.load_trees(
            FakeWS([HEADER, _row("Edgar_20260903083523_003", SIG_ROW)])
        )
        self.assertEqual(trees[0]["pk_hash"], REAL_KEY_HASH)
        self.assertNotIn("public_key", trees[0])
        self.assertNotIn(REAL_KEY, str(trees[0]))


if __name__ == "__main__":
    unittest.main()
