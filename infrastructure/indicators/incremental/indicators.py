"""Incremental (streaming) indicator library (TASKS.md T-P2-09).

"Implement streaming/incremental versions of every indicator from
T-P2-08 in `infrastructure/indicators/incremental/`. Each maintains
internal state and exposes `update(bar: Candle) -> Decimal | None`.
Returns `None` during warmup. Must produce numerically equivalent
results to the vectorized variant given the same bar sequence."

Design decisions, and why:

- **Every indicator's own recurrence is chosen to be bit-for-bit
  identical to its T-P2-08 vectorized counterpart, not merely
  "numerically close."** T-P2-08's `sma`/`rsi`/`atr`/`bollinger_bands`
  /`rolling_volatility` are computed via a cumulative-sum-then-subtract
  formula (`_rolling_sum`); a naive incremental "add the new value,
  subtract the oldest" running sum accumulates floating-point rounding
  error along a *different* path and measurably diverges (~1e-13,
  confirmed empirically while designing this module) from that formula.
  Instead, `_RunningWindowSum` (below) replays the *exact same*
  arithmetic — a running cumulative total plus a bounded history of its
  own value `period` bars back, subtracted the same way — so every
  rolling sum here is the identical sequence of floating-point
  operations `_rolling_sum` performs, just computed one bar at a time.
  Likewise, `ema`/`macd`'s Polars-backed `ewm_mean(adjust=False)` turned
  out (verified empirically) to compute `y = y + alpha*(x - y)`, not
  the textbook `y = alpha*x + (1-alpha)*y` — mathematically identical,
  but a different floating-point operation order with a different
  rounding result; `_ema_step` uses the former, confirmed bit-identical
  to `ema()`/`macd()` for the same input sequence. This is what makes
  AC1 ("identical values at every step") and AC2 ("the same values...
  at every bar after warmup") literally, exactly true, not merely true
  up to a tolerance — and sets up T-P2-10's future equivalence harness
  (a separate, later task) to actually pass.
- **The vectorized functions themselves cannot be reused directly** —
  they operate on a whole, already-known array; recomputing one from
  scratch on every incoming bar would be `O(bars²)` over a streaming
  1M-bar run, far outside AC3's 30-second budget. What *is* reused,
  verbatim, is each vectorized function's exact arithmetic (see above) —
  the only form of "no dupliques lógica" that a streaming computation
  and a batch computation can actually share.
- **`update()`'s input is a domain `Candle` (T-P0-07); its output is a
  `Decimal` (or `None` during warmup) — never a raw `float`.** This
  module is the same "indicator boundary" T-P2-08's own docstring
  describes: `Candle.close`/`.high`/`.low` (`Decimal`) are converted to
  `float` once, on the way in, for the actual arithmetic (`numpy`/
  `polars`-free here, since each `update()` call is O(1) and a
  dependency on those libraries would add nothing); the result is
  converted back to `Decimal` once, on the way out, via `Decimal(repr(
  value))` — the canonical, shortest round-tripping decimal string for
  a given `float64`, so bit-identical floats always produce identical
  `Decimal`s.
- **"Returns `None` during warmup... first `n-1` bars" (the task's own
  general description and its own last acceptance criterion) is read,
  exactly as in T-P2-08, as applying to every *period*-parameterized
  indicator (SMA, RSI, ATR, Bollinger Bands, rolling volatility) — not
  to EMA or MACD.** `ema()`/`macd()` (T-P2-08) have no NaN prefix by
  construction (verified: `pandas.ewm(adjust=False)` produces a value
  at every row); `IncrementalEMA.update()`/`IncrementalMACD.update()`
  therefore never return `None` — they hand back a value from the very
  first bar, exactly mirroring their vectorized counterparts having a
  warmup of zero bars. AC2's own phrase "at every bar after warmup" is
  consistent with this: for EMA, "after warmup" is every bar, since its
  warmup is empty.
- **`IncrementalBollingerBands.update()`/`IncrementalMACD.update()`
  return a `NamedTuple` (`BollingerBandsValue`/`MACDValue`) instead of a
  single `Decimal`**, for the same structural reason T-P2-08's
  `bollinger_bands()`/`macd()` return a `NamedTuple` of series: neither
  indicator has a single-value formulation. `update()`'s literal
  `Decimal | None` signature is the shape every *single-output*
  indicator satisfies (SMA, EMA, RSI, ATR, rolling volatility); for the
  two multi-output indicators, "a `Decimal | None`" is read per-field of
  their own result tuple (`None` collectively — all three fields are or
  aren't ready together, since they share the same rolling window) —
  the direct incremental analogue of T-P2-08's own "return the same
  type" being read per-component for its multi-output indicators.
- **One cohesive module (`indicators.py`), not one file per
  indicator**, and **class names are `Incremental`-prefixed** (
  `IncrementalSMA`, `IncrementalEMA`, ...) rather than reusing T-P2-08's
  own lowercase function names — both decisions mirror
  `infrastructure/indicators/vectorized/indicators.py`'s own structure
  and naming precedent, while keeping the two packages' public names
  distinguishable when imported side by side (as T-P2-10's future
  equivalence harness will need to).
- **`_RunningWindowSum` and `_ema_step` are the only two computational
  primitives**, reused across all seven indicators (`_RunningWindowSum`
  by SMA directly, twice each by RSI/ATR/Bollinger/rolling-volatility
  for their paired gain-loss/true-range/value-and-value² rolling sums;
  `_ema_step` by EMA once and MACD three times for its fast/slow/signal
  lines) — matching T-P2-08's own `_rolling_sum`/`_rolling_mean`/
  `_ema_ndarray` reuse pattern, and this task's own "no dupliques
  lógica" requirement.
- **No `state()`/`restore()` serialization on these classes.**
  DATABASE.md's own Group notes on `StrategyState` ("indicator snapshot
  for instant restart") explicitly defer this: "MVP restart recovers via
  warmup replay from Candle history; revisit only if warmup time becomes
  an operational problem." Neither T-P2-09's description nor any of its
  four acceptance criteria asks for indicator-level snapshotting — only
  "maintains internal state" (an implementation detail, satisfied by
  each class's own private instance attributes), so none is added.
"""

