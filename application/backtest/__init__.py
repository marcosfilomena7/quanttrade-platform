"""Backtest use-case orchestration (ARCHITECTURE.md M13, Phase 2).

`run_backtest` (T-P2-04) is the core simulation loop: the first piece
of code in `application/`. It depends only on domain types and ports
(`Clock`, `MarketDataView`) plus small, locally defined Protocols that
extend those ports with exactly the extra methods this loop needs —
never on `infrastructure/`, per the Dependency Rule. Concrete adapters
(`SimulatedClock`, `CursorMarketDataView`, `HistoricalFeed`, all
T-P2-01/02/03) satisfy those Protocols structurally and are supplied
by the caller.

`compute_tearsheet` (T-P2-11) is the metrics/tearsheet computation
module: given a run's own equity curve and fills, it computes total
return, CAGR, drawdown, the Sharpe/Sortino/Calmar/Omega ratios, trade
statistics, and fee drag as a JSON-serializable `Tearsheet` dict — pure
calculation, with no dependency on `run_backtest` or any persisted
entity.
"""

from __future__ import annotations

from application.backtest.loop import (
    AdvanceableClock,
    AdvanceableMarketDataView,
    BacktestResult,
    BacktestStrategy,
    EventSource,
    run_backtest,
)
from application.backtest.metrics import EquityPoint, Tearsheet, compute_tearsheet

__all__ = [
    "AdvanceableClock",
    "AdvanceableMarketDataView",
    "BacktestResult",
    "BacktestStrategy",
    "EquityPoint",
    "EventSource",
    "Tearsheet",
    "compute_tearsheet",
    "run_backtest",
]
