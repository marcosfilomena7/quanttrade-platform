"""Determinism Harness (TASKS.md T-P2-13).

"Implement a determinism test that runs the same strategy on the same
`DatasetVersion` twice with the same seed and asserts bit-identical
output: same sequence of signals, same fills, same final metrics. Also
test cross-machine reproducibility by storing a golden-file output and
comparing on every CI run. A divergence anywhere means a non-deterministic
path exists (e.g., `datetime.now()`, non-seeded random, dict ordering)."

Design decisions, and why:

- **Placed as a test module, not new production code.** T-P2-13's own
  title is "Determinism *Harness*" — the identical framing already used
  for T-P2-10 ("Vectorized vs. Incremental Equivalence Test *Harness*"),
  which this codebase already resolved the same way:
  `tests/infrastructure/indicators/test_equivalence.py`, a pure pytest
  module with no corresponding `application/`/`infrastructure/` file.
  Nothing about "run twice and compare" or "compare against a stored
  file" is a capability a production module needs to expose; it is
  entirely a test concern. Placed at
  `tests/application/backtest/test_determinism.py` since it exercises
  `application/backtest/loop.py` (T-P2-04) and `application/backtest
  /metrics.py` (T-P2-11) together — the same directory as
  `test_loop.py` and `test_backtest_metrics.py`.
- **"The reference strategy" does not yet exist anywhere in this
  codebase, and is not built here.** T-P3-01 ("Reference Strategy 1 —
  EMA Crossover") is the task that will eventually own a real
  `domain.strategy.Strategy` subclass named for exactly this purpose —
  and it is not a listed dependency of T-P2-13 (whose own dependency
  list is only "T-P2-12, T-P2-04"). This is the *identical* situation
  T-P2-12 already faced and already resolved, verbatim, in this same
  codebase: `tests/infrastructure/backtest/test_run_registry_integration
  .py`'s own `_ReferenceStrategy` docstring reads "A fixture 'strategy
  class'... stands in for a real `domain.strategy.Strategy` subclass
  without depending on T-P2-07 (not one of T-P2-12's own listed
  dependencies)." The same resolution is reused here, one dependency
  layer further down: this module's own `_ReferenceStrategy` is a small,
  deterministic, test-local fixture satisfying `application/backtest
  /loop.py`'s `BacktestStrategy` Protocol (T-P2-04) — not the full
  `domain.strategy.Strategy` ABC (T-P2-07), and not the eventual EMA
  crossover (T-P3-01), both later, non-blocking tasks. Its trading rule
  (toggle long/flat every `_TOGGLE_EVERY` bars) is deliberately simple
  and self-contained: this harness's job is to prove *the engine*
  reproduces bit-identically, not to validate any particular strategy's
  trading logic.
- **"Same fills" is satisfied via real `domain.fill.Fill` objects (T-P0-
  06), constructed directly — not via `domain.fill_simulation
  .simulate_fill` (T-P2-06) or a full `domain.order.Order` (T-P0-05).**
  T-P2-13's own dependency list names neither T-P2-05 nor T-P2-06;
  pulling in `simulate_fill`'s full `Order`/`Instrument`/`FeeSchedule`
  plumbing (none of which this task depends on) to answer "did the same
  signal produce the same fill" would be solving a much larger, out-of-
  scope problem. `Fill.order_id` is a bare `UUID` foreign key with no
  requirement that a matching `Order` object exist in memory — a
  deterministic `Fill` (fixed qty, fixed fee rate, price taken from the
  next bar's open — the same "no lookahead, fill at t+1" rule T-P2-06
  already establishes) is constructed directly for each signal. Every
  identifier (`Fill.id`, `Fill.order_id`, `Fill.venue_fill_id`) is
  derived deterministically from the signal's own bar index via
  `uuid.uuid5` (a pure function of its inputs, unlike `uuid4`, which
  reads OS randomness) — required for two independent runs, or two
  different machines, to produce byte-identical `Fill` objects rather
  than merely equivalent ones with different random IDs.
- **"Same final metrics" is read literally as T-P2-11's own
  `Tearsheet`.** AC1's own wording is explicit — "produce bit-identical
  *tearsheets*" — a proper noun this codebase already defines
  (`application.backtest.metrics.Tearsheet`). `compute_tearsheet` (T-P2-
  11, already committed) is reused unmodified against a real
  `equity_curve` built by replaying the harness's own deterministic
  fills through `domain.position.Position` (T-P0-06, also already
  committed) — mark-to-market equity at every bar close, exactly the
  same "reuse, don't reimplement" precedent `metrics.py`'s own module
  docstring already established for per-trade P&L. TASKS.md's front-
  matter rule ("Dependencies list only blocking predecessors") is read
  as a scheduling statement, not a ban on reusing already-completed
  code from earlier, non-blocking tasks — T-P2-11 finished before T-P2-
  12 even started, so nothing here waits on it; using its output type
  is the literal opposite of "adelantar trabajo de tareas futuras."
- **Golden-file storage covers the full deterministic output — signals,
  fills, *and* the tearsheet — not the tearsheet alone.** The task's own
  description states the bit-identical output the harness must guard as
  three explicit parts ("same sequence of signals, same fills, same
  final metrics"); a golden file holding only the aggregate `Tearsheet`
  could not, even in principle, catch a divergence that happened to
  cancel out in the final aggregate numbers (e.g., two engines producing
  different signal timing or fill IDs that nonetheless summed to the
  same Sharpe/CAGR) — exactly the "a divergence anywhere" (the task's
  own words) this harness exists to catch. `_serialize_pipeline_result`
  turns one `_PipelineResult` into a plain, JSON-native `dict` (`signals`
  → `{side, bar_index}`; `fills` → every field, `Decimal`s and the `UUID`
  as `str`, `ts` as ISO-8601; `tearsheet` → passed through unchanged,
  already JSON-native per T-P2-11's own AC4) — the golden file is
  exactly this serialized shape, and the comparison is plain `==` after
  `json.loads`, with no custom decoding needed on the read side.
- **AC3 ("introducing a `random.random()` call... breaks the
  determinism test") is demonstrated with a second, throwaway strategy
  class, `_NonDeterministicReferenceStrategy`** — identical to
  `_ReferenceStrategy` except its toggle decision additionally consults
  an *unseeded* `random.random()` on every bar. Python's global `random`
  module is never reset between the two calls this test makes in the
  same process, so its state carries over from the first invocation
  into the second, exactly modeling a strategy bug that silently
  destroys reproducibility. The random draw happens on every one of the
  60 bars (not only at toggle points), making the probability that two
  independent runs coincidentally produce an identical 60-draw sequence
  astronomically small (rather than relying on a handful of draws, which
  could flake in CI over enough runs).
- **AC4 ("changing the `DatasetVersion` used breaks the golden-file
  test") is demonstrated by re-running the identical strategy and
  pipeline against a second, distinct candle series** (a monotonic
  uptrend, vs. the golden dataset's triangle-wave oscillation) **and
  asserting its tearsheet does not equal the golden file's stored
  content.** `Tearsheet` carries no `dataset_version_id` field of its
  own (T-P2-11 never threads one through), so "the DatasetVersion
  changed" is observed the only way it *can* be observed from a
  tearsheet alone: the underlying data — and therefore the computed
  metrics — differ. This also guards against the golden-file test being
  vacuously true (i.e., proves the comparison is actually sensitive to
  the dataset, not a tautology that would pass no matter what ran).
- **All identifiers, timestamps, and the price series itself are fixed,
  literal, or `uuid.uuid5`-derived — never `uuid4()`, `datetime.now()`,
  or unseeded randomness (outside the one deliberate AC3 exception).**
  This is what makes the "positive" tests genuinely test determinism
  rather than assume it: every run, on any machine, computes the exact
  same inputs from scratch.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from application.backtest.loop import run_backtest
from application.backtest.metrics import EquityPoint, Tearsheet, compute_tearsheet
from domain.candle import Candle
from domain.dataset_version import DatasetVersion
from domain.fill import Fill
from domain.money import Money
from domain.order import OrderSide
from domain.ports import MarketDataView
from domain.position import Position
from infrastructure.backtest.historical_feed import HistoricalFeed
from infrastructure.backtest.market_data_view import CursorMarketDataView
from infrastructure.clock import SimulatedClock

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "quanttrade:t-p2-13:determinism-harness")


def _deterministic_uuid(label: str) -> UUID:
    """A `uuid.uuid5` derivation — a pure function of `label` alone,
    unlike `uuid.uuid4()`, which reads OS randomness. Required so two
    independent runs (or two different machines) construct
    byte-identical `Fill` objects, not merely equivalent ones."""
    return uuid.uuid5(_NAMESPACE, label)


_INSTRUMENT_ID = _deterministic_uuid("instrument")
_STRATEGY_INSTANCE_ID = _deterministic_uuid("strategy-instance")
_DATASET_VERSION_ID = _deterministic_uuid("dataset-version")
_SYMBOL = "BTC/USDT"
_TIMEFRAME = "1m"
_CURRENCY = "USDT"
_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)
_BAR_COUNT = 60
_WARMUP_BARS = 5
_TOGGLE_EVERY = 8
_SEED = 7
_FILL_QTY = Decimal("1")
_FEE_RATE = Decimal("0.001")
_STARTING_CASH = Decimal("100000")

_GOLDEN_PATH = Path(__file__).parent / "golden" / "reference_strategy_output.json"


@dataclass(frozen=True, slots=True)
class _Signal:
    """The minimal, test-local signal shape `_ReferenceStrategy` returns.
    `application/backtest/loop.py`'s own `BacktestStrategy.on_data`
    leaves the signal type as a bare `object` (T-P2-04's own deliberate
    scoping) — this is this harness's own concrete choice of shape."""

    side: OrderSide
    bar_index: int


