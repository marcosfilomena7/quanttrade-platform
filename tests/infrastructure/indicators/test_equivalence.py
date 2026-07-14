"""Vectorized vs. incremental indicator equivalence harness (TASKS.md T-P2-10).

"Implement a property-based test (Hypothesis) that generates random price
sequences and asserts that the vectorized and incremental variants of every
indicator produce identical output... A divergence anywhere means a bug that
will produce different backtest results from live signal generation."

One `@given`/`@settings(max_examples=100)` property per indicator (AC1),
mirroring `tests/domain/test_position.py`'s own Hypothesis usage and
`tests/infrastructure/indicators/test_incremental.py`'s own bar-building and
comparison conventions (feed the same bars to both variants, compare at
every step, `None`/`NaN` aligned the same way).

`test_a_one_bar_off_by_one_bug_in_incremental_ema_is_caught_and_localized`
is AC2's own scenario ("intentionally introducing a one-bar off-by-one...
causes the test to fail and identify which bar diverged"). `_OffByOneEMA`
is a genuine, `update()`-driven test double carrying that exact bug (it
applies each bar's close on the *next* call instead of its own) — never
the real `IncrementalEMA`, which is untouched — fed through the same
loop-and-compare pattern the seven properties above use. The bug's first
observable effect lands at bar 1, not bar 0: both the correct and the
buggy EMA seed identically from the first bar's own close, so the lag
is invisible until the second bar, which is exactly where the comparison
below asserts the divergence is first detected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.candle import Candle
from infrastructure.indicators.incremental import (
    IncrementalATR,
    IncrementalBollingerBands,
    IncrementalEMA,
    IncrementalMACD,
    IncrementalRollingVolatility,
    IncrementalRSI,
    IncrementalSMA,
)
from infrastructure.indicators.vectorized import (
    atr,
    bollinger_bands,
    ema,
    macd,
    rolling_volatility,
    rsi,
    sma,
)

_INSTRUMENT_ID = uuid4()
_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)

# Strictly positive, finite, moderate-magnitude prices: rolling_volatility
# divides by the previous close, so zero must be excluded; the bound keeps
# squared/cumulative-sum intermediates well inside float64 range. Hypothesis's
# own per-example generation/bookkeeping cost (not the indicator math itself)
# dominates this module's runtime — `--hypothesis-show-statistics` measured
# ~1.2-1.6s of *generate-phase* time per 100-example property regardless of
# list size in the 30-150 range tested, so `min_size`/`max_size` are kept
# just wide enough to comfortably clear every indicator's own default period
# (26, MACD's slow_span, is the largest) while minimizing that per-draw cost:
# small enough that all seven 100-example properties plus the AC2 scenario
# finish under AC3's 10-second CI budget (measured ~7-8s total at this size,
# vs. ~9.5-19s at the previous, wider ranges).
_PRICES = st.lists(
    st.floats(min_value=0.01, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    min_size=27,
    max_size=40,
)


def _bars(prices: list[float]) -> list[Candle]:
    """Same construction as `test_incremental.py`'s own `_bars()`: `high`/
    `low` offset by 1 from `close` so `atr()`'s true-range formula sees a
    non-degenerate range."""
    return [
        Candle(
            instrument_id=_INSTRUMENT_ID,
            interval="1m",
            open_time=_BASE_TS + timedelta(minutes=i),
            open=Decimal(repr(float(price))),
            high=Decimal(repr(float(price) + 1.0)),
            low=Decimal(repr(float(price) - 1.0)),
            close=Decimal(repr(float(price))),
            volume=Decimal("10"),
            is_closed=True,
        )
        for i, price in enumerate(prices)
    ]


def _assert_matches_reference(result: Decimal | None, reference: float) -> None:
    if np.isnan(reference):
        assert result is None
    else:
        assert result == Decimal(repr(reference))


@given(_PRICES)
@settings(max_examples=100)
def test_incremental_sma_matches_vectorized_sma_on_random_price_sequences(
    prices: list[float],
) -> None:
    bars = _bars(prices)
    reference = sma(np.asarray(prices, dtype=np.float64), 20)
    indicator = IncrementalSMA(20)
    for i, bar in enumerate(bars):
        _assert_matches_reference(indicator.update(bar), float(reference[i]))


@given(_PRICES)
@settings(max_examples=100)
def test_incremental_ema_matches_vectorized_ema_on_random_price_sequences(
    prices: list[float],
) -> None:
    bars = _bars(prices)
    reference = ema(np.asarray(prices, dtype=np.float64), span=10)
    indicator = IncrementalEMA(span=10)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        assert result == Decimal(repr(float(reference[i])))


@given(_PRICES)
@settings(max_examples=100)
def test_incremental_rsi_matches_vectorized_rsi_on_random_price_sequences(
    prices: list[float],
) -> None:
    bars = _bars(prices)
    reference = rsi(np.asarray(prices, dtype=np.float64), period=14)
    indicator = IncrementalRSI(period=14)
    for i, bar in enumerate(bars):
        _assert_matches_reference(indicator.update(bar), float(reference[i]))


@given(_PRICES)
@settings(max_examples=100)
def test_incremental_atr_matches_vectorized_atr_on_random_price_sequences(
    prices: list[float],
) -> None:
    bars = _bars(prices)
    values = np.asarray(prices, dtype=np.float64)
    reference = atr(values + 1.0, values - 1.0, values, period=14)
    indicator = IncrementalATR(period=14)
    for i, bar in enumerate(bars):
        _assert_matches_reference(indicator.update(bar), float(reference[i]))


@given(_PRICES)
@settings(max_examples=100)
def test_incremental_bollinger_bands_matches_vectorized_on_random_price_sequences(
    prices: list[float],
) -> None:
    bars = _bars(prices)
    reference = bollinger_bands(np.asarray(prices, dtype=np.float64), period=20, num_std=2.0)
    indicator = IncrementalBollingerBands(period=20, num_std=2.0)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        if np.isnan(reference.middle[i]):
            assert result is None
        else:
            assert result is not None
            assert result.upper == Decimal(repr(float(reference.upper[i])))
            assert result.middle == Decimal(repr(float(reference.middle[i])))
            assert result.lower == Decimal(repr(float(reference.lower[i])))


@given(_PRICES)
@settings(max_examples=100)
def test_incremental_macd_matches_vectorized_macd_on_random_price_sequences(
    prices: list[float],
) -> None:
    bars = _bars(prices)
    values = np.asarray(prices, dtype=np.float64)
    reference = macd(values, fast_span=12, slow_span=26, signal_span=9)
    indicator = IncrementalMACD(fast_span=12, slow_span=26, signal_span=9)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        assert result.macd == Decimal(repr(float(reference.macd[i])))
        assert result.signal == Decimal(repr(float(reference.signal[i])))
        assert result.histogram == Decimal(repr(float(reference.histogram[i])))


@given(_PRICES)
@settings(max_examples=100)
def test_incremental_rolling_volatility_matches_vectorized_on_random_price_sequences(
    prices: list[float],
) -> None:
    bars = _bars(prices)
    reference = rolling_volatility(np.asarray(prices, dtype=np.float64), period=20)
    indicator = IncrementalRollingVolatility(period=20)
    for i, bar in enumerate(bars):
        _assert_matches_reference(indicator.update(bar), float(reference[i]))


def _off_by_one_ema_step(previous: float | None, value: float, span: int) -> float:
    """The exact same recurrence `IncrementalEMA`'s own `_ema_step` uses
    — reproduced locally (not imported) so `_OffByOneEMA` below is a
    self-contained test double, not a partially-real one."""
    if previous is None:
        return value
    alpha = 2.0 / (span + 1)
    return previous + alpha * (value - previous)


class _OffByOneEMA:
    """A test-only double for `IncrementalEMA` carrying an intentional
    one-bar-lag bug: each `update(bar)` call applies the *previous*
    bar's close instead of the current one. Exists only to prove the
    comparison pattern used by every property above actually catches
    and localizes such a bug — the real `IncrementalEMA` is never
    touched or imported here."""

    def __init__(self, span: int) -> None:
        self._span = span
        self._value: float | None = None
        self._pending_close: float | None = None

    def update(self, bar: Candle) -> Decimal:
        close = float(bar.close)
        input_value = self._pending_close if self._pending_close is not None else close
        self._value = _off_by_one_ema_step(self._value, input_value, self._span)
        self._pending_close = close
        return Decimal(repr(self._value))


def test_a_one_bar_off_by_one_bug_in_incremental_ema_is_caught_and_localized() -> None:
    """TASKS.md T-P2-10 AC2, verbatim: "Intentionally introducing a
    one-bar off-by-one in the incremental EMA causes the test to fail
    and identify which bar diverged." `_OffByOneEMA` (above) is fed the
    same bars, through the same per-bar comparison every property in
    this module uses, and the first divergent bar is asserted to be
    exactly bar 1 — not merely "some" bar — confirming the comparison
    both fails *and* pinpoints where."""
    prices = _price_series(seed=7, n=50)
    bars = _bars(list(prices))
    reference = ema(prices, span=10)

    broken = _OffByOneEMA(span=10)
    actual = [broken.update(bar) for bar in bars]

    divergence = next(
        (i for i, value in enumerate(actual) if value != Decimal(repr(float(reference[i])))),
        None,
    )

    assert divergence is not None, "expected the one-bar-lag bug to be detected"
    assert divergence == 1, f"expected the bug to first manifest at bar 1, not bar {divergence}"


def _price_series(seed: int, n: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (np.cumsum(rng.normal(0, 1, n)) + 100.0).astype(np.float64)
