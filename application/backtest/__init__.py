"""Backtest use-case orchestration (ARCHITECTURE.md M13, Phase 2).

`run_backtest` (T-P2-04) is the core simulation loop: the first piece
of code in `application/`. It depends only on domain types and ports
(`Clock`, `MarketDataView`) plus small, locally defined Protocols that
extend those ports with exactly the extra methods this loop needs —
never on `infrastructure/`, per the Dependency Rule. Concrete adapters
(`SimulatedClock`, `CursorMarketDataView`, `HistoricalFeed`, all
T-P2-01/02/03) satisfy those Protocols structurally and are supplied
by the caller.
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

__all__ = [
    "AdvanceableClock",
    "AdvanceableMarketDataView",
    "BacktestResult",
    "BacktestStrategy",
    "EventSource",
    "run_backtest",
]
