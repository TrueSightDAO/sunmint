#!/usr/bin/env python3
"""Mint pseudo-tree rows into the ``SunMint Tree Planting`` tab (PR3).

Part of ``plans/SUNMINT_PSEUDO_TREE_QR_PLAN.md``.

A *pseudo tree* is a placeholder slot for a tree the density estimator expects
on a plot but which has **not** been photographed or located.  It is a
``SunMint Tree Planting``-shaped row with **blank Latitude/Longitude**, **no
photo**, ``plot_id`` set, and a stable id ``PS-<plot_id>-<seq>`` -- see
:mod:`pseudo_trees` for the id scheme and allocator.

This CLI is the **mint primitive** for those rows.  It writes them into the
single ``SunMint Tree Planting`` registry (D4) using the
``sunmint-ledger-manager`` service account -- the same principal that owns the
``SunMint Plots`` tab.

Safety rails (all enforced here):

* **dry-run by default** -- nothing is written unless ``--commit`` is passed.
* **never over-mint** (D2) -- bounded by the plot's ``Expected Trees``.  A
  plot with **no** ``Expected Trees`` set is refused (fail-closed), as is a
  ``--count`` beyond the remaining capacity.
* **plot-type gate (D9)** -- only ``restoration`` / ``maturing`` /
  ``enrichment`` / ``nursery`` may mint; ``mature`` / ``research`` /
  ``infrastructure`` / blank are refused.
* **idempotent** -- slots already present for the plot are detected and the
  next free slot is used, so re-running never duplicates.

The id is written to the **``Telegram Update ID``** column (A): the trees index
resolves a row's id via ``pick_tree_id`` (col D when it is an ``Edgar_*`` id,
else col A), so col A is the home for a non-Telegram id.  Latitude/Longitude,
photo, and linked-QR are left blank -- blank K/L *is* the pseudo discriminator
(``pseudo_trees.is_pseudo_row``), and the index builder skips those rows so a
placeholder never becomes a fabricated map point.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pseudo_trees

SHEET_ID = "1qbZZhf-_7xzmDTriaJVWj6OZshyQsFkdsAV8-pyzASQ"
TREES_TAB = "SunMint Tree Planting"
PLOTS_TAB = "SunMint Plots"

# D9: which plot types may mint a pseudo row
# (never mature/research/infrastructure/blank). 'maturing' = an
# established-but-still-growing stand -- mintable per Gary (2026-10).
GATED_PLOT_TYPES = {"restoration", "maturing", "enrichment", "nursery"}

# Provenance marker written to the Submission Source column of a minted row.
MINT_SOURCE = "pseudo-tree-mint"

# Status of a freshly minted (unbound) slot; != LINKED so the link handler's
# find-unlinked query (plot_id==X AND Status!=LINKED) sees it as free (SD20).
STATUS_AVAILABLE = "available"

SA_NAME = "sunmint_ledger_manager"
DEFAULT_CREDS_DIR = "/opt/truesight_autopilot/config/google"
SHEET_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

# Header fragments -> canonical field name (exact match tried before prefix).
TREE_COLS = {
    "id_a": ["telegram update id", "tree id"],
    "id_d": ["telegram message id"],
    "status": ["status"],
    "plot_id": ["plot id", "plot", "parcel", "site name", "site"],
    "lat": ["latitude"],
    "lng": ["longitude"],
    "photo": ["photo of tree planted", "photo of tree", "photo"],
    "qr": ["linked qr code", "linked qr", "qr code"],
    "source": ["submission source"],
}
PLOT_COLS = {
    "plot_id": ["plot id", "plot"],
    "plot_type": ["plot type", "type"],
    "expected_trees": ["expected trees", "expected tree count", "expected"],
}


# --- pure logic ----------------------------------------------------------- #
def _find_idx(header, names):
    hl = [(h or "").strip().lower() for h in header]
    for n in names:
        n = n.strip().lower()
        if not n:
            continue
        for i, h in enumerate(hl):
            if h == n:
                return i
    for n in names:
        n = n.strip().lower()
        for i, h in enumerate(hl):
            if h.startswith(n):
                return i
    return None


def header_index(header, spec):
    """Map canonical field -> column index (None when absent)."""
    return {field: _find_idx(header, names) for field, names in spec.items()}


def plan_mint(plot_id, count, existing_ids, expected_trees, plot_type):
    """Return the ordered list of pseudo ids to mint for ``plot_id``.

    Raises ``ValueError`` on: a gated (non-mintable) plot type (D9), a
    non-positive count, a **missing** ``expected_trees`` (D2 -- fail-closed:
    a plot with no Expected Trees set may not mint), or a request that would
    exceed it (never over-mint).  Deterministic: same inputs -> same ids.
    """
    pid = str(plot_id).strip()
    if not pid:
        raise ValueError("plot_id is required")
    ptype = (plot_type or "").strip().lower()
    if ptype not in GATED_PLOT_TYPES:
        raise ValueError(
            f"refused: plot type {plot_type!r} may not mint pseudo trees "
            f"(D9 allows only {sorted(GATED_PLOT_TYPES)})"
        )
    if int(count) < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    if expected_trees is None:
        raise ValueError(
            "refused: plot has no 'Expected Trees' set -- refusing to mint "
            "unbounded (D2 fail-closed); set Expected Trees on the plot first"
        )
    used = pseudo_trees.used_slots(pid, existing_ids)
    if len(used) + int(count) > expected_trees:
        raise ValueError(
            f"refused: minting {count} would exceed Expected Trees "
            f"({expected_trees}) -- {len(used)} slot(s) already exist"
        )
    ids = []
    cur = list(existing_ids)
    for _ in range(int(count)):
        slot = pseudo_trees.allocate_slot(pid, cur, expected_trees=expected_trees)
        ids.append(slot.tree_id)
        cur.append(slot.tree_id)
    return ids


def build_row(width, idx, tree_id, plot_id):
    """Build a full-width pseudo row: id / plot_id / status set, K/L blank.

    Latitude, Longitude, photo, and linked-QR are left empty on purpose --
    blank K/L is the pseudo discriminator (SD1) and the index skips it.
    """
    row = [""] * max(0, int(width))

    def put(field, value):
        i = idx.get(field)
        if i is not None and 0 <= i < len(row):
            row[i] = value

    put("id_a", tree_id)
    put("status", STATUS_AVAILABLE)
    put("plot_id", plot_id)
    put("source", MINT_SOURCE)
    return row


# --- sheet I/O ------------------------------------------------------------ #
def get_creds():
    from google.oauth2 import service_account

    creds_dir = os.environ.get("GOOGLE_CREDS_DIR", DEFAULT_CREDS_DIR)
    path = Path(creds_dir) / f"{SA_NAME}_gdrive_key.json"
    if path.is_file():
        return service_account.Credentials.from_service_account_file(
            str(path), scopes=[SHEET_SCOPE]
        )
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=[SHEET_SCOPE]
        )
    sys.exit(f"no credentials: {path} missing and GOOGLE_SERVICE_ACCOUNT_JSON unset")


def get_workbook(creds):
    import gspread

    return gspread.authorize(creds).open_by_key(SHEET_ID)


def read_plot(plots_ws, plot_id):
    """Return (plot_type, expected_trees) for ``plot_id``; raises if not found."""
    rows = plots_ws.get_all_values()
    if not rows:
        raise ValueError(f"'{PLOTS_TAB}' tab is empty")
    cols = header_index(rows[0], PLOT_COLS)
    ci = cols["plot_id"]
    if ci is None:
        raise ValueError("no 'Plot ID' column in the plots tab")
    for row in rows[1:]:
        val = (row[ci] if ci < len(row) else "").strip()
        if val != str(plot_id).strip():
            continue
        ptype = cols["plot_type"]
        exp = cols["expected_trees"]
        ptype_v = (row[ptype] if ptype is not None and ptype < len(row) else "").strip()
        exp_raw = (row[exp] if exp is not None and exp < len(row) else "").strip()
        expected = None
        if exp_raw:
            try:
                expected = round(float(exp_raw.replace(",", "")))
            except ValueError:
                expected = None
        return ptype_v, expected
    raise ValueError(f"plot {plot_id!r} not found in '{PLOTS_TAB}'")


def read_existing_ids(tree_ws, idx):
    """All non-empty tree ids from cols A and D (for next-free allocation)."""
    values = tree_ws.get_all_values()
    out = []
    for row in values[1:]:
        for field in ("id_a", "id_d"):
            i = idx.get(field)
            if i is not None and i < len(row) and row[i].strip():
                out.append(row[i].strip())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plot-id", required=True, help="e.g. RM-P2")
    ap.add_argument(
        "--count", type=int, default=1, help="how many slots to mint (default 1)"
    )
    ap.add_argument(
        "--commit",
        action="store_true",
        help="actually write the sheet (default: dry-run)",
    )
    args = ap.parse_args(argv)

    wb = get_workbook(get_creds())
    trees_ws = wb.worksheet(TREES_TAB)
    plots_ws = wb.worksheet(PLOTS_TAB)

    header = trees_ws.row_values(1)
    idx = header_index(header, TREE_COLS)
    if idx.get("id_a") is None:
        sys.exit("could not find the tree id column (A / Tree ID)")

    try:
        plot_type, expected = read_plot(plots_ws, args.plot_id)
    except ValueError as exc:
        sys.exit(f"REFUSED: {exc}")
    existing = read_existing_ids(trees_ws, idx)

    print(f"plot_id={args.plot_id!r} plot_type={plot_type!r} expected_trees={expected}")
    print(
        f"existing pseudo slots on plot: {sorted(pseudo_trees.used_slots(args.plot_id, existing))}"
    )

    try:
        ids = plan_mint(args.plot_id, args.count, existing, expected, plot_type)
    except ValueError as exc:
        sys.exit(f"REFUSED: {exc}")
    print(f"would mint {len(ids)} slot(s): {ids}")

    if not args.commit:
        print("\n=== DRY RUN (no sheet write) -- pass --commit to write ===")
        for tid in ids:
            print(f"  row: {build_row(len(header), idx, tid, args.plot_id)}")
        return

    for tid in ids:
        trees_ws.append_row(
            build_row(len(header), idx, tid, args.plot_id),
            value_input_option="RAW",
        )
        print(f"  minted {tid}")
    print(f"wrote {len(ids)} pseudo row(s) to '{TREES_TAB}'")


if __name__ == "__main__":
    main()
