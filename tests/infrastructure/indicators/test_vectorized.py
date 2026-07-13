"""Tests for infrastructure/indicators/vectorized/ (TASKS.md T-P2-08)."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import polars as pl
import pytest

from infrastructure.indicators.vectorized import (
    atr,
    bollinger_bands,
    ema,
    macd,
    rolling_volatility,
    rsi,
    sma,
)

# --- acceptance criterion 1: SMA(20) of [1..100] matches a hand-computed reference --


def test_sma_20_of_1_to_100_matches_hand_computed_reference_at_index_20_and_100() -> None:
    """TASKS.md T-P2-08 acceptance criterion, verbatim: "SMA(20) of
    [1..100] matches a hand-computed reference value at index 20 and
    100.\" Read 1-indexed (as the "[1..100]" values themselves are):
    the 20th value is the mean of 1..20 (10.5); the 100th (last) value
    is the mean of 81..100 (90.5)."""
    x = np.arange(1, 101, dtype=np.float64)

    result = sma(x, 20)

    assert result[19] == pytest.approx(10.5)
    assert result[99] == pytest.approx(90.5)


def test_sma_preserves_the_polars_series_type_and_name() -> None:
    x = pl.Series("close", np.arange(1, 101, dtype=np.float64))

    result = sma(x, 20)

    assert isinstance(result, pl.Series)
    assert result.name == "close"
    assert result[19] == pytest.approx(10.5)


def test_sma_preserves_the_ndarray_type() -> None:
    x = np.arange(1, 101, dtype=np.float64)

    result = sma(x, 20)

    assert isinstance(result, np.ndarray)


def test_sma_rejects_a_non_positive_period() -> None:
    with pytest.raises(ValueError, match="period"):
        sma(np.array([1.0, 2.0, 3.0]), 0)


# --- acceptance criterion 2: first n-1 values of an n-period indicator are NaN -----


def test_sma_first_n_minus_1_values_are_nan() -> None:
    """TASKS.md T-P2-08 acceptance criterion, verbatim: "The first `n-1`
    values of an n-period indicator are `NaN`.\""""
    x = np.arange(1, 101, dtype=np.float64)

    result = sma(x, 20)

    assert np.all(np.isnan(result[:19]))
    assert not np.any(np.isnan(result[19:]))


def test_rsi_first_n_minus_1_values_are_nan() -> None:
    x = np.arange(1, 101, dtype=np.float64)

    result = rsi(x, 14)

    assert np.all(np.isnan(result[:13]))
    assert not np.any(np.isnan(result[13:]))


def test_atr_first_n_minus_1_values_are_nan() -> None:
    x = np.arange(1, 101, dtype=np.float64)
    high, low = x + 1, x - 1

    result = atr(high, low, x, 14)

    assert np.all(np.isnan(result[:13]))
    assert not np.any(np.isnan(result[13:]))


def test_bollinger_bands_first_n_minus_1_values_are_nan_for_all_three_bands() -> None:
    x = np.arange(1, 101, dtype=np.float64)

    bands = bollinger_bands(x, 20, 2.0)

    for series in (bands.upper, bands.middle, bands.lower):
        assert np.all(np.isnan(series[:19]))
        assert not np.any(np.isnan(series[19:]))


def test_rolling_volatility_first_n_minus_1_values_are_nan() -> None:
    rng = np.random.default_rng(1)
    x = np.cumsum(rng.normal(0, 1, 100)) + 100.0

    result = rolling_volatility(x, 20)

    assert np.all(np.isnan(result[:19]))
    assert not np.any(np.isnan(result[19:]))


# --- acceptance criterion 3: EMA(span=10) matches pandas.ewm(adjust=False) ----------


def test_ema_span_10_matches_pandas_ewm_adjust_false_mean() -> None:
    """TASKS.md T-P2-08 acceptance criterion, verbatim: "EMA with
    `span=10` matches `pandas.ewm(span=10, adjust=False).mean()` on
    identical input.\""""
    rng = np.random.default_rng(42)
    x = rng.normal(loc=100, scale=5, size=500).astype(np.float64)

    result = ema(x, span=10)
    reference = pd.Series(x).ewm(span=10, adjust=False).mean().to_numpy()

    np.testing.assert_allclose(result, reference, rtol=0, atol=1e-9)


def test_ema_has_no_nan_prefix() -> None:
    """Unlike period-based indicators, EMA is defined from the first
    bar — matching pandas.ewm(adjust=False), which has no min_periods
    gate by default."""
    x = np.arange(1, 101, dtype=np.float64)

    result = ema(x, span=10)

    assert not np.any(np.isnan(result))


def test_macd_reuses_ema_and_has_no_nan_prefix() -> None:
    x = np.arange(1, 101, dtype=np.float64)

    result = macd(x)

    assert not np.any(np.isnan(result.macd))
    assert not np.any(np.isnan(result.signal))
    np.testing.assert_allclose(result.histogram, result.macd - result.signal)


# --- acceptance criterion 4: all indicators process 1M rows in < 1 second ----------


def test_all_indicators_process_1_million_rows_in_under_1_second() -> None:
    """TASKS.md T-P2-08 acceptance criterion, verbatim: "All indicators
    process 1M rows in < 1 second (vectorized performance
    requirement).\""""
    rng = np.random.default_rng(7)
    n = 1_000_000
    prices = (np.cumsum(rng.normal(0, 1, n)) + 1000.0).astype(np.float64)
    high = prices + rng.uniform(0, 2, n)
    low = prices - rng.uniform(0, 2, n)

    calls: dict[str, object] = {
        "sma": lambda: sma(prices, 20),
        "ema": lambda: ema(prices, 10),
        "rsi": lambda: rsi(prices, 14),
        "atr": lambda: atr(high, low, prices, 14),
        "bollinger_bands": lambda: bollinger_bands(prices, 20, 2.0),
        "macd": lambda: macd(prices),
        "rolling_volatility": lambda: rolling_volatility(prices, 20),
    }
    for name, call in calls.items():
        start = time.perf_counter()
        call()  # type: ignore[operator]
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"{name} took {elapsed:.3f}s for {n} rows"


# --- structural sanity --------------------------------------------------------------


def test_atr_rejects_mismatched_input_types() -> None:
    x = np.array([1.0, 2.0, 3.0])
    s = pl.Series("x", x)
    with pytest.raises(TypeError, match="same type"):
        atr(x, s, x, 2)  # type: ignore[arg-type]


def test_bollinger_bands_rejects_a_period_below_2() -> None:
    with pytest.raises(ValueError, match="period"):
        bollinger_bands(np.array([1.0, 2.0, 3.0]), period=1)


def test_rolling_volatility_rejects_a_period_below_2() -> None:
    with pytest.raises(ValueError, match="period"):
        rolling_volatility(np.array([1.0, 2.0, 3.0]), period=1)


def test_macd_rejects_a_fast_span_not_less_than_slow_span() -> None:
    with pytest.raises(ValueError, match="fast_span"):
        macd(np.arange(1.0, 50.0), fast_span=26, slow_span=12)


def test_rsi_of_a_monotonically_increasing_series_is_100_after_warmup() -> None:
    """An all-gains, no-losses window is the unambiguous RSI edge case."""
    x = np.arange(1, 101, dtype=np.float64)

    result = rsi(x, 14)

    assert np.all(result[13:] == pytest.approx(100.0))
