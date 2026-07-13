"""Tests for application/backtest/loop.py (TASKS.md T-P2-04).

Driven with real `HistoricalFeed`, `SimulatedClock`, and
`CursorMarketDataView` instances (T-P2-01/02/03) — this test file sits
outside the domain/application/infrastructure layering (like every
composition root or test in this repo), so it is free to import and
wire together concrete infrastructure adapters that satisfy
`application/backtest/loop.py`'s own locally defined Protocols
structurally, with zero import coupling in the production code itself.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from application.backtest.loop import BacktestResult, run_backtest
from domain.candle import Candle
from domain.dataset_version import DatasetVersion
from domain.ports import MarketDataView
from infrastructure.backtest.historical_feed import HistoricalFeed
from infrastructure.backtest.market_data_view import CursorMarketDataView
from infrastructure.clock import SimulatedClock

_INSTRUMENT_ID = uuid4()


def _candle(i: int, *, base: datetime) -> Candle:
    return Candle(
        instrument_id=_INSTRUMENT_ID,
        interval="1m",
        open_time=base + i * timedelta(minutes=1),
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close=Decimal("100.50"),
        volume=Decimal("10"),
        is_closed=True,
    )


def _dataset_version() -> DatasetVersion:
    return DatasetVersion(
        id=uuid4(),
        content_hash="abc123",
        symbol_set=(_INSTRUMENT_ID,),
        date_range_start=datetime(2026, 1, 1, tzinfo=UTC).date(),
        date_range_end=datetime(2026, 1, 2, tzinfo=UTC).date(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class _AlwaysSignalStrategy:
    """A strategy that returns a signal on *every* bar — including
    warmup ones — so the loop's own warmup-gating is what's under test,
    not the strategy's own behavior."""

    def __init__(self, warmup_bars: int) -> None:
        self.warmup_bars = warmup_bars
        self.on_data_calls: list[Candle] = []

    def on_data(self, event: Candle, view: MarketDataView) -> object | None:
        self.on_data_calls.append(event)
        return {"kind": "always-signal"}


class _NeverSignalStrategy:
    def __init__(self, warmup_bars: int) -> None:
        self.warmup_bars = warmup_bars

    def on_data(self, event: Candle, view: MarketDataView) -> object | None:
        return None


def _build_run(
    *, bar_count: int, warmup_bars: int, strategy_cls: type = _AlwaysSignalStrategy
) -> tuple[BacktestResult, object]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [_candle(i, base=base) for i in range(bar_count)]
    series = {("BTC/USDT", "1m"): candles}

    feed = HistoricalFeed(series)
    view = CursorMarketDataView(series)
    clock = SimulatedClock()
    strategy = strategy_cls(warmup_bars)

    signals_received: list[object] = []

    async def on_signal(signal: object) -> None:
        signals_received.append(signal)

    async def run() -> BacktestResult:
        return await run_backtest(
            dataset_version=_dataset_version(),
            event_source=feed,
            clock=clock,
            view=view,
            strategy=strategy,
            seed=42,
            on_signal=on_signal,
        )

    result = asyncio.run(run())
    return result, strategy


# --- acceptance criterion 1: zero signals during warmup ---------------------


def test_a_strategy_that_signals_every_bar_produces_zero_signals_during_warmup() -> None:
    """TASKS.md T-P2-04 acceptance criterion, verbatim: "A strategy that
    generates a signal on every bar produces zero signals during its
    declared warmup period.\""""
    result, strategy = _build_run(bar_count=20, warmup_bars=5)

    # The strategy itself IS called every bar, including warmup ones —
    # it's the loop's own signal-forwarding that's gated, not the call.
    assert len(strategy.on_data_calls) == 20  # type: ignore[attr-defined]
    assert result.signals_produced == 15  # only the 15 post-warmup bars
    assert result.signal_opportunities == 15


# --- acceptance criterion 2: warmup signals discarded, not counted ----------


def test_signals_from_warmup_bars_are_silently_discarded_not_counted_in_metrics() -> None:
    """TASKS.md T-P2-04 acceptance criterion, verbatim: "Signals from
    warmup bars are silently discarded, not counted in metrics.\""""
    result, _ = _build_run(bar_count=20, warmup_bars=5)

    assert result.signals_discarded_during_warmup == 5
    # "Not counted in metrics": the discarded count is tracked
    # separately and never inflates signals_produced.
    assert result.signals_produced == 15
    assert result.signals_produced + result.signals_discarded_during_warmup == 20


def test_on_signal_is_never_invoked_for_a_warmup_bars_signal() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [_candle(i, base=base) for i in range(10)]
    series = {("BTC/USDT", "1m"): candles}
    feed = HistoricalFeed(series)
    view = CursorMarketDataView(series)
    clock = SimulatedClock()
    strategy = _AlwaysSignalStrategy(warmup_bars=4)
    received: list[object] = []

    async def on_signal(signal: object) -> None:
        received.append(signal)

    async def run() -> BacktestResult:
        return await run_backtest(
            dataset_version=_dataset_version(),
            event_source=feed,
            clock=clock,
            view=view,
            strategy=strategy,
            on_signal=on_signal,
        )

    result = asyncio.run(run())
    assert len(received) == 6  # only the 6 post-warmup bars
    assert len(received) == result.signals_produced


