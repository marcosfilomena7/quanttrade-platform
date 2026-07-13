"""CursorMarketDataView — MarketDataView port implementation with
lookahead prevention by construction (TASKS.md T-P2-02).

"Implement `MarketDataView`: a read-only cursor over historical data.
The view holds a `current_ts` pointer. `bars(symbol, timeframe, n)`
returns the last `n` closed bars *before* `current_ts`. Attempting to
access data at or after `current_ts` is structurally impossible — not
blocked at runtime with an `if` check, but architecturally absent from
the return value. The view is advanced externally by the backtest loop
via `advance(ts)`. Strategies receive a `MarketDataView` — they have no
other data access path."

Design decisions, and why:

- **"Structurally impossible... architecturally absent," not an `if`
  check: `bars()` contains zero timestamp comparisons.** All lookahead-
  prevention logic lives exclusively in `advance()`, the only method
  that ever grows a per-`(symbol, timeframe)` "revealed count" pointer
  into the caller-supplied series. `bars()` itself is a plain slice —
  `self._series[key][:revealed_count][-n:]` — with no `if
  candle.open_time < current_ts` anywhere in it. A future bar cannot
  leak through `bars()` because, at the moment `bars()` runs, that bar
  is simply outside the slice bound `advance()` already computed; there
  is no per-read comparison for a bug to get wrong.
- **Placed in `infrastructure/backtest/`, not `application/`.**
  `MarketDataView` (`domain/ports/market_data.py`, T-P0-07) is a domain
  port; every other port implementation built so far in this codebase
  (`RealClock`/`SimulatedClock` for `Clock`, T-P2-01;
  `PostgresDatasetVersionRepository` for `DatasetVersionRepository`,
  T-P1-12) lives in `infrastructure/`, matching ARCHITECTURE.md's own
  framing of concrete port implementations as "adapters." T-P2-04's
  future backtest loop (`application/backtest/`) will *consume* this
  view; it does not need to *be* application-layer code itself.
  `infrastructure/backtest/` already exists (T-P1-12) and is the
  natural home for backtest-engine infrastructure.
- **Reuses `interval_to_timedelta` from
  `infrastructure/jobs/ohlcv_backfill_job.py` (T-P1-04), unmodified —
  not duplicated.** Determining "before `current_ts`" requires knowing
  each bar's *close* time (`open_time + interval step`), not just its
  open time (see the worked boundary example below); this mapping
  already exists and is exercised by four earlier tasks. Placing this
  view in `infrastructure/` (an infra-to-infra import, not a layer
  violation) is what makes this reuse possible instead of needing a
  duplicate copy — resolving the tension between "don't duplicate
  logic" and "respect the Dependency Rule" in favor of the (stricter,
  structural) Dependency Rule, which a same-layer import never violates.
- **The boundary is strict: a bar closing *exactly at* `current_ts` is
  excluded, not included.** Worked from AC1's own example: "`current_ts
  = T` returns the 10 bars ending at `T-1m` (not T)." For 1-minute
  bars, the bar "ending at T" has `open_time = T-1m`; its close time
  (`T-1m + 1m = T`) equals `current_ts` exactly — and it is explicitly
  excluded ("not T"). So the rule is `candle.open_time + step <
  current_ts`, strictly, not `<=`: at `current_ts = T`, `T` itself is
  still "the current, still-forming bar," never visible.
- **Constructor takes the complete, already-known series per
  `(symbol, timeframe)` — there is no separate `ingest`/`append`
  method.** `bars(symbol: str, timeframe: str, n: int)`'s own port
  signature uses plain string keys, and `Candle` (T-P0-07) carries no
  string `symbol` field at all — only `instrument_id: UUID` — so
  resolving which candles belong to which `(symbol, timeframe)` key is
  necessarily a caller-side concern (reference-data lookup), not
  something this view can derive from a bare `Candle`. TASKS.md's own
  description names exactly one externally-called mutator,
  `advance(ts)`; adding a second, differently-named ingestion method
  not named anywhere in the task's description or four acceptance
  criteria would be unrequested API surface. A future caller (T-P2-03's
  `HistoricalFeed`, which "loads bars from a `DatasetVersion`" up
  front) naturally already has the complete per-symbol series available
  before the backtest loop starts advancing the clock.
- **Per-series input is assumed already sorted ascending by
  `open_time` — not defensively re-sorted.** Matches the identical,
  already-established precedent in T-P1-05's
  `validate_candle_sequence`: "`candles` is assumed already ordered by
  `open_time`... this function does not re-sort." `advance()`'s
  forward-scanning pointer depends on this ordering to be correct and
  cheap (amortized O(1) per call across a full backtest run).
- **`bars()` never raises for `n` larger than what has been revealed —
  it returns however many bars are actually visible, including none.**
  No acceptance criterion specifies error behavior, and this is exactly
  what makes AC3's "clairvoyant" strategy scenario resolve to "zero
  signal": requesting an oversized `n` cannot reach past the revealed
  prefix; it just returns fewer bars, never a future one.
- **`current_ts` is exposed as a public read-only property.** The
  task's own description names "a `current_ts` pointer" as a defining
  feature of the view, not merely an implementation detail; exposing it
  costs nothing and is a literal, minimal fulfillment of that sentence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from domain.candle import Candle
from infrastructure.jobs.ohlcv_backfill_job import interval_to_timedelta

_SeriesKey = tuple[str, str]


class CursorMarketDataView:
    """`MarketDataView` port implementation (T-P0-07): a read-only,
    past-only cursor over a complete, pre-known candle series per
    `(symbol, timeframe)`. `advance(ts)` is the only way the cursor
    moves; `bars()` performs no timestamp comparison of its own — see
    this module's docstring for why that is the point.
    """

    def __init__(self, series: Mapping[tuple[str, str], Sequence[Candle]]) -> None:
        self._series: dict[_SeriesKey, tuple[Candle, ...]] = {
            key: tuple(candles) for key, candles in series.items()
        }
        self._revealed_count: dict[_SeriesKey, int] = dict.fromkeys(self._series, 0)
        self._current_ts: datetime | None = None

    @property
    def current_ts(self) -> datetime | None:
        """The most recent timestamp passed to `advance()`, or `None`
        before the first call."""
        return self._current_ts

    def advance(self, ts: datetime) -> None:
        """Reveal every pending bar, in every series, whose close time
        (`open_time + interval step`) is strictly before `ts`."""
        for key, candles in self._series.items():
            step = interval_to_timedelta(key[1])
            count = self._revealed_count[key]
            total = len(candles)
            while count < total and candles[count].open_time + step < ts:
                count += 1
            self._revealed_count[key] = count
        self._current_ts = ts

    def bars(self, symbol: str, timeframe: str, n: int) -> Sequence[Candle]:
        if n <= 0:
            return ()
        key = (symbol, timeframe)
        count = self._revealed_count.get(key, 0)
        revealed = self._series.get(key, ())[:count]
        return revealed[-n:]
