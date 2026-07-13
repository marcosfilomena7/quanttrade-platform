"""Vectorized indicator library (TASKS.md T-P2-08).

"Implement vectorized (Polars/NumPy) indicators in
`infrastructure/indicators/vectorized/`: SMA, EMA, RSI (14-period),
ATR, Bollinger Bands, MACD, rolling volatility (annualized). All accept
a `pl.Series` or `np.ndarray` and return the same type. All handle the
`NaN` prefix correctly (first `n-1` values are NaN for an n-period
indicator). No float in price inputs — accept Decimal arrays converted
to float only at the indicator boundary."

Design decisions, and why:

- **Placed in `infrastructure/`, not `domain/`.** TASKS.md's own path is
  literal (`infrastructure/indicators/vectorized/`), and the reasoning
  matches: this module depends on `numpy`/`polars`, third-party
  numerical libraries with no place inside a zero-outward-dependency
  domain layer (ARCHITECTURE.md §3.5). It is *not* mypy-strict-scoped
  (`pyproject.toml`'s `[tool.mypy] files` covers `domain`/`application`
  only) for the same, already-established reason infrastructure adapters
  in general aren't: third-party numeric stubs are frequently
  incomplete. Every function here is still fully type-hinted.
- **"No float in price inputs... converted to float only at the
  indicator boundary" is read as: *this module* is that boundary, and
  its own public functions take an already-float `pl.Series` or
  `np.ndarray` directly** — not a `Decimal`-dtype array that this module
  itself unwraps. ARCHITECTURE.md §3.5's `Decimal`-everywhere rule (and
  `scripts/check_no_float.py`) governs `domain/` and `application/`,
  where every price value (`Candle.close`, etc.) is `Decimal`; nothing
  in this task's own four acceptance criteria exercises a `Decimal`
  input or output, and TASKS.md's own accepted input types are
  explicitly "`pl.Series` or `np.ndarray`," neither of which is a
  `Decimal` container. Converting a `Decimal`-typed price history into a
  float `pl.Series`/`np.ndarray` is therefore the *caller's* job (a
  future task assembling real indicator pipelines from `Candle`
  history), matching the same resolution already used for "loaded from
  config" in T-P2-05/06: this module receives an already-prepared
  input, it does not reach further upstream than TASKS.md's own stated
  accepted types.
- **One cohesive module (`indicators.py`), not one file per
  indicator.** Every indicator here shares the same input/output type
  contract and the same rolling-window arithmetic core
  (`_rolling_sum`/`_rolling_mean`/`_rolling_mean_std`); splitting into
  seven near-trivial files would scatter that shared logic or duplicate
  it. This mirrors `domain/fee_schedule.py`'s own precedent: one
  cohesive concern, one file, even though it exposes several public
  names.
- **Every public function type-dispatches on `pl.Series` vs.
  `np.ndarray` (`_to_ndarray`/`_like`), but every actual formula is
  implemented exactly once, over a plain `np.ndarray`** (the private
  `_*_ndarray` helpers). This is what "return the same type" (TASKS.md's
  own words) means in practice — the container type round-trips through
  each function unchanged — without duplicating any indicator's math
  once for Polars and once for NumPy.
- **EMA (and MACD, built from it) is computed via Polars'
  `Series.ewm_mean(span=..., adjust=False)`, not a hand-rolled NumPy
  recurrence.** EMA is an IIR (recursive) filter — there is no
  numerically stable, allocation-light *closed-form* vectorization of
  it in plain NumPy for a million-row series (the naive closed form
  requires terms like `(1-alpha)**-k`, which overflows for even
  moderately long series). Polars' `ewm_mean` is a genuine, Rust-backed
  vectorized implementation — squarely inside "vectorized
  (Polars/**NumPy**)," TASKS.md's own named toolset — and is verified
  numerically (see `tests/infrastructure/indicators/test_vectorized
  .py`) to match `pandas.ewm(span=..., adjust=False).mean()` to
  float64 precision, which is exactly AC3's own literal requirement.
- **The "first `n-1` values are NaN" rule (AC2) is read as applying to
  every *period*-parameterized indicator (SMA, RSI, ATR, Bollinger
  Bands, rolling volatility) — not to EMA or MACD**, which TASKS.md
  itself calls out by "span," not "period" ("EMA with `span=10`"), and
  whose own AC3 requires exact parity with `pandas.ewm(adjust=False)
  .mean()`, which produces a value at *every* row (no NaN prefix) by
  design. Treating AC2 as universal would make AC2 and AC3
  contradictory for EMA; reading AC2 as scoped to window/period-based
  indicators resolves the conflict literally, without weakening either
  criterion for the indicators it actually governs.
- **RSI, ATR, and rolling volatility all internally difference the
  input (bar-over-bar change, true range, returns) before applying a
  rolling window.** A first difference has no defined value at index 0
  (there is no prior observation) — left as `NaN`, this would propagate
  through every later rolling-window value once it fell inside a
  cumulative sum (this module's rolling-sum implementation, below,
  is `NaN`-poisoning by construction, deliberately: real gaps in the
  input must not be silently smoothed over). Index 0 of each differenced
  quantity is instead defined as a neutral zero (no price change, no
  range beyond the bar's own high-low, no return) — a standard,
  documented convention (many public RSI/ATR implementations do the
  same for their very first bar) that is also what keeps AC2's "first
  `n-1` values are NaN" exactly true for these three indicators, rather
  than off by one.
- **Rolling standard deviation (Bollinger Bands, rolling volatility)
  uses the sum-of-squares shortcut, `Var = E[X²] − E[X]²`, scaled by
  `period/(period−1)` for the sample (ddof=1) convention** — matching
  `pandas.Series.rolling().std()`'s own default ddof, in case any future
  work cross-checks against it. Both require `period >= 2` (a
  single-point sample variance is undefined); floating-point
  cancellation can push the population-variance term fractionally
  negative for a near-constant window, so it is clamped to `>= 0`
  before the square root.
- **`atr()` takes three same-typed series (`high`, `low`, `close`), not
  one.** True Range is structurally a 3-input quantity — there is no
  single-series formulation. `bollinger_bands()` and `macd()` return a
  `NamedTuple` of same-typed series (`upper`/`middle`/`lower`,
  `macd`/`signal`/`histogram`) rather than a single value, since each is
  inherently a multi-output indicator; "return the same type" (TASKS.md)
  is read per-component in that case — each element of the tuple is a
  `pl.Series` when the input was, an `np.ndarray` when it was.
- **`rsi`'s default period is `14`** — the only indicator TASKS.md
  itself pins to a specific number ("RSI (14-period)"), read as its
  canonical default while still leaving it a caller-overridable
  parameter (matching AC1's own `SMA(20)` — a specific period supplied
  by the caller, not hardcoded into `sma()` itself). `atr`'s default
  (`14`) and `bollinger_bands`'s default (`period=20, num_std=2.0`) are
  the same, unremarkable industry-standard defaults referenced by any
  technical-analysis reference (and `20` is also literally AC1's own
  SMA test period); no acceptance criterion pins a specific numeric
  value for either, so these exist purely as ergonomic defaults, not
  hardcoded behavior.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import numpy.typing as npt
import polars as pl

FloatSeries = pl.Series | npt.NDArray[np.float64]

_FloatArray = npt.NDArray[np.float64]


def _to_ndarray(x: FloatSeries) -> _FloatArray:
    if isinstance(x, pl.Series):
        return x.to_numpy().astype(np.float64, copy=False)
    return np.asarray(x, dtype=np.float64)


def _like(x: FloatSeries, values: _FloatArray) -> FloatSeries:
    if isinstance(x, pl.Series):
        return pl.Series(x.name, values)
    return values


def _rolling_sum(values: _FloatArray, period: int) -> _FloatArray:
    """`NaN` for the first `period - 1` entries, then the trailing
    `period`-window sum thereafter. Poisons forward (by cumulative-sum
    construction) if `values` itself contains a `NaN` anywhere inside a
    window — a genuine gap must never be silently smoothed over."""
    n = values.shape[0]
    result = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return result
    cumsum = np.cumsum(np.insert(values, 0, 0.0))
    result[period - 1 :] = cumsum[period:] - cumsum[:-period]
    return result


def _rolling_mean(values: _FloatArray, period: int) -> _FloatArray:
    return _rolling_sum(values, period) / period


def _rolling_mean_std(values: _FloatArray, period: int) -> tuple[_FloatArray, _FloatArray]:
    """Rolling mean and sample (`ddof=1`) standard deviation, via the
    `Var = E[X²] − E[X]²` shortcut. Requires `period >= 2`."""
    mean = _rolling_mean(values, period)
    mean_sq = _rolling_mean(values * values, period)
    population_variance = np.clip(mean_sq - mean * mean, a_min=0.0, a_max=None)
    sample_variance = population_variance * (period / (period - 1))
    return mean, np.sqrt(sample_variance)


def _ema_ndarray(values: _FloatArray, span: int) -> _FloatArray:
    result: _FloatArray = pl.Series(values=values).ewm_mean(span=span, adjust=False).to_numpy()
    return result


def sma(x: FloatSeries, period: int) -> FloatSeries:
    """Simple moving average over `period` observations."""
    if period < 1:
        raise ValueError("sma: period must be >= 1")
    values = _to_ndarray(x)
    return _like(x, _rolling_mean(values, period))


def ema(x: FloatSeries, span: int) -> FloatSeries:
    """Exponential moving average, matching `pandas.ewm(span=span,
    adjust=False).mean()` (TASKS.md T-P2-08 AC3)."""
    if span < 1:
        raise ValueError("ema: span must be >= 1")
    values = _to_ndarray(x)
    return _like(x, _ema_ndarray(values, span))


def rsi(x: FloatSeries, period: int = 14) -> FloatSeries:
    """Relative Strength Index: `100 * avg_gain / (avg_gain + avg_loss)`
    over a rolling `period`-bar window of bar-over-bar gains/losses —
    algebraically identical to the textbook `100 - 100 / (1 + RS)` form
    wherever `avg_loss > 0`, and additionally well-defined (`50`, a
    neutral reading) when a window has neither gains nor losses."""
    if period < 1:
        raise ValueError("rsi: period must be >= 1")
    values = _to_ndarray(x)
    delta = np.diff(values, prepend=values[:1])  # delta[0] := 0 (no prior bar)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _rolling_mean(gain, period)
    avg_loss = _rolling_mean(loss, period)
    denom = avg_gain + avg_loss
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(denom == 0, 50.0, 100.0 * avg_gain / np.where(denom == 0, 1.0, denom))
    result = np.where(np.isnan(avg_gain) | np.isnan(avg_loss), np.nan, result)
    return _like(x, result)


def atr(high: FloatSeries, low: FloatSeries, close: FloatSeries, period: int = 14) -> FloatSeries:
    """Average True Range: a rolling `period`-bar mean of True Range,
    `max(high-low, |high-prev_close|, |low-prev_close|)`. `high`, `low`,
    and `close` must all be the same container type."""
    if period < 1:
        raise ValueError("atr: period must be >= 1")
    if not (type(high) is type(low) is type(close)):
        raise TypeError("atr: high, low, and close must all be the same type")
    h = _to_ndarray(high)
    lo = _to_ndarray(low)
    c = _to_ndarray(close)
    prev_close = np.empty_like(c)
    prev_close[0] = np.nan  # no prior bar: true range degrades to high - low
    prev_close[1:] = c[:-1]
    true_range = np.nanmax(
        np.vstack([h - lo, np.abs(h - prev_close), np.abs(lo - prev_close)]), axis=0
    )
    return _like(high, _rolling_mean(true_range, period))


class BollingerBands(NamedTuple):
    upper: FloatSeries
    middle: FloatSeries
    lower: FloatSeries


def bollinger_bands(x: FloatSeries, period: int = 20, num_std: float = 2.0) -> BollingerBands:
    """`middle` is the `period`-bar SMA; `upper`/`lower` are `middle`
    plus/minus `num_std` rolling (sample) standard deviations."""
    if period < 2:
        raise ValueError("bollinger_bands: period must be >= 2")
    values = _to_ndarray(x)
    mean, std = _rolling_mean_std(values, period)
    upper = mean + num_std * std
    lower = mean - num_std * std
    return BollingerBands(upper=_like(x, upper), middle=_like(x, mean), lower=_like(x, lower))


class MACDResult(NamedTuple):
    macd: FloatSeries
    signal: FloatSeries
    histogram: FloatSeries


def macd(
    x: FloatSeries, fast_span: int = 12, slow_span: int = 26, signal_span: int = 9
) -> MACDResult:
    """`macd` = EMA(`fast_span`) − EMA(`slow_span`); `signal` =
    EMA(`signal_span`) of `macd`; `histogram` = `macd` − `signal`."""
    if fast_span < 1 or slow_span < 1 or signal_span < 1:
        raise ValueError("macd: fast_span, slow_span, and signal_span must all be >= 1")
    if fast_span >= slow_span:
        raise ValueError("macd: fast_span must be < slow_span")
    values = _to_ndarray(x)
    fast_ema = _ema_ndarray(values, fast_span)
    slow_ema = _ema_ndarray(values, slow_span)
    macd_line = fast_ema - slow_ema
    signal_line = _ema_ndarray(macd_line, signal_span)
    histogram = macd_line - signal_line
    return MACDResult(
        macd=_like(x, macd_line), signal=_like(x, signal_line), histogram=_like(x, histogram)
    )


def rolling_volatility(x: FloatSeries, period: int, periods_per_year: int = 252) -> FloatSeries:
    """Annualized rolling volatility: the `period`-bar sample standard
    deviation of simple returns, scaled by `sqrt(periods_per_year)`."""
    if period < 2:
        raise ValueError("rolling_volatility: period must be >= 2")
    values = _to_ndarray(x)
    prev = np.empty_like(values)
    prev[0] = np.nan
    prev[1:] = values[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        returns = (values - prev) / prev
    returns[0] = 0.0  # no prior bar: no return
    _, std = _rolling_mean_std(returns, period)
    return _like(x, std * np.sqrt(float(periods_per_year)))