class _ReferenceStrategy:
    """A deterministic fixture "reference strategy" for this harness
    alone — see this module's own docstring for why this is not T-P3-01's
    (not-yet-built) real EMA crossover, and not T-P2-07's `Strategy` ABC.
    Toggles between long and flat every `_TOGGLE_EVERY` bars, purely as a
    function of its own bar counter — no randomness, no wall clock, no
    I/O.
    """

    def __init__(self) -> None:
        self.warmup_bars = _WARMUP_BARS
        self._bar_index = 0
        self._position_open = False

    def on_data(self, event: Candle, view: MarketDataView) -> object | None:
        index = self._bar_index
        self._bar_index += 1
        if index % _TOGGLE_EVERY != 0:
            return None
        side = OrderSide.SELL if self._position_open else OrderSide.BUY
        self._position_open = not self._position_open
        return _Signal(side=side, bar_index=index)


class _NonDeterministicReferenceStrategy:
    """TASKS.md T-P2-13 AC3, verbatim: "Introducing a `random.random()`
    call in the strategy breaks the determinism test." Otherwise
    identical to `_ReferenceStrategy`, except the toggle decision is
    additionally gated by an *unseeded* `random.random()` call on every
    bar — Python's global `random` state is never reset between the two
    "runs" a test in this module makes within one process, so the two
    calls draw from a continuing, un-repeating stream.
    """

    def __init__(self) -> None:
        self.warmup_bars = _WARMUP_BARS
        self._bar_index = 0
        self._position_open = False

    def on_data(self, event: Candle, view: MarketDataView) -> object | None:
        index = self._bar_index
        self._bar_index += 1
        if random.random() > 0.5:  # noqa: S311 — deliberately non-deterministic, for AC3
            return None
        side = OrderSide.SELL if self._position_open else OrderSide.BUY
        self._position_open = not self._position_open
        return _Signal(side=side, bar_index=index)


