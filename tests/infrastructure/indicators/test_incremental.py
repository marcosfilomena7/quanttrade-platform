"""Tests for infrastructure/indicators/incremental/ (TASKS.md T-P2-09)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import numpy as np
import pytest

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


def _bars(
    prices: np.ndarray, highs: np.ndarray | None = None, lows: np.ndarray | None = None
) -> list[Candle]:
    highs = prices if highs is None else highs
    lows = prices if lows is None else lows
    return [
        Candle(
            instrument_id=_INSTRUMENT_ID,
            interval="1m",
            open_time=_BASE_TS + timedelta(minutes=i),
            open=Decimal(repr(float(prices[i]))),
            high=Decimal(repr(float(highs[i]))),
            low=Decimal(repr(float(lows[i]))),
            close=Decimal(repr(float(prices[i]))),
            volume=Decimal("10"),
            is_closed=True,
        )
        for i in range(len(prices))
    ]


def _price_series(seed: int, n: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (np.cumsum(rng.normal(0, 1, n)) + 100.0).astype(np.float64)


# --- acceptance criterion 1: incremental SMA(20) == vectorized SMA(20) at every step -


def test_incremental_sma_20_and_vectorized_sma_20_produce_identical_values_at_every_step() -> (
    None
):
    """TASKS.md T-P2-09 acceptance criterion, verbatim: "An incremental
    SMA(20) and vectorized SMA(20) fed the same 100 bars produce
    identical values at every step.\""""
    prices = _price_series(seed=1, n=100)
    bars = _bars(prices)
    reference = sma(prices, 20)

    indicator = IncrementalSMA(20)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        if np.isnan(reference[i]):
            assert result is None
        else:
            assert result == Decimal(repr(float(reference[i])))


# --- acceptance criterion 2: incremental EMA == vectorized EMA at every bar ---------


def test_incremental_ema_produces_the_same_values_as_vectorized_at_every_bar_after_warmup() -> (
    None
):
    """TASKS.md T-P2-09 acceptance criterion, verbatim: "An incremental
    EMA produces the same values as the vectorized variant at every bar
    after warmup." EMA's own warmup is zero bars (T-P2-08): every bar
    is "after warmup.\""""
    prices = _price_series(seed=2, n=100)
    bars = _bars(prices)
    reference = ema(prices, span=10)

    indicator = IncrementalEMA(span=10)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        assert result is not None
        assert result == Decimal(repr(float(reference[i])))


# --- acceptance criterion 3: update() one bar at a time on 1M bars in < 30 seconds --


def test_update_one_bar_at_a_time_on_1_million_bars_completes_in_under_30_seconds() -> None:
    """TASKS.md T-P2-09 acceptance criterion, verbatim: "Calling
    `update()` one bar at a time on 1M bars completes in < 30 seconds
    (single-threaded performance).\""""
    n = 1_000_000
    prices = _price_series(seed=3, n=n)
    highs = prices + 1.0
    lows = prices - 1.0
    bars = _bars(prices, highs, lows)

    indicators: dict[str, object] = {
        "sma": IncrementalSMA(20),
        "ema": IncrementalEMA(10),
        "rsi": IncrementalRSI(14),
        "atr": IncrementalATR(14),
        "bollinger_bands": IncrementalBollingerBands(20, 2.0),
        "macd": IncrementalMACD(),
        "rolling_volatility": IncrementalRollingVolatility(20),
    }
    for name, indicator in indicators.items():
        start = time.perf_counter()
        for bar in bars:
            indicator.update(bar)  # type: ignore[attr-defined]
        elapsed = time.perf_counter() - start
        assert elapsed < 30.0, f"{name} took {elapsed:.3f}s for {n} bars"


# --- acceptance criterion 4: None for first n-1 bars, then Decimal thereafter ------


def test_incremental_sma_returns_none_for_first_n_minus_1_bars_then_decimal() -> None:
    """TASKS.md T-P2-09 acceptance criterion, verbatim: "Returns `None`
    for the first `n-1` bars, then a `Decimal` thereafter.\""""
    bars = _bars(_price_series(seed=4, n=30))
    indicator = IncrementalSMA(20)

    results = [indicator.update(bar) for bar in bars]

    assert all(r is None for r in results[:19])
    assert all(isinstance(r, Decimal) for r in results[19:])


def test_incremental_rsi_returns_none_for_first_n_minus_1_bars_then_decimal() -> None:
    bars = _bars(_price_series(seed=5, n=30))
    indicator = IncrementalRSI(14)

    results = [indicator.update(bar) for bar in bars]

    assert all(r is None for r in results[:13])
    assert all(isinstance(r, Decimal) for r in results[13:])


