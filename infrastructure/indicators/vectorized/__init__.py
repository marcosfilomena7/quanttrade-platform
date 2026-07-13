"""Vectorized indicator library (TASKS.md T-P2-08).

`sma`, `ema`, `rsi`, `atr`, `bollinger_bands`, `macd`, and
`rolling_volatility` each accept a `pl.Series` or `np.ndarray` and
return the same container type — see `indicators.py`'s own module
docstring for the full set of design decisions (NaN-prefix semantics,
why EMA is Polars-backed, the Decimal-to-float conversion boundary).
"""

from __future__ import annotations

from infrastructure.indicators.vectorized.indicators import (
    BollingerBands,
    FloatSeries,
    MACDResult,
    atr,
    bollinger_bands,
    ema,
    macd,
    rolling_volatility,
    rsi,
    sma,
)

__all__ = [
    "BollingerBands",
    "FloatSeries",
    "MACDResult",
    "atr",
    "bollinger_bands",
    "ema",
    "macd",
    "rolling_volatility",
    "rsi",
    "sma",
]
