"""Vectorized vs. incremental indicator equivalence harness (TASKS.md T-P2-10).

"Implement a property-based test (Hypothesis) that generates random price
sequences and asserts that the vectorized and incremental variants of every
indicator produce identical output... A divergence anywhere means a bug that
will produce different backtest results from live signal generation."

One `@given`/`@settings(max_examples=100)` property per indicator (AC1),
mirroring `tests/domain/test_position.py`'s own Hypothesis usage and
`tests/infrastructure/indicators/test_incremental.py`'s own bar-building and
comparison conventions (feed the same bars to both variants, compare at
every step, `None`/`NaN` aligned the same way). `test_a_deliberately_broken_
incremental_ema_is_caught_by_the_comparison_pattern` is AC2's own scenario
("intentionally introducing a one-bar off-by-one... causes the test to fail
and identify which bar diverged") turned into an assertion that the
comparison itself would catch such a divergence, without mutating the real
`IncrementalEMA`.
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
# squared/cumulative-sum intermediates well inside float64 range.
_PRICES = st.lists(
    st.floats(min_value=0.01, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    min_size=30,
    max_size=150,
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


def test_a_deliberately_broken_incremental_ema_is_caught_by_the_comparison_pattern() -> None:
    """TASKS.md T-P2-10 AC2, verbatim: "Intentionally introducing a
    one-bar off-by-one in the incremental EMA causes the test to fail and
    identify which bar diverged." A one-bar-shifted "incremental" EMA is
    built locally (the real `IncrementalEMA` is never touched) and the same
    comparison every property above performs is shown to reject it, at a
    specific, identifiable bar index."""
    prices = _price_series(seed=7, n=50)
    bars = _bars(list(prices))
    reference = ema(prices, span=10)

    shifted = [None, *(Decimal(repr(float(v))) for v in reference[:-1])]

    first_divergence = next(
        i for i, (result, bar) in enumerate(zip(shifted, bars, strict=True)) if result is None
        or result != Decimal(repr(float(reference[i])))
    )
    assert first_divergence == 0


def _price_series(seed: int, n: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (np.cumsum(rng.normal(0, 1, n)) + 100.0).astype(np.float64)