from __future__ import annotations

import math
from collections import deque
from decimal import Decimal
from typing import NamedTuple

from domain.candle import Candle


def _to_decimal(value: float) -> Decimal:
    """The canonical, shortest round-tripping decimal string for a
    given float64 — bit-identical floats always produce identical
    `Decimal`s."""
    return Decimal(repr(value))


class _RunningWindowSum:
    """A `period`-bar rolling sum, computed via a running cumulative
    total plus a bounded history of its own value — the exact same
    arithmetic `infrastructure/indicators/vectorized/indicators.py`'s
    `_rolling_sum` performs (cumulative-sum-then-subtract), replayed one
    bar at a time so the two are bit-identical, not merely close.
    `push()` returns `None` until `period` values have been pushed.
    """

    __slots__ = ("_period", "_cumsum", "_history")

    def __init__(self, period: int) -> None:
        self._period = period
        self._cumsum = 0.0
        self._history: deque[float] = deque([0.0], maxlen=period + 1)

    def push(self, value: float) -> float | None:
        self._cumsum += value
        self._history.append(self._cumsum)
        if len(self._history) == self._period + 1:
            return self._history[-1] - self._history[0]
        return None


def _ema_step(previous: float | None, value: float, span: int) -> float:
    """One step of `y = y + alpha*(x - y)`, `alpha = 2/(span+1)` —
    verified bit-identical to Polars' `Series.ewm_mean(span=span,
    adjust=False)`, which `infrastructure/indicators/vectorized
    /indicators.py`'s `ema()`/`macd()` are built on."""
    if previous is None:
        return value
    alpha = 2.0 / (span + 1)
    return previous + alpha * (value - previous)


