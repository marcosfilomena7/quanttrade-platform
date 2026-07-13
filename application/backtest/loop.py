"""Backtest Event Loop — the core simulation loop (TASKS.md T-P2-04).

"Implement the main backtest loop in `application/backtest/`: (1) load
dataset version; (2) init `SimulatedClock`; (3) init strategy and warm
up (discard signals during warmup); (4) loop: advance clock → update
`MarketDataView` cursor → process pending fills from prior bar → call
`strategy.on_data(event, view)` → if signal: pass to Portfolio → if
intent: pass to `SimulatedVenue` → queue fill. Warmup bars do not
trigger signals."

Design decisions, and why:

- **This module never imports `infrastructure/`.** The task places it
  literally in `application/backtest/`, and the Dependency Rule
  (`domain <- application <- infrastructure`, enforced by this repo's
  import-linter contract) forbids `application/` from importing
  `infrastructure/`. `HistoricalFeed`, `CursorMarketDataView`, and
  `SimulatedClock` (T-P2-01/02/03) are all concrete infrastructure
  adapters; this module references only `Candle`/`DatasetVersion`
  (plain domain values) and `Clock`/`MarketDataView` (domain ports),
  plus three small, *locally defined* Protocols (`EventSource`,
  `AdvanceableClock`, `AdvanceableMarketDataView`) that each extend an
  existing domain port with exactly the one extra method this loop
  needs beyond what that port already declares. Because Python
  `Protocol`s are structural, `HistoricalFeed`/`SimulatedClock`/
  `CursorMarketDataView` satisfy these richer local shapes automatically
  — they already have the extra methods — with zero import coupling in
  either direction. The one caller-visible consequence: whoever wires a
  real backtest run together (a test, or a future composition root)
  passes already-constructed `HistoricalFeed`/`SimulatedClock`/
  `CursorMarketDataView` instances in; *that* wiring code is free to
  import infrastructure, since it sits outside the three-layer stack.
- **`EventSource.next_closed_event()` returns `Candle | None`
  (`None` = exhausted), not an exception.** `HistoricalFeed.next_event()`
  (T-P2-03) raises `FeedExhausted` on exhaustion — the right contract
  for *that* module's own acceptance criterion ("exhausting the feed
  raises `FeedExhausted` rather than silently stopping"), but this
  layer cannot name that exception type without importing it. A plain
  `None` return is a value, not a type, so no import is needed; the one
  new method this required, `HistoricalFeed.next_closed_event()`, was
  added *additively* to T-P2-03's own file — `next_event()` itself is
  untouched, and all of T-P2-03's existing tests keep passing unchanged.
- **The loop only ever knows candles carry a *close time* it is handed,
  never re-deriving one from an interval string.** Computing a close
  time needs `interval_to_timedelta` (`infrastructure/jobs/
  ohlcv_backfill_job.py`, T-P1-04) — another `infrastructure/` import
  this layer cannot make. `HistoricalFeed.next_closed_event()` (see
  above) already computes it once, using the exact same helper T-P2-02's
  `CursorMarketDataView.advance()` itself relies on, and hands the loop
  a ready-made `(Candle, close_time)` pair.
- **`on_data` is called on every bar, including warmup bars — the loop
  discards the *signal*, not the call.** AC1's own scenario ("a strategy
  that generates a signal on every bar produces zero signals during its
  declared warmup period") only makes sense if `on_data` genuinely runs
  during warmup too (so a strategy's own indicators can warm up
  naturally); what changes is whether the loop *acts on* what `on_data`
  returns. This matches the task's own step list, where "call
  `strategy.on_data`" is one unconditional loop step and "warmup bars do
  not trigger signals" is a separate, following sentence about the
  *outcome*, not about skipping the call.
- **`BacktestStrategy` is a small, task-scoped Protocol — not the
  `Strategy` port.** T-P2-07 ("Strategy Port and Registry") is a
  separate, later task explicitly named for defining the real domain
  Strategy contract and a registry around it; defining a full one here
  would be doing that task's work early. `BacktestStrategy` declares
  only what T-P2-04's own four acceptance criteria actually need:
  `warmup_bars: int` (the strategy's own declared warmup length) and a
  synchronous `on_data(event, view) -> object | None` (pure computation,
  no I/O — matching `MarketDataView.bars()`'s own synchronous
  convention; a strategy never awaits anything). The signal type itself
  is left as a bare `object` — inventing a `Signal` domain value now
  would guess at a shape neither this task nor its dependencies define.
- **No Portfolio/SimulatedVenue integration; `on_signal` and
  `process_pending_fills` are optional, generic callables defaulting to
  no-ops.** T-P2-04's own dependency list is "T-P2-01, T-P2-02, T-P2-03,
  T-P0-07" — `SimulatedVenue` (T-P2-05/06) and the Strategy/Portfolio
  registry (T-P2-07 and later, no dedicated Portfolio task exists in
  this codebase's plan before Phase 4/7) are not among them, and none of
  T-P2-04's four acceptance criteria test fills, fees, or portfolio
  sizing — only warmup-signal gating and determinism. The loop still
  follows the description's own step order exactly (pending fills
  processed *before* `on_data` is called each bar) so that wiring a real
  `SimulatedVenue` in later is a matter of supplying a real callable,
  not restructuring this loop.
- **Determinism (AC4) needs no seed-consuming logic of its own.** This
  loop performs no randomness, no wall-clock reads, and iterates
  sequences (not unordered sets/dicts) throughout — given the same
  `event_source`/`view`/`clock`/`strategy` (i.e., the same dataset
  version, loaded the same way), two runs produce byte-identical
  `BacktestResult`s by construction. `seed` is accepted and recorded on
  the result purely for provenance (e.g., a future `BacktestRun` row,
  T-P2-12) — nothing in this module's own logic branches on it, since
  nothing here is stochastic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from domain.candle import Candle
from domain.dataset_version import DatasetVersion
from domain.ports import Clock, MarketDataView


@runtime_checkable
class EventSource(Protocol):
    """Pulls the next closed candle, in strict chronological order,
    paired with its own close time. `None` signals exhaustion.
    `HistoricalFeed` (T-P2-03) satisfies this via its own additive
    `next_closed_event()` method.
    """

    async def next_closed_event(self) -> tuple[Candle, datetime] | None: ...


@runtime_checkable
class AdvanceableClock(Clock, Protocol):
    """`Clock` (T-P0-07) plus the one extra method this loop needs to
    move simulated time forward. `SimulatedClock` (T-P2-01) already has
    both `now()` and `advance_to()`, satisfying this structurally."""

    def advance_to(self, ts: datetime) -> None: ...


@runtime_checkable
class AdvanceableMarketDataView(MarketDataView, Protocol):
    """`MarketDataView` (T-P0-07) plus the one extra method this loop
    needs to move the lookahead-safe cursor forward.
    `CursorMarketDataView` (T-P2-02) already has both `bars()` and
    `advance()`, satisfying this structurally."""

    def advance(self, ts: datetime) -> None: ...


@runtime_checkable
class BacktestStrategy(Protocol):
    """The minimal shape this loop needs from a strategy — not the full
    `Strategy` port (T-P2-07, a separate later task). `warmup_bars`
    declares how many bars `on_data` is called for before any signal it
    returns is honored. `on_data` is synchronous, pure computation: no
    I/O, matching `MarketDataView.bars()`'s own convention.
    """

    warmup_bars: int

    def on_data(self, event: Candle, view: MarketDataView) -> object | None: ...


@dataclass(frozen=True)
class BacktestResult:
    """The outcome of one `run_backtest` call."""

    dataset_version_id: UUID
    seed: int
    events: tuple[Candle, ...]
    bars_processed: int
    warmup_bars: int
    signal_opportunities: int
    signals_produced: int
    signals_discarded_during_warmup: int


async def run_backtest(
    *,
    dataset_version: DatasetVersion,
    event_source: EventSource,
    clock: AdvanceableClock,
    view: AdvanceableMarketDataView,
    strategy: BacktestStrategy,
    seed: int = 0,
    on_signal: Callable[[object], Awaitable[None]] | None = None,
    process_pending_fills: Callable[[datetime], Awaitable[None]] | None = None,
) -> BacktestResult:
    """Runs `strategy` over every event `event_source` yields, in order.

    (1) The dataset version is already loaded — `event_source` and
    `view` were built from it by the caller. (2) `clock` is a freshly
    constructed, uninitialized `SimulatedClock`. (3) `strategy
    .warmup_bars` declares the warmup period; signals produced during it
    are counted separately and never reach `on_signal`. (4) The loop
    itself, in the exact step order TASKS.md describes: advance clock →
    advance view → process pending fills → call `on_data` → (outside
    warmup) forward any signal.
    """
    events: list[Candle] = []
    bars_processed = 0
    signal_opportunities = 0
    signals_produced = 0
    signals_discarded_during_warmup = 0

    while True:
        next_item = await event_source.next_closed_event()
        if next_item is None:
            break
        candle, close_ts = next_item

        clock.advance_to(close_ts)
        view.advance(close_ts)

        if process_pending_fills is not None:
            await process_pending_fills(close_ts)

        events.append(candle)
        bars_processed += 1
        in_warmup = bars_processed <= strategy.warmup_bars

        signal = strategy.on_data(candle, view)

        if in_warmup:
            if signal is not None:
                signals_discarded_during_warmup += 1
            continue

        signal_opportunities += 1
        if signal is not None:
            signals_produced += 1
            if on_signal is not None:
                await on_signal(signal)

    return BacktestResult(
        dataset_version_id=dataset_version.id,
        seed=seed,
        events=tuple(events),
        bars_processed=bars_processed,
        warmup_bars=strategy.warmup_bars,
        signal_opportunities=signal_opportunities,
        signals_produced=signals_produced,
        signals_discarded_during_warmup=signals_discarded_during_warmup,
    )
