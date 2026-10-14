"""Tests for scripts/mint_pseudo_trees.py (PR3).

Covers the pure mint planning (id order, D9 plot-type gate, D2 capacity cap,
idempotency) and the row shape (blank K/L, plot_id + id set, no photo/QR).
No network: the sheet is faked by plain lists / a tiny stand-in worksheet.
"""

import os
import sys
from typing import ClassVar

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import mint_pseudo_trees as m
import pseudo_trees


# --- plan_mint ------------------------------------------------------------ #
class TestPlanMint:
    def test_first_mint_starts_at_one(self):
        assert m.plan_mint("RM-P2", 3, [], 10, "restoration") == [
            "PS-RM-P2-1",
            "PS-RM-P2-2",
            "PS-RM-P2-3",
        ]

    def test_continues_after_existing_slots(self):
        existing = ["PS-RM-P2-1", "PS-RM-P2-2"]
        assert m.plan_mint("RM-P2", 2, existing, 10, "restoration") == [
            "PS-RM-P2-3",
            "PS-RM-P2-4",
        ]

    def test_idempotent_no_duplicate_ids(self):
        first = m.plan_mint("V-06-29", 2, [], 10, "nursery")
        second = m.plan_mint("V-06-29", 1, first, 10, "nursery")
        assert set(first).isdisjoint(second)
        assert second == ["PS-V-06-29-3"]

    def test_dash_plot_id_round_trips(self):
        ids = m.plan_mint("V-06-29", 1, [], 5, "enrichment")
        assert ids == ["PS-V-06-29-1"]
        assert pseudo_trees.pseudo_plot_id(ids[0]) == "V-06-29"
        assert pseudo_trees.pseudo_slot(ids[0]) == 1

    def test_ignores_ids_from_other_plots(self):
        existing = ["PS-OTHER-1", "PS-OTHER-2", "REAL_1"]
        assert m.plan_mint("RM-P2", 1, existing, 10, "restoration") == ["PS-RM-P2-1"]

    @pytest.mark.parametrize(
        "ptype", ["mature", "research", "infrastructure", "", None]
    )
    def test_d9_gate_refuses_non_mintable_types(self, ptype):
        with pytest.raises(ValueError, match="may not mint"):
            m.plan_mint("RM-P2", 1, [], 100, ptype)

    @pytest.mark.parametrize("ptype", ["restoration", "enrichment", "nursery"])
    def test_d9_gate_allows_mintable_types(self, ptype):
        assert m.plan_mint("RM-P2", 1, [], 100, ptype) == ["PS-RM-P2-1"]

    def test_d2_cap_refused_when_over_expected(self):
        existing = ["PS-RM-P2-1", "PS-RM-P2-2"]
        with pytest.raises(ValueError, match="exceed Expected Trees"):
            m.plan_mint("RM-P2", 2, existing, 3, "restoration")

    def test_d2_cap_exact_boundary_ok(self):
        existing = ["PS-RM-P2-1", "PS-RM-P2-2"]
        assert m.plan_mint("RM-P2", 1, existing, 3, "restoration") == ["PS-RM-P2-3"]

    def test_d2_fail_closed_when_expected_missing(self):
        with pytest.raises(ValueError, match="no 'Expected Trees' set"):
            m.plan_mint("RM-P2", 1, [], None, "restoration")

    def test_non_positive_count_refused(self):
        with pytest.raises(ValueError, match="count must be"):
            m.plan_mint("RM-P2", 0, [], 10, "restoration")

    def test_empty_plot_id_refused(self):
        with pytest.raises(ValueError, match="plot_id is required"):
            m.plan_mint("  ", 1, [], 10, "restoration")


# --- build_row ------------------------------------------------------------ #
class TestBuildRow:
    HEADER: ClassVar[list[str]] = [
        "Telegram Update ID",
        "Telegram Message ID",
        "Status",
        "Plot ID",
        "Latitude",
        "Longitude",
        "Photo of Tree Planted",
        "Linked QR Code",
        "Submission Source",
    ]

    def _row(self):
        idx = m.header_index(self.HEADER, m.TREE_COLS)
        return m.build_row(len(self.HEADER), idx, "PS-RM-P2-1", "RM-P2")

    def test_id_goes_to_col_a(self):
        row = self._row()
        assert row[0] == "PS-RM-P2-1"

    def test_plot_id_set(self):
        row = self._row()
        assert row[self.HEADER.index("Plot ID")] == "RM-P2"

    def test_lat_lng_blank(self):
        row = self._row()
        assert row[self.HEADER.index("Latitude")] == ""
        assert row[self.HEADER.index("Longitude")] == ""
        assert pseudo_trees.is_pseudo_row(
            row[self.HEADER.index("Latitude")], row[self.HEADER.index("Longitude")]
        )

    def test_photo_and_qr_blank(self):
        row = self._row()
        assert row[self.HEADER.index("Photo of Tree Planted")] == ""
        assert row[self.HEADER.index("Linked QR Code")] == ""
        assert row[self.HEADER.index("Telegram Message ID")] == ""

    def test_status_is_available_not_linked(self):
        row = self._row()
        assert row[self.HEADER.index("Status")] == m.STATUS_AVAILABLE
        assert row[self.HEADER.index("Status")] != "LINKED"

    def test_source_is_mint_marker(self):
        row = self._row()
        assert row[self.HEADER.index("Submission Source")] == m.MINT_SOURCE

    def test_row_width_matches_header(self):
        assert len(self._row()) == len(self.HEADER)


# --- header_index --------------------------------------------------------- #
class TestHeaderIndex:
    def test_exact_then_prefix(self):
        idx = m.header_index(["Plot ID", "Latitude", "Expected Trees"], m.TREE_COLS)
        assert idx["plot_id"] == 0
        assert idx["lat"] == 1

    def test_missing_columns_are_none(self):
        idx = m.header_index(["Plot ID"], m.TREE_COLS)
        assert idx["qr"] is None
        assert idx["photo"] is None


# --- build_row with a narrow header --------------------------------------- #
class TestBuildRowNarrow:
    def test_absent_columns_silently_skipped(self):
        header = ["Telegram Update ID", "Plot ID"]
        idx = m.header_index(header, m.TREE_COLS)
        row = m.build_row(len(header), idx, "PS-RM-P2-1", "RM-P2")
        assert row == ["PS-RM-P2-1", "RM-P2"]