def test_incremental_atr_returns_none_for_first_n_minus_1_bars_then_decimal() -> None:
    prices = _price_series(seed=6, n=30)
    bars = _bars(prices, prices + 1.0, prices - 1.0)
    indicator = IncrementalATR(14)

    results = [indicator.update(bar) for bar in bars]

    assert all(r is None for r in results[:13])
    assert all(isinstance(r, Decimal) for r in results[13:])


def test_incremental_bollinger_bands_returns_none_for_first_n_minus_1_bars_then_value() -> None:
    bars = _bars(_price_series(seed=7, n=30))
    indicator = IncrementalBollingerBands(20, 2.0)

    results = [indicator.update(bar) for bar in bars]

    assert all(r is None for r in results[:19])
    assert all(r is not None for r in results[19:])


def test_incremental_rolling_volatility_returns_none_for_first_n_minus_1_bars_then_decimal() -> (
    None
):
    bars = _bars(_price_series(seed=8, n=30))
    indicator = IncrementalRollingVolatility(20)

    results = [indicator.update(bar) for bar in bars]

    assert all(r is None for r in results[:19])
    assert all(isinstance(r, Decimal) for r in results[19:])


def test_incremental_ema_never_returns_none() -> None:
    bars = _bars(_price_series(seed=9, n=30))
    indicator = IncrementalEMA(10)

    results = [indicator.update(bar) for bar in bars]

    assert all(isinstance(r, Decimal) for r in results)


def test_incremental_macd_never_returns_none() -> None:
    bars = _bars(_price_series(seed=10, n=30))
    indicator = IncrementalMACD()

    results = [indicator.update(bar) for bar in bars]

    assert all(r is not None for r in results)


# --- structural sanity: full-run equivalence for the remaining indicator types -----


def test_incremental_rsi_matches_vectorized_rsi_across_a_full_run() -> None:
    prices = _price_series(seed=11, n=100)
    bars = _bars(prices)
    reference = rsi(prices, 14)

    indicator = IncrementalRSI(14)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        if np.isnan(reference[i]):
            assert result is None
        else:
            assert result == Decimal(repr(float(reference[i])))


def test_incremental_atr_matches_vectorized_atr_across_a_full_run() -> None:
    prices = _price_series(seed=12, n=100)
    highs, lows = prices + 1.5, prices - 1.5
    bars = _bars(prices, highs, lows)
    reference = atr(highs, lows, prices, 14)

    indicator = IncrementalATR(14)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        if np.isnan(reference[i]):
            assert result is None
        else:
            assert result == Decimal(repr(float(reference[i])))


def test_incremental_bollinger_bands_matches_vectorized_across_a_full_run() -> None:
    prices = _price_series(seed=13, n=100)
    bars = _bars(prices)
    reference = bollinger_bands(prices, 20, 2.0)

    indicator = IncrementalBollingerBands(20, 2.0)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        if np.isnan(reference.middle[i]):
            assert result is None
        else:
            assert result is not None
            assert result.middle == Decimal(repr(float(reference.middle[i])))
            assert result.upper == Decimal(repr(float(reference.upper[i])))
            assert result.lower == Decimal(repr(float(reference.lower[i])))


def test_incremental_macd_matches_vectorized_across_a_full_run() -> None:
    prices = _price_series(seed=14, n=100)
    bars = _bars(prices)
    reference = macd(prices)

    indicator = IncrementalMACD()
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        assert result.macd == Decimal(repr(float(reference.macd[i])))
        assert result.signal == Decimal(repr(float(reference.signal[i])))


def test_incremental_rolling_volatility_matches_vectorized_across_a_full_run() -> None:
    prices = _price_series(seed=15, n=100)
    bars = _bars(prices)
    reference = rolling_volatility(prices, 20)

    indicator = IncrementalRollingVolatility(20)
    for i, bar in enumerate(bars):
        result = indicator.update(bar)
        if np.isnan(reference[i]):
            assert result is None
        else:
            assert result == Decimal(repr(float(reference[i])))


# --- construction validation --------------------------------------------------------


def test_incremental_sma_rejects_a_non_positive_period() -> None:
    with pytest.raises(ValueError, match="period"):
        IncrementalSMA(0)


def test_incremental_bollinger_bands_rejects_a_period_below_2() -> None:
    with pytest.raises(ValueError, match="period"):
        IncrementalBollingerBands(period=1)


def test_incremental_rolling_volatility_rejects_a_period_below_2() -> None:
    with pytest.raises(ValueError, match="period"):
        IncrementalRollingVolatility(period=1)


def test_incremental_macd_rejects_a_fast_span_not_less_than_slow_span() -> None:
    with pytest.raises(ValueError, match="fast_span"):
        IncrementalMACD(fast_span=26, slow_span=12)
