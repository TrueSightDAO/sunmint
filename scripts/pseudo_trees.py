"""Pseudo-tree slot identity + allocation for SunMint plots.

A "pseudo tree" is a placeholder slot for a tree the density estimator
expects on a plot but which has **not** been photographed or located. Per the
governor rulings of 2026-10-14 (see
``plans/SUNMINT_PSEUDO_TREE_QR_PLAN.md``):

  * A pseudo slot is a ``SunMint Tree Planting``-shaped row with **blank
    Latitude/Longitude** and **no photo**, ``plot_id`` set, and a stable id
    ``"PS-<plot_id>-<N>"``.
  * **Blank K/L *is* the discriminator** — a real, GPS-tagged submission always
    carries coordinates, so no extra "record_type" column is needed.
  * A pseudo row is **promoted in place**: when a real, located submission
    arrives via the **QR-bound** path it writes K/L (+ photo) onto the **same
    row** -- the row *is* the tree. There is **no matching step at all**: a real
    tree arriving with **no** QR bound to a slot simply **stands alone** and
    claims no slot (D13=(a)). So there is **no FIFO / claim-on-arrival matching**
    and **no spatial nearest-match**.

This module holds **only** the id scheme and the allocator — no sheet, network,
or ledger coupling — so it can be unit-tested and reused by the index builder,
the DApp link handler, and the reconciliation/index jobs. It encodes **settled**
decisions only (SD1/SD2/SD6); it deliberately does not gate on ``plot_type``
(row-minting policy D2/D9) or touch QR status (D5).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

# ``PS-`` is the allocation NAMESPACE / provenance marker: a row whose id
# starts with it entered the ledger as a spacing-estimate placeholder. Per D1
# the id is **not load-bearing** -- the live pseudo-vs-real test is blank K/L
# (``is_pseudo_row``), so a plain tree id would be equally valid.
PSEUDO_PREFIX = "PS-"


def _coerce_n(n: object) -> int:
    """Coerce a slot number to a positive int, or raise ValueError."""
    try:
        value = int(str(n).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"slot number must be an integer, got {n!r}") from exc
    if value < 1:
        raise ValueError(f"slot number must be >= 1, got {value}")
    return value


def make_pseudo_id(plot_id: str, n: object) -> str:
    """Build a pseudo-slot id ``"PS-<plot_id>-<N>"``.

    Raises ValueError if ``plot_id`` is empty or ``n`` is not a positive int.
    """
    if not plot_id or not str(plot_id).strip():
        raise ValueError("plot_id is required to build a pseudo id")
    return f"{PSEUDO_PREFIX}{str(plot_id).strip()}-{_coerce_n(n)}"


def is_pseudo_id(tree_id: object) -> bool:
    """True if ``tree_id`` is in the pseudo namespace (starts with ``PS-``).

    Namespace-level check only: per D1 the id is **not load-bearing**, so the
    live pseudo-vs-real discriminator is blank lat/lng (:func:`is_pseudo_row`).
    """
    if not tree_id:
        return False
    return str(tree_id).startswith(PSEUDO_PREFIX)


def pseudo_slot(tree_id: object) -> int | None:
    """Return the integer slot ``N`` from ``PS-<plot_id>-<N>``, else None."""
    if not is_pseudo_id(tree_id):
        return None
    tail = str(tree_id).rsplit("-", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None


def pseudo_plot_id(tree_id: object) -> str | None:
    """Return the plot id embedded in ``PS-<plot_id>-<N>``, else None.

    The plot id may itself contain dashes (``RM-P2``, ``V-06-29``); only the
    final ``-<N>`` segment is the slot, so it is stripped from the right.
    """
    if not is_pseudo_id(tree_id):
        return None
    body = str(tree_id)[len(PSEUDO_PREFIX) :]
    head, sep, tail = body.rpartition("-")
    if not sep:
        return None
    try:
        int(tail)
    except ValueError:
        return None
    return head or None


def _is_blank(v: object) -> bool:
    return v is None or not str(v).strip()


def is_pseudo_row(lat: object, lng: object) -> bool:
    """True if a sheet row is a pseudo slot: **both** latitude and longitude blank.

    A real submission is GPS-tagged, so blank K/L is the discriminator. A row
    with exactly one coordinate present is malformed and treated as NOT pseudo
    (it will not be silently swallowed as a placeholder).
    """
    return _is_blank(lat) and _is_blank(lng)


class Slot(NamedTuple):
    """An allocated pseudo slot."""

    tree_id: str
    plot_id: str
    n: int


def used_slots(plot_id: str, existing_ids: Iterable[object]) -> set[int]:
    """Set of slot numbers already taken on ``plot_id`` (ignores other plots)."""
    taken: set[int] = set()
    for tid in existing_ids:
        if pseudo_plot_id(tid) != str(plot_id).strip():
            continue
        n = pseudo_slot(tid)
        if n is not None:
            taken.add(n)
    return taken


def next_free_slot(plot_id: str, existing_ids: Iterable[object]) -> int:
    """Smallest unused slot number (>=1) on ``plot_id`` (the next-free slot).

    Slots are interchangeable placeholders, so the next-free rule keeps ids
    stable; it is **not** a claim/matching target (see the module docstring).
    """
    taken = used_slots(plot_id, existing_ids)
    n = 1
    while n in taken:
        n += 1
    return n


def allocate_slot(
    plot_id: str,
    existing_ids: Iterable[object],
    *,
    expected_trees: int | None = None,
    explicit_n: object | None = None,
) -> Slot:
    """Allocate a pseudo slot on ``plot_id``.

    * ``explicit_n`` — bind to a specific slot; refused if already used.
    * otherwise the **next free** slot is chosen.
    * ``expected_trees`` — when given, the ceiling (from the plot's estimate);
      requesting a slot beyond it is refused (the caller rejects past cap).
      ``None`` means unbounded.

    Raises ValueError on: bad ids, a reuse of an explicit slot, or exceeding the
    ceiling. Deterministic: same inputs -> same slot.
    """
    existing = list(existing_ids)
    if explicit_n is not None:
        n = _coerce_n(explicit_n)
        if n in used_slots(plot_id, existing):
            raise ValueError(
                f"pseudo slot {make_pseudo_id(plot_id, n)} is already used"
            )
    else:
        n = next_free_slot(plot_id, existing)
    if expected_trees is not None:
        ceiling = int(expected_trees)
        if ceiling < 0:
            raise ValueError(f"expected_trees must be >= 0, got {ceiling}")
        if n > ceiling:
            raise ValueError(
                f"slot {n} exceeds expected_trees={ceiling} for plot {plot_id} "
                "(estimate may need raising first)"
            )
    return Slot(tree_id=make_pseudo_id(plot_id, n), plot_id=str(plot_id).strip(), n=n)
