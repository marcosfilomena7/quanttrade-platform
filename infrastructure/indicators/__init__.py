"""Indicator library — one subpackage per computation style.

ARCHITECTURE.md S-05: "Indicator library (vectorized + streaming/
incremental variants)." `vectorized/` (TASKS.md T-P2-08) holds the
batch, whole-series implementations built for backtest research over
historical candle data. `incremental/` (TASKS.md T-P2-09, a later,
separate task) will hold the streaming, one-bar-at-a-time counterparts
used in live trading, tested for numeric equivalence against these
(TASKS.md T-P2-10). This package only holds indicator computation —
never anything domain/application depend on directly.
"""
