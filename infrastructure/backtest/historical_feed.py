"""HistoricalFeed — MarketDataFeed port implementation for backtesting,
with strictly monotonic, multi-timeframe merge ordering (TASKS.md T-P2-03).

"Implement `HistoricalFeed` as the `MarketDataFeed` port for
backtesting. Loads bars from a `DatasetVersion` (Parquet or Postgres).
Emits events in strictly monotonic timestamp order across all symbols
and timeframes. Multiple timeframes are merged into a single event
stream: a 1h bar closes only when all contained 1m bars have been
processed. Uses a min-heap for merge ordering."

Design decisions, and why:

- **Loads from Postgres only, not Parquet.** The task's own dependency
  list is "T-P2-01, T-P1-12, T-P0-07" — T-P1-11 (the Parquet archival
  pipeline, and by extension "how to read a Parquet file back") is
  conspicuously absent, unlike every other task in this codebase whose
  description names a capability it actually depends on. None of
  T-P2-03's four acceptance criteria mention Parquet either — AC3 only
  requires that "loading a `DatasetVersion`... produces events
  deterministically," which Postgres alone already satisfies. "Parquet
  or Postgres" in the task's own prose is read here the same way this
  session has already resolved several earlier either/or or
  forward-looking mentions (e.g. T-P1-08's Redis cache, T-P2-01's "lint
  rule from T-P0-01"): a true statement about the *system* as a whole,
  not a literal requirement of *this* task's own scope.
- **`HistoricalFeed` implements both halves of `MarketDataFeed`
  (`subscribe`/`unsubscribe`) *and* an additional pull method,
  `next_event()`.** T-P0-07's own port is push/callback-shaped
  (`subscribe(symbol, timeframe, handler)`), but T-P2-03's own
  acceptance criteria describe pull semantics no callback-only design
  can express: "exhausting the feed raises `FeedExhausted` rather than
  silently stopping" only makes sense for something a caller actively
  *asks* for the next item from — a push-only feed has no caller-visible
  moment of "exhaustion" to raise from. `next_event()` pops the next
  candle from the internal min-heap merge (in strict order), invokes
  the handler subscribed for that `(symbol, timeframe)` if any (so the
  port's own push contract is still genuinely honored), and returns the
  candle; once nothing is left, it raises `FeedExhausted` instead of
  returning `None` or silently no-op'ing.
- **No new "CandleClosed" event type.** T-P2-03's acceptance criteria
  use the phrase "a 1h `CandleClosed` event," but `MarketDataFeed`'s own
  handler signature (T-P0-07) is `Callable[[Candle], Awaitable[None]]`
  — a bare `Candle`, not an event wrapper. T-P1-08's own `CandleClosed`
  (a *different*, Binance-specific dataclass carrying `exchange_ts`/
  `local_recv_ts`) belongs to the live WS pipeline; those fields are
  meaningless for a historical replay with no exchange or network
  latency, and reusing that class here would also be a venue-specific
  infra module reaching into a venue-agnostic one. "CandleClosed event"
  is read literally as "a `Candle` (with `is_closed=True`) delivered via
  the subscribed handler" — the port's own, unmodified, existing shape.
- **The min-heap key is `(close_time, interval_duration_seconds,
  insertion_sequence)`, not `close_time` alone.** This is what makes "a
  1h bar closes only when all contained 1m bars have been processed"
  true *by construction*, not by any special-cased bookkeeping that
  tracks which 1m bars compose which 1h bar. The 60th (last) 1m bar of
  an hour and that hour's own 1h bar share the *exact same* close time
  (`open_time + step`); every other 1m bar of that hour closes
  strictly earlier and is already ordered first by the primary key
  alone. Breaking the tie by interval *duration* (shorter first)
  guarantees the 1h bar — a longer interval — always sorts after any
  same-instant 1m bar, with zero knowledge of which bars "belong" to
  which aggregate. `insertion_sequence` (a monotonically increasing
  counter) is a third tie-breaker purely to keep `heapq` from ever
  needing to compare the non-orderable `(symbol, timeframe)` key tuples
  it would otherwise fall through to on a genuine triple-tie (e.g. two
  different symbols' bars closing at the identical instant with the
  identical interval) — AC1 only requires *a* correct, deterministic
  chronological order in that case, not a specific tie-break rule
  between symbols.
- **Per-series input is assumed already sorted ascending by
  `open_time`**, matching the identical precedent already established
  by T-P1-05's `validate_candle_sequence` and T-P2-02's
  `CursorMarketDataView`.
- **Placed in `infrastructure/backtest/`.** ARCHITECTURE.md names
  `HistoricalFeed` explicitly, by this exact name, as one of backtest's
  "swapped adapters" (alongside `SimulatedClock` and `SimulatedVenue`)
  implementing a domain port for backtesting — the identical placement
  reasoning already used for `SimulatedClock`/`RealClock` (T-P2-01) and
  `CursorMarketDataView` (T-P2-02).
- **The Postgres loader (`load_candle_series_from_dataset_version`)
  resolves `symbol` via a join against `instrument`, not a caller-
  supplied `instrument_id -> symbol` mapping.** `DatasetVersion.symbol_set`
  (T-P1-12) is a tuple of `instrument_id`s; `Candle` (T-P0-07) carries no
  string symbol; `instrument.symbol` (T-P0-04/T-P1-02) already exists for
  exactly this resolution — reusing it via a join avoids duplicating a
  symbol-resolution mapping T-P2-02 left to its own caller, since here a
  DB connection is already in hand and the join is a single extra column.
- **`intervals` (which timeframes to load) is an explicit, required
  parameter of the loader — never auto-discovered.** `DatasetVersion`
  carries a `symbol_set` and a date range, but no interval/timeframe
  field at all (DATABASE.md §G, entity 20), so there is nothing to
  "discover" from the version record itself; and AC2's own scenario
  (a 1h bar merged correctly against its 60 1m constituents) only makes
  sense if the caller has *already decided* to load both "1m" and "1h"
  together. Auto-discovering every interval present in the table would
  be unrequested behavior no acceptance criterion asks for.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, time
from typing import Any

import sqlalchemy as sa

from domain.candle import Candle
from domain.dataset_version import DatasetVersion
from infrastructure.db.tables.market_data import candle as candle_table
from infrastructure.db.tables.reference import instrument as instrument_table
from infrastructure.jobs.ohlcv_backfill_job import interval_to_timedelta

_SeriesKey = tuple[str, str]
_HeapEntry = tuple[datetime, float, int, _SeriesKey]


class FeedExhausted(Exception):  # noqa: N818 — name fixed by docs/TASKS.md T-P2-03
    """Raised by `HistoricalFeed.next_event()` once every loaded candle,
    across every `(symbol, timeframe)` series, has already been
    delivered — never a silent stop."""


class HistoricalFeed:
    """`MarketDataFeed` port implementation (T-P0-07) for backtesting: a
    strictly monotonic, min-heap merge over one or more pre-loaded
    per-`(symbol, timeframe)` candle series.
    """

    def __init__(self, series: Mapping[tuple[str, str], Sequence[Candle]]) -> None:
        self._series: dict[_SeriesKey, tuple[Candle, ...]] = {
            key: tuple(candles) for key, candles in series.items()
        }
        self._cursor: dict[_SeriesKey, int] = dict.fromkeys(self._series, 0)
        self._handlers: dict[_SeriesKey, Callable[[Candle], Awaitable[None]]] = {}
        self._tie_breaker = itertools.count()
        self._heap: list[_HeapEntry] = []
        for key in self._series:
            self._push_next(key)

    def _push_next(self, key: _SeriesKey) -> None:
        idx = self._cursor[key]
        candles = self._series[key]
        if idx >= len(candles):
            return
        step = interval_to_timedelta(key[1])
        close_ts = candles[idx].open_time + step
        heapq.heappush(self._heap, (close_ts, step.total_seconds(), next(self._tie_breaker), key))

    async def subscribe(
        self, symbol: str, timeframe: str, handler: Callable[[Candle], Awaitable[None]]
    ) -> None:
        self._handlers[(symbol, timeframe)] = handler

    async def unsubscribe(self, symbol: str, timeframe: str) -> None:
        self._handlers.pop((symbol, timeframe), None)

    async def next_event(self) -> Candle:
        """Pop and return the next candle in strict chronological
        (close-time) order across every loaded series, invoking the
        handler subscribed for its `(symbol, timeframe)`, if any.
        Raises `FeedExhausted` once nothing is left."""
        if not self._heap:
            raise FeedExhausted("HistoricalFeed has delivered every loaded candle")

        _, _, _, key = heapq.heappop(self._heap)
        idx = self._cursor[key]
        candle = self._series[key][idx]
        self._cursor[key] = idx + 1
        self._push_next(key)

        handler = self._handlers.get(key)
        if handler is not None:
            await handler(candle)
        return candle


def load_candle_series_from_dataset_version(
    conn: sa.Connection,
    *,
    dataset_version: DatasetVersion,
    intervals: Sequence[str],
) -> dict[tuple[str, str], list[Candle]]:
    """Loads every `candle` row for `dataset_version`'s own `symbol_set`
    and `[date_range_start, date_range_end]`, across `intervals`, from
    Postgres — keyed by `(symbol, timeframe)`, each series ordered
    ascending by `open_time` (`HistoricalFeed`'s own precondition).
    Loading the same `dataset_version` again reads the same immutable
    rows and produces the same series, in the same order — TASKS.md's
    own "produces events deterministically."
    """
    range_start = datetime.combine(dataset_version.date_range_start, time.min, tzinfo=UTC)
    range_end = datetime.combine(dataset_version.date_range_end, time.max, tzinfo=UTC)

    rows: Sequence[Any] = conn.execute(
        sa.select(
            instrument_table.c.symbol,
            candle_table.c.instrument_id,
            candle_table.c.interval,
            candle_table.c.open_time,
            candle_table.c.open,
            candle_table.c.high,
            candle_table.c.low,
            candle_table.c.close,
            candle_table.c.volume,
            candle_table.c.is_closed,
        )
        .select_from(
            candle_table.join(
                instrument_table, candle_table.c.instrument_id == instrument_table.c.id
            )
        )
        .where(
            candle_table.c.instrument_id.in_(dataset_version.symbol_set),
            candle_table.c.interval.in_(intervals),
            candle_table.c.open_time >= range_start,
            candle_table.c.open_time <= range_end,
        )
        .order_by(candle_table.c.open_time)
    ).all()

    series: dict[tuple[str, str], list[Candle]] = {}
    for row in rows:
        key = (row.symbol, row.interval)
        series.setdefault(key, []).append(
            Candle(
                instrument_id=row.instrument_id,
                interval=row.interval,
                open_time=row.open_time,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                is_closed=row.is_closed,
            )
        )
    return series