def _triangle_close(i: int) -> Decimal:
    """A deterministic, oscillating (triangle-wave) price series —
    non-constant, so returns have genuine variance for Sharpe/Sortino,
    computed from pure `int` arithmetic (never `float`)."""
    triangle = abs((i % 16) - 8)
    return Decimal(100) + Decimal(triangle - 4)


def _trend_close(i: int) -> Decimal:
    """A distinct, deterministic price series (a monotonic uptrend) used
    only for AC4's "changing the DatasetVersion" negative test."""
    return Decimal(100) + Decimal(i)


def _build_candles(bar_count: int, close_fn: Callable[[int], Decimal]) -> list[Candle]:
    candles: list[Candle] = []
    previous_close: Decimal | None = None
    for i in range(bar_count):
        close = close_fn(i)
        open_ = previous_close if previous_close is not None else close
        candles.append(
            Candle(
                instrument_id=_INSTRUMENT_ID,
                interval=_TIMEFRAME,
                open_time=_BASE_TS + timedelta(minutes=i),
                open=open_,
                high=max(open_, close) + Decimal(1),
                low=min(open_, close) - Decimal(1),
                close=close,
                volume=Decimal(10),
                is_closed=True,
            )
        )
        previous_close = close
    return candles


def _reference_candles() -> list[Candle]:
    return _build_candles(_BAR_COUNT, _triangle_close)


def _alternate_candles() -> list[Candle]:
    return _build_candles(_BAR_COUNT, _trend_close)


