"""Tests for the pseudo-tree id scheme + allocator (SUNMINT_PSEUDO_TREE_QR_PLAN)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pseudo_trees as pt


class TestIdScheme(unittest.TestCase):
    def test_make_and_parse_roundtrip(self):
        tid = pt.make_pseudo_id("RM-P2", 7)
        self.assertEqual(tid, "PS-RM-P2-7")
        self.assertTrue(pt.is_pseudo_id(tid))
        self.assertEqual(pt.pseudo_slot(tid), 7)
        self.assertEqual(pt.pseudo_plot_id(tid), "RM-P2")

    def test_dash_containing_plot_ids_roundtrip(self):
        # Plot ids may contain dashes; only the final -<N> is the slot, so it
        # must be stripped from the RIGHT.
        for pid in ("RM-P2", "V-06-29", "SA-P1"):
            tid = pt.make_pseudo_id(pid, 3)
            self.assertEqual(tid, f"PS-{pid}-3")
            self.assertEqual(pt.pseudo_plot_id(tid), pid)
            self.assertEqual(pt.pseudo_slot(tid), 3)

    def test_real_ids_are_not_pseudo(self):
        for rid in ("Edgar_123", "42", "AGL8-001", "RM-P2-pseudo-1"):
            self.assertFalse(pt.is_pseudo_id(rid))
            self.assertIsNone(pt.pseudo_slot(rid))
            self.assertIsNone(pt.pseudo_plot_id(rid))

    def test_make_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            pt.make_pseudo_id("", 1)
        with self.assertRaises(ValueError):
            pt.make_pseudo_id("RM-P2", 0)
        with self.assertRaises(ValueError):
            pt.make_pseudo_id("RM-P2", "x")

    def test_slot_number_is_coerced_and_validated(self):
        self.assertEqual(pt.pseudo_slot("PS-RM-P2-12"), 12)
        self.assertIsNone(pt.pseudo_slot("PS-RM-P2-xx"))


class TestBlankDiscriminator(unittest.TestCase):
    def test_both_blank_is_pseudo(self):
        for lat, lng in ((None, None), ("", ""), ("  ", None)):
            self.assertTrue(pt.is_pseudo_row(lat, lng))

    def test_any_coordinate_present_is_not_pseudo(self):
        self.assertFalse(pt.is_pseudo_row("-3.29", "-52.58"))
        self.assertFalse(pt.is_pseudo_row("-3.29", None))  # malformed -> not pseudo
        self.assertFalse(pt.is_pseudo_row(None, "-52.58"))


class TestAllocator(unittest.TestCase):
    def test_first_allocation_is_slot_1(self):
        s = pt.allocate_slot("RM-P2", [])
        self.assertEqual(s.tree_id, "PS-RM-P2-1")
        self.assertEqual(s.n, 1)

    def test_next_free_skips_taken_slots(self):
        existing = ["PS-RM-P2-1", "PS-RM-P2-3"]
        self.assertEqual(pt.next_free_slot("RM-P2", existing), 2)
        self.assertEqual(pt.allocate_slot("RM-P2", existing).n, 2)

    def test_other_plots_do_not_interfere(self):
        existing = ["PS-RM-P2-1", "PS-RM-P2-2", "PS-V-06-29-1"]
        s = pt.allocate_slot("RM-P2", existing)
        self.assertEqual(s.n, 3)  # V-06-29 slot ignored

    def test_explicit_slot_honoured(self):
        s = pt.allocate_slot("RM-P2", ["PS-RM-P2-1"], explicit_n=14)
        self.assertEqual(s.tree_id, "PS-RM-P2-14")

    def test_explicit_slot_reuse_refused(self):
        with self.assertRaises(ValueError):
            pt.allocate_slot("RM-P2", ["PS-RM-P2-5"], explicit_n=5)

    def test_ceiling_enforced(self):
        # expected_trees=2, both taken -> next free (3) exceeds ceiling.
        with self.assertRaises(ValueError):
            pt.allocate_slot("RM-P2", ["PS-RM-P2-1", "PS-RM-P2-2"], expected_trees=2)

    def test_ceiling_allows_up_to_limit(self):
        s = pt.allocate_slot("RM-P2", ["PS-RM-P2-1"], expected_trees=2)
        self.assertEqual(s.n, 2)

    def test_unbounded_when_no_ceiling(self):
        # No ceiling => the next free slot, even past a stale/low estimate.
        s = pt.allocate_slot("RM-P2", ["PS-RM-P2-1", "PS-RM-P2-2"])
        self.assertEqual(s.n, 3)

    def test_deterministic(self):
        a = pt.allocate_slot("RM-P2", ["PS-RM-P2-1"])
        b = pt.allocate_slot("RM-P2", ["PS-RM-P2-1"])
        self.assertEqual(a, b)

    def test_real_ids_in_existing_are_ignored(self):
        s = pt.allocate_slot("RM-P2", ["Edgar_9", "42"])
        self.assertEqual(s.n, 1)


if __name__ == "__main__":
    unittest.main()
