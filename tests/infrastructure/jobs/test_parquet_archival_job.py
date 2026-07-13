"""Pure unit tests for infrastructure/jobs/parquet_archival_job.py
(TASKS.md T-P1-11) — no database needed.

Live-database tests (the five acceptance criteria, which are all
statements about Postgres/Parquet-file state) live in
test_parquet_archival_job_integration.py, gated on Docker per this
repo's established convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from infrastructure.jobs.parquet_archival_job import (
    _candle_rows_to_table,
    _month_boundaries,
    _trade_tick_rows_to_table,
)


def test_month_boundaries_within_a_single_month_yields_one_window() -> None:
    start = datetime(2026, 3, 5, tzinfo=UTC)
    end = datetime(2026, 3, 20, tzinfo=UTC)
    boundaries = _month_boundaries(start, end)
    assert boundaries == [(start, end)]


def test_month_boundaries_spanning_three_months_yields_three_clipped_windows() -> None:
    start = datetime(2026, 1, 15, tzinfo=UTC)
    end = datetime(2026, 3, 10, tzinfo=UTC)
    boundaries = _month_boundaries(start, end)

    assert boundaries == [
        (datetime(2026, 1, 15, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)),
        (datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)),
        (datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 3, 10, tzinfo=UTC)),
    ]


def test_month_boundaries_spanning_a_year_boundary_rolls_over_correctly() -> None:
    start = datetime(2025, 12, 20, tzinfo=UTC)
    end = datetime(2026, 1, 10, tzinfo=UTC)
    boundaries = _month_boundaries(start, end)

    assert boundaries == [
        (datetime(2025, 12, 20, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 10, tzinfo=UTC)),
    ]


def test_month_boundaries_on_an_empty_range_yields_nothing() -> None:
    same = datetime(2026, 1, 1, tzinfo=UTC)
    assert _month_boundaries(same, same) == []


def test_month_boundaries_exactly_on_a_month_start_does_not_yield_an_empty_leading_window() -> (
    None
):
    start = datetime(2026, 2, 1, tzinfo=UTC)
    end = datetime(2026, 2, 15, tzinfo=UTC)
    assert _month_boundaries(start, end) == [(start, end)]


@dataclass(frozen=True)
class _FakeCandleRow:
    instrument_id: object
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int
    is_closed: bool
    source: str


@dataclass(frozen=True)
class _FakeTradeTickRow:
    instrument_id: object
    ts: datetime
    venue_trade_id: str
    price: Decimal
    qty: Decimal
    side: str


def test_candle_rows_to_table_preserves_decimal_precision_as_strings() -> None:
    instrument_id = uuid4()
    row = _FakeCandleRow(
        instrument_id=instrument_id,
        interval="1m",
        open_time=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal("50000.123456789012345678"),
        high=Decimal("50010.00"),
        low=Decimal("49990.00"),
        close=Decimal("50005.00"),
        volume=Decimal("1.5"),
        trade_count=10,
        is_closed=True,
        source="binance_rest_backfill",
    )
    table = _candle_rows_to_table([row])

    assert table.num_rows == 1
    assert table.column("instrument_id")[0].as_py() == str(instrument_id)
    assert table.column("open")[0].as_py() == "50000.123456789012345678"
    assert Decimal(table.column("open")[0].as_py()) == row.open
    assert table.column("is_closed")[0].as_py() is True
    assert table.column("trade_count")[0].as_py() == 10


def test_trade_tick_rows_to_table_preserves_decimal_precision_as_strings() -> None:
    instrument_id = uuid4()
    row = _FakeTradeTickRow(
        instrument_id=instrument_id,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        venue_trade_id="12345",
        price=Decimal("50000.00000001"),
        qty=Decimal("0.001"),
        side="buy",
    )
    table = _trade_tick_rows_to_table([row])

    assert table.num_rows == 1
    assert table.column("price")[0].as_py() == "50000.00000001"
    assert Decimal(table.column("price")[0].as_py()) == row.price
    assert table.column("side")[0].as_py() == "buy"