# --- acceptance criterion 3: 1000 bars, 50-bar warmup, 950 opportunities ---


def test_1000_bars_with_50_bar_warmup_processes_exactly_950_signal_opportunities() -> None:
    """TASKS.md T-P2-04 acceptance criterion, verbatim: "A strategy
    running on 1000 bars with a 50-bar warmup processes exactly 950
    signal opportunities.\""""
    result, _ = _build_run(bar_count=1000, warmup_bars=50)

    assert result.bars_processed == 1000
    assert result.warmup_bars == 50
    assert result.signal_opportunities == 950
    assert result.signals_produced == 950
    assert result.signals_discarded_during_warmup == 50


def test_signal_opportunity_count_is_independent_of_whether_the_strategy_signals() -> None:
    """"Signal opportunities" counts bars the strategy *could* have
    acted on, regardless of whether it actually returned a signal."""
    result, _ = _build_run(bar_count=1000, warmup_bars=50, strategy_cls=_NeverSignalStrategy)

    assert result.signal_opportunities == 950
    assert result.signals_produced == 0
    assert result.signals_discarded_during_warmup == 0


# --- acceptance criterion 4: deterministic across runs ----------------------


def test_two_runs_with_the_same_dataset_version_and_seed_produce_the_same_event_sequence() -> (
    None
):
    """TASKS.md T-P2-04 acceptance criterion, verbatim: "The loop is
    deterministic: two runs with the same dataset version and seed
    produce the same sequence of events.\""""

    dataset_version = _dataset_version()  # same DatasetVersion record for both runs

    def one_run() -> BacktestResult:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        candles = [_candle(i, base=base) for i in range(100)]
        series = {("BTC/USDT", "1m"): candles}
        feed = HistoricalFeed(series)
        view = CursorMarketDataView(series)
        clock = SimulatedClock()
        strategy = _AlwaysSignalStrategy(warmup_bars=10)

        async def run() -> BacktestResult:
            return await run_backtest(
                dataset_version=dataset_version,
                event_source=feed,
                clock=clock,
                view=view,
                strategy=strategy,
                seed=7,
            )

        return asyncio.run(run())

    first = one_run()
    second = one_run()

    assert first.events == second.events
    assert first.bars_processed == second.bars_processed
    assert first.signal_opportunities == second.signal_opportunities
    assert first.signals_produced == second.signals_produced
    assert first.signals_discarded_during_warmup == second.signals_discarded_during_warmup
    assert first == second


# --- structural sanity -------------------------------------------------------


def test_the_loop_advances_the_clock_and_view_to_each_bars_close_time() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [_candle(i, base=base) for i in range(3)]
    series = {("BTC/USDT", "1m"): candles}
    feed = HistoricalFeed(series)
    view = CursorMarketDataView(series)
    clock = SimulatedClock()
    strategy = _NeverSignalStrategy(warmup_bars=0)

    async def run() -> None:
        await run_backtest(
            dataset_version=_dataset_version(),
            event_source=feed,
            clock=clock,
            view=view,
            strategy=strategy,
        )

    asyncio.run(run())

    last_close = candles[-1].open_time + timedelta(minutes=1)
    assert clock.now() == last_close
    assert view.current_ts == last_close
    # T-P2-02's own lookahead-prevention boundary: advancing to a bar's
    # own close time makes every *earlier* bar visible via view.bars(),
    # but never that bar itself — the strategy already received it
    # directly via `event`; view.bars() must never also hand it back.
    assert view.bars("BTC/USDT", "1m", 10) == tuple(candles[:2])


def test_an_empty_event_source_produces_a_zero_bar_result() -> None:
    view = CursorMarketDataView({})
    feed = HistoricalFeed({})
    clock = SimulatedClock()
    strategy = _NeverSignalStrategy(warmup_bars=5)

    async def run() -> BacktestResult:
        return await run_backtest(
            dataset_version=_dataset_version(),
            event_source=feed,
            clock=clock,
            view=view,
            strategy=strategy,
        )

    result = asyncio.run(run())
    assert result.bars_processed == 0
    assert result.events == ()
    assert result.signal_opportunities == 0


def test_process_pending_fills_is_called_once_per_bar_with_the_bars_close_time() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [_candle(i, base=base) for i in range(3)]
    series = {("BTC/USDT", "1m"): candles}
    feed = HistoricalFeed(series)
    view = CursorMarketDataView(series)
    clock = SimulatedClock()
    strategy = _NeverSignalStrategy(warmup_bars=0)
    seen_close_times: list[datetime] = []

    async def process_pending_fills(close_ts: datetime) -> None:
        seen_close_times.append(close_ts)

    async def run() -> None:
        await run_backtest(
            dataset_version=_dataset_version(),
            event_source=feed,
            clock=clock,
            view=view,
            strategy=strategy,
            process_pending_fills=process_pending_fills,
        )

    asyncio.run(run())
    expected = [c.open_time + timedelta(minutes=1) for c in candles]
    assert seen_close_times == expected