def _reference_dataset_version(*, content_hash: str = "determinism-harness-v1") -> DatasetVersion:
    return DatasetVersion(
        id=_DATASET_VERSION_ID,
        content_hash=content_hash,
        symbol_set=(_INSTRUMENT_ID,),
        date_range_start=_BASE_TS.date(),
        date_range_end=(_BASE_TS + timedelta(minutes=_BAR_COUNT)).date(),
        created_at=_BASE_TS,
    )


def _signals_to_fills(signals: Sequence[_Signal], candles: Sequence[Candle]) -> list[Fill]:
    """Deterministically turns each signal into a `Fill` executed against
    the *next* bar's open (never the signal bar's own close — the same
    no-lookahead convention T-P2-06's `simulate_fill` already
    establishes), at a fixed quantity and fee rate. A signal on the
    series' own last bar has no next bar to fill against and is dropped —
    it would simply carry forward, unfilled, to a bar this harness's
    fixed-length series does not include."""
    fills: list[Fill] = []
    for i, signal in enumerate(signals):
        next_index = signal.bar_index + 1
        if next_index >= len(candles):
            continue
        next_bar = candles[next_index]
        price = next_bar.open
        fee_amount = price * _FILL_QTY * _FEE_RATE
        fills.append(
            Fill(
                id=_deterministic_uuid(f"fill-{i}"),
                order_id=_deterministic_uuid(f"order-{i}"),
                venue_fill_id=f"determinism-fill-{i}",
                side=signal.side,
                qty=_FILL_QTY,
                price=price,
                fee=Money(fee_amount, _CURRENCY),
                ts=next_bar.open_time,
                is_maker=False,
            )
        )
    return fills


def _equity_curve(candles: Sequence[Candle], fills: Sequence[Fill]) -> list[EquityPoint]:
    """Mark-to-market equity at every bar's close: starting cash, plus
    `Position`'s own running realized P&L (T-P0-06, reused unmodified),
    plus unrealized P&L on any open position at that bar's close price.
    """
    fills_by_ts = {fill.ts: fill for fill in fills}
    position = Position.flat(_INSTRUMENT_ID, _STRATEGY_INSTANCE_ID, _CURRENCY)
    points: list[EquityPoint] = []
    for candle in candles:
        fill = fills_by_ts.get(candle.open_time)
        if fill is not None:
            position = position.apply_fill(fill)
        unrealized = (
            position.qty * (candle.close - position.avg_entry)
            if position.qty != 0
            else Decimal(0)
        )
        equity = _STARTING_CASH + position.realized_pnl.amount + unrealized
        points.append(EquityPoint(ts=candle.open_time, equity=equity))
    return points


@dataclass(frozen=True, slots=True)
class _PipelineResult:
    signals: tuple[_Signal, ...]
    fills: tuple[Fill, ...]
    tearsheet: Tearsheet


def _serialize_signal(signal: _Signal) -> dict[str, object]:
    return {"side": signal.side.value, "bar_index": signal.bar_index}


def _serialize_fill(fill: Fill) -> dict[str, object]:
    return {
        "id": str(fill.id),
        "order_id": str(fill.order_id),
        "venue_fill_id": fill.venue_fill_id,
        "side": fill.side.value,
        "qty": str(fill.qty),
        "price": str(fill.price),
        "fee_amount": str(fill.fee.amount),
        "fee_currency": fill.fee.currency,
        "ts": fill.ts.isoformat(),
        "is_maker": fill.is_maker,
    }


def _serialize_pipeline_result(result: _PipelineResult) -> dict[str, object]:
    """Turns one `_PipelineResult` into a plain, JSON-native `dict`
    covering *every* part of TASKS.md T-P2-13's own "bit-identical
    output" claim — signals, fills, and the tearsheet — not the
    tearsheet alone. This is the exact shape stored in, and compared
    against, the golden file: a divergence in signal timing or in any
    individual fill is caught here even if it happened to leave the
    aggregate tearsheet unchanged."""
    return {
        "signals": [_serialize_signal(signal) for signal in result.signals],
        "fills": [_serialize_fill(fill) for fill in result.fills],
        "tearsheet": dict(result.tearsheet),
    }


