"""Pure unit tests for infrastructure/backtest/dataset_version_repository.py
(TASKS.md T-P1-12) — no database needed.

Live-database tests (repository `.get()` round-trips and the
`backtest_run` FK relationship) live in
test_dataset_version_repository_integration.py, gated on Docker per this
repo's established convention.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from infrastructure.backtest.dataset_version_repository import (
    compute_dataset_content_hash,
    hash_row,
)


def _sample_rows() -> list[dict[str, object]]:
    return [
        {"open_time": "2026-01-01T00:00:00", "open": Decimal("100.00"), "close": Decimal("100.50")},
        {"open_time": "2026-01-01T00:01:00", "open": Decimal("100.50"), "close": Decimal("101.00")},
        {"open_time": "2026-01-01T00:02:00", "open": Decimal("101.00"), "close": Decimal("100.75")},
    ]


def test_two_identical_dataset_exports_produce_the_same_content_hash() -> None:
    """TASKS.md T-P1-12 acceptance criterion, verbatim: "Two identical
    dataset exports produce the same content hash.\""""
    symbol_set = [uuid4(), uuid4()]
    rows = _sample_rows()
    sample_hashes = [hash_row(r) for r in rows]

    first = compute_dataset_content_hash(
        symbol_set=symbol_set,
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=sample_hashes,
    )
    second = compute_dataset_content_hash(
        symbol_set=list(symbol_set),  # a fresh, distinct list object with equal content
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=[hash_row(r) for r in _sample_rows()],  # freshly recomputed
    )

    assert first == second


def test_modifying_one_row_in_the_dataset_produces_a_different_hash() -> None:
    """TASKS.md T-P1-12 acceptance criterion, verbatim: "Modifying one row
    in the dataset produces a different hash.\""""
    symbol_set = [uuid4()]
    rows = _sample_rows()
    original_hash = compute_dataset_content_hash(
        symbol_set=symbol_set,
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=[hash_row(r) for r in rows],
    )

    modified_rows = _sample_rows()
    modified_rows[1]["close"] = Decimal("999.99")  # change exactly one row
    modified_hash = compute_dataset_content_hash(
        symbol_set=symbol_set,
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(modified_rows),
        sample_hashes=[hash_row(r) for r in modified_rows],
    )

    assert original_hash != modified_hash


def test_modifying_a_row_outside_a_naive_subsample_still_changes_the_hash() -> None:
    """Guards against a "statistical subsample" misreading of
    "sample_hashes": every row contributes its own hash, so a change to
    the *last* row (which a naive first-N sample would miss) still
    changes the overall content hash."""
    rows = _sample_rows()
    original_hash = compute_dataset_content_hash(
        symbol_set=[uuid4()],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=[hash_row(r) for r in rows],
    )

    modified_rows = _sample_rows()
    modified_rows[-1]["open"] = Decimal("1.00")
    modified_hash = compute_dataset_content_hash(
        symbol_set=[uuid4()],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(modified_rows),
        sample_hashes=[hash_row(r) for r in modified_rows],
    )

    assert original_hash != modified_hash


def test_reordering_the_same_symbols_does_not_change_the_hash() -> None:
    """`symbol_set` is a set, not a meaningful sequence — collection
    order must not affect the hash."""
    a, b = uuid4(), uuid4()
    rows = _sample_rows()
    sample_hashes = [hash_row(r) for r in rows]

    forward = compute_dataset_content_hash(
        symbol_set=[a, b],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=sample_hashes,
    )
    reversed_ = compute_dataset_content_hash(
        symbol_set=[b, a],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=sample_hashes,
    )

    assert forward == reversed_


def test_reordering_the_rows_themselves_does_change_the_hash() -> None:
    """Unlike `symbol_set`, `sample_hashes` preserves the dataset's own
    row order — a genuine reordering of the same rows is a different
    dataset shape and must produce a different hash."""
    rows = _sample_rows()
    forward_hashes = [hash_row(r) for r in rows]
    reversed_hashes = list(reversed(forward_hashes))

    forward = compute_dataset_content_hash(
        symbol_set=[uuid4()],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=forward_hashes,
    )
    reversed_result = compute_dataset_content_hash(
        symbol_set=[uuid4()],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=reversed_hashes,
    )

    assert forward != reversed_result


def test_different_row_counts_produce_different_hashes() -> None:
    symbol_set = [uuid4()]
    rows = _sample_rows()
    sample_hashes = [hash_row(r) for r in rows]

    full = compute_dataset_content_hash(
        symbol_set=symbol_set,
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=sample_hashes,
    )
    wrong_count = compute_dataset_content_hash(
        symbol_set=symbol_set,
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows) + 1,
        sample_hashes=sample_hashes,
    )

    assert full != wrong_count


def test_hash_row_handles_decimal_and_uuid_values_deterministically() -> None:
    instrument_id = uuid4()
    row = {"instrument_id": instrument_id, "price": Decimal("50000.00000001")}

    assert hash_row(row) == hash_row(dict(row))  # same content, fresh dict object
    assert hash_row(row) != hash_row({**row, "price": Decimal("50000.00000002")})
