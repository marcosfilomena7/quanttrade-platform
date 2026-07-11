"""Candle domain value — one OHLCV bar.

T-P0-07 does not itself task "implement a Candle domain model" — but its
own description defines `MarketDataView.bars(...) -> Sequence[Candle]`,
which cannot type-check without a `Candle` type to return. This is the
minimal type that makes that signature meaningful, not a full mirror of
DATABASE.md §4's Candle table: `trade_count`, `source`, and `inserted_at`
are ingestion/persistence metadata with no bearing on what a strategy or
backtest needs to *read*, so they are left for whichever later task
actually constructs and persists rows (T-P1-04/T-P1-08).

`is_closed` is kept, deliberately: ARCHITECTURE.md §7.2 calls a
partially-formed candle "the #1 lookahead vector in live trading" and
DATABASE.md §4 says it "must be explicit, not inferred" — exactly the
concern `MarketDataView` exists to guard against. Structural OHLC
invariant validation (high ≥ max(open, close), etc.) is explicitly T-P1-05
("Data Validation Suite")'s job, not this one; this type only rejects
float, matching every other Decimal-bearing value object in `domain/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV bar. `open_time` is the start of the interval (DATABASE.md)."""

    instrument_id: UUID
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool

    def __post_init__(self) -> None:
        for field_name in ("open", "high", "low", "close", "volume"):
            value = getattr(self, field_name)
            if isinstance(value, float):  # float-guard
                raise TypeError(f"Candle.{field_name} must be Decimal, not float")
            if not isinstance(value, Decimal):
                raise TypeError(f"Candle.{field_name} must be Decimal")