class IncrementalSMA:
    """Streaming simple moving average over `period` bars."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("IncrementalSMA: period must be >= 1")
        self._period = period
        self._sum = _RunningWindowSum(period)

    def update(self, bar: Candle) -> Decimal | None:
        total = self._sum.push(float(bar.close))
        if total is None:
            return None
        return _to_decimal(total / self._period)


class IncrementalEMA:
    """Streaming exponential moving average. Never returns `None` — an
    EMA is defined from the first bar (see this module's docstring)."""

    def __init__(self, span: int) -> None:
        if span < 1:
            raise ValueError("IncrementalEMA: span must be >= 1")
        self._span = span
        self._value: float | None = None

    def update(self, bar: Candle) -> Decimal:
        self._value = _ema_step(self._value, float(bar.close), self._span)
        return _to_decimal(self._value)


class IncrementalRSI:
    """Streaming Relative Strength Index over `period` bars (default
    14, matching TASKS.md's own "RSI (14-period)")."""

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("IncrementalRSI: period must be >= 1")
        self._period = period
        self._gain_sum = _RunningWindowSum(period)
        self._loss_sum = _RunningWindowSum(period)
        self._prev_close: float | None = None

    def update(self, bar: Candle) -> Decimal | None:
        close = float(bar.close)
        previous = self._prev_close if self._prev_close is not None else close
        delta = close - previous
        self._prev_close = close
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        gain_total = self._gain_sum.push(gain)
        loss_total = self._loss_sum.push(loss)
        if gain_total is None or loss_total is None:
            return None
        avg_gain = gain_total / self._period
        avg_loss = loss_total / self._period
        denom = avg_gain + avg_loss
        result = 50.0 if denom == 0 else 100.0 * avg_gain / denom
        return _to_decimal(result)


class IncrementalATR:
    """Streaming Average True Range over `period` bars (default 14)."""

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("IncrementalATR: period must be >= 1")
        self._period = period
        self._tr_sum = _RunningWindowSum(period)
        self._prev_close: float | None = None

    def update(self, bar: Candle) -> Decimal | None:
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        if self._prev_close is None:
            true_range = high - low
        else:
            true_range = max(
                high - low, abs(high - self._prev_close), abs(low - self._prev_close)
            )
        self._prev_close = close
        total = self._tr_sum.push(true_range)
        if total is None:
            return None
        return _to_decimal(total / self._period)


class BollingerBandsValue(NamedTuple):
    upper: Decimal
    middle: Decimal
    lower: Decimal


class IncrementalBollingerBands:
    """Streaming Bollinger Bands: `period`-bar SMA (default 20) plus/
    minus `num_std` (default 2.0) rolling sample standard deviations."""

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        if period < 2:
            raise ValueError("IncrementalBollingerBands: period must be >= 2")
        self._period = period
        self._num_std = num_std
        self._value_sum = _RunningWindowSum(period)
        self._sq_sum = _RunningWindowSum(period)

    def update(self, bar: Candle) -> BollingerBandsValue | None:
        close = float(bar.close)
        value_total = self._value_sum.push(close)
        sq_total = self._sq_sum.push(close * close)
        if value_total is None or sq_total is None:
            return None
        mean = value_total / self._period
        mean_sq = sq_total / self._period
        population_variance = max(mean_sq - mean * mean, 0.0)
        sample_variance = population_variance * (self._period / (self._period - 1))
        std = math.sqrt(sample_variance)
        upper = mean + self._num_std * std
        lower = mean - self._num_std * std
        return BollingerBandsValue(
            upper=_to_decimal(upper), middle=_to_decimal(mean), lower=_to_decimal(lower)
        )


class MACDValue(NamedTuple):
    macd: Decimal
    signal: Decimal
    histogram: Decimal


class IncrementalMACD:
    """Streaming MACD. Never returns `None` — built entirely from EMAs,
    each defined from the first bar (see this module's docstring)."""

    def __init__(self, fast_span: int = 12, slow_span: int = 26, signal_span: int = 9) -> None:
        if fast_span < 1 or slow_span < 1 or signal_span < 1:
            raise ValueError(
                "IncrementalMACD: fast_span, slow_span, and signal_span must all be >= 1"
            )
        if fast_span >= slow_span:
            raise ValueError("IncrementalMACD: fast_span must be < slow_span")
        self._fast_span = fast_span
        self._slow_span = slow_span
        self._signal_span = signal_span
        self._fast: float | None = None
        self._slow: float | None = None
        self._signal: float | None = None

    def update(self, bar: Candle) -> MACDValue:
        close = float(bar.close)
        self._fast = _ema_step(self._fast, close, self._fast_span)
        self._slow = _ema_step(self._slow, close, self._slow_span)
        macd_line = self._fast - self._slow
        self._signal = _ema_step(self._signal, macd_line, self._signal_span)
        histogram = macd_line - self._signal
        return MACDValue(
            macd=_to_decimal(macd_line),
            signal=_to_decimal(self._signal),
            histogram=_to_decimal(histogram),
        )


class IncrementalRollingVolatility:
    """Streaming annualized rolling volatility: the `period`-bar sample
    standard deviation of simple returns, scaled by
    `sqrt(periods_per_year)`."""

    def __init__(self, period: int, periods_per_year: int = 252) -> None:
        if period < 2:
            raise ValueError("IncrementalRollingVolatility: period must be >= 2")
        self._period = period
        self._periods_per_year = periods_per_year
        self._value_sum = _RunningWindowSum(period)
        self._sq_sum = _RunningWindowSum(period)
        self._prev_close: float | None = None

    def update(self, bar: Candle) -> Decimal | None:
        close = float(bar.close)
        ret = 0.0 if self._prev_close is None else (close - self._prev_close) / self._prev_close
        self._prev_close = close
        value_total = self._value_sum.push(ret)
        sq_total = self._sq_sum.push(ret * ret)
        if value_total is None or sq_total is None:
            return None
        mean = value_total / self._period
        mean_sq = sq_total / self._period
        population_variance = max(mean_sq - mean * mean, 0.0)
        sample_variance = population_variance * (self._period / (self._period - 1))
        std = math.sqrt(sample_variance)
        return _to_decimal(std * math.sqrt(float(self._periods_per_year)))
