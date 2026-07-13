"""Incremental (streaming) indicator library (TASKS.md T-P2-09).

`IncrementalSMA`, `IncrementalEMA`, `IncrementalRSI`, `IncrementalATR`,
`IncrementalBollingerBands`, `IncrementalMACD`, and
`IncrementalRollingVolatility` each maintain their own internal state
and expose `update(bar: Candle) -> Decimal | None` (or, for the two
multi-output indicators, a `NamedTuple` of `Decimal | None` fields) —
see `indicators.py`'s own module docstring for the full set of design
decisions, in particular why every recurrence here is bit-identical to
its `infrastructure.indicators.vectorized` counterpart.
"""

from __future__ import annotations

from infrastructure.indicators.incremental.indicators import (
    BollingerBandsValue,
    IncrementalATR,
    IncrementalBollingerBands,
    IncrementalEMA,
    IncrementalMACD,
    IncrementalRollingVolatility,
    IncrementalRSI,
    IncrementalSMA,
    MACDValue,
)

__all__ = [
    "BollingerBandsValue",
    "IncrementalATR",
    "IncrementalBollingerBands",
    "IncrementalEMA",
    "IncrementalMACD",
    "IncrementalRollingVolatility",
    "IncrementalRSI",
    "IncrementalSMA",
    "MACDValue",
]