def _run_reference_backtest(
    strategy_cls: type = _ReferenceStrategy,
    candles: Sequence[Candle] | None = None,
) -> _PipelineResult:
    """Runs `strategy_cls` once, end to end: `run_backtest` (T-P2-04)
    over a fresh `HistoricalFeed`/`CursorMarketDataView`/`SimulatedClock`
    to collect signals, deterministically converts them to `Fill`s,
    replays those fills into an equity curve, and computes the resulting
    `Tearsheet` (T-P2-11). A brand-new strategy/clock/view instance is
    built each call — this function is exactly what "a run" means for
    every test in this module, so calling it twice is the literal
    "run twice" TASKS.md T-P2-13 describes.
    """
    resolved_candles = list(candles) if candles is not None else _reference_candles()
    series = {(_SYMBOL, _TIMEFRAME): resolved_candles}
    feed = HistoricalFeed(series)
    view = CursorMarketDataView(series)
    clock = SimulatedClock()
    strategy = strategy_cls()
    signals: list[_Signal] = []

    async def on_signal(signal: object) -> None:
        assert isinstance(signal, _Signal)
        signals.append(signal)

    async def run() -> None:
        await run_backtest(
            dataset_version=_reference_dataset_version(),
            event_source=feed,
            clock=clock,
            view=view,
            strategy=strategy,
            seed=_SEED,
            on_signal=on_signal,
        )

    asyncio.run(run())

    fills = _signals_to_fills(signals, resolved_candles)
    equity_curve = _equity_curve(resolved_candles, fills)
    tearsheet = compute_tearsheet(equity_curve=equity_curve, fills=fills)
    return _PipelineResult(signals=tuple(signals), fills=tuple(fills), tearsheet=tearsheet)


# --- acceptance criterion 1: two local runs, bit-identical tearsheets --------


def test_two_local_runs_produce_bit_identical_signals_fills_and_tearsheets() -> None:
    """TASKS.md T-P2-13 acceptance criterion, verbatim: "Two local runs of
    the reference strategy produce bit-identical tearsheets." The task's
    own description states the full bit-identical-output claim this
    harness must guard as three explicit parts — "same sequence of
    signals, same fills, same final metrics" — so all three are asserted
    independently here, not just the aggregate tearsheet: a divergence in
    signal timing or in any individual fill, even one that happened not
    to move the final tearsheet, must still fail this test."""
    first = _run_reference_backtest()
    second = _run_reference_backtest()

    assert first.signals == second.signals
    assert first.fills == second.fills
    assert first.tearsheet == second.tearsheet


def test_the_reference_strategy_produces_at_least_one_signal_and_fill() -> None:
    """Structural sanity guard: without this, the two tests above could
    pass vacuously (an empty pipeline is trivially "identical" to
    itself)."""
    result = _run_reference_backtest()
    assert len(result.signals) > 0
    assert len(result.fills) > 0


# --- acceptance criterion 2: golden-file cross-machine reproducibility ------


def test_golden_file_matches_stored_signals_fills_and_tearsheet() -> None:
    """TASKS.md T-P2-13 acceptance criterion, verbatim: "The golden-file
    test passes in CI (i.e., the CI environment produces the same output
    as the local golden file)." The golden file was generated once from
    this exact pipeline and is committed alongside this test
    (`golden/reference_strategy_output.json`) — it holds the full
    deterministic output (signals, fills, and the tearsheet), not the
    tearsheet alone, so any environment recomputing a different signal
    sequence, a different fill, or a different tearsheet is caught here;
    a divergence anywhere in the pipeline is a genuine reproducibility
    regression this test is designed to catch by construction."""
    result = _run_reference_backtest()
    serialized = _serialize_pipeline_result(result)
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert serialized == golden


# --- acceptance criterion 3: random.random() in the strategy breaks it ------


def test_introducing_random_random_in_the_strategy_breaks_the_determinism_test() -> None:
    """TASKS.md T-P2-13 acceptance criterion, verbatim: "Introducing a
    `random.random()` call in the strategy breaks the determinism test.\""""
    first = _run_reference_backtest(_NonDeterministicReferenceStrategy)
    second = _run_reference_backtest(_NonDeterministicReferenceStrategy)

    assert first.signals != second.signals


# --- acceptance criterion 4: changing the DatasetVersion breaks golden-file -


def test_changing_the_dataset_version_used_breaks_the_golden_file_comparison() -> None:
    """TASKS.md T-P2-13 acceptance criterion, verbatim: "Changing the
    `DatasetVersion` used breaks the golden-file test." A distinct candle
    series (a monotonic uptrend, vs. the golden dataset's oscillating
    triangle wave) run through the identical strategy and pipeline must
    not coincidentally reproduce the golden file's own signals, fills, or
    tearsheet — this also proves the golden-file comparison is actually
    sensitive to the dataset used, not vacuously true regardless of what
    ran."""
    result = _run_reference_backtest(candles=_alternate_candles())
    serialized = _serialize_pipeline_result(result)
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert serialized != golden
