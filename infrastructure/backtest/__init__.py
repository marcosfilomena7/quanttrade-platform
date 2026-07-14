"""Backtest & Research infrastructure (DATABASE.md Group G).

`DatasetVersion` creation and repository access (T-P1-12) is the first
piece of this package; the eventual Backtest Engine (ARCHITECTURE.md
M13, Phase 2) will add `BacktestRun`/`BacktestTrade`/`BacktestMetrics`
repositories alongside it.

`CursorMarketDataView` (T-P2-02) is the `MarketDataView` port
implementation with lookahead prevention by construction: a read-only,
past-only cursor over a pre-known candle series, advanced by the
backtest loop via `advance(ts)`.

`HistoricalFeed` (T-P2-03) is the `MarketDataFeed` port implementation
for backtesting: a strictly monotonic, multi-timeframe min-heap merge
over candle series loaded from a `DatasetVersion`, raising
`FeedExhausted` once exhausted rather than silently stopping.

`BacktestRunRegistry`/`log_backtest_run` (T-P2-12) auto-log every
backtest run to `backtest_run`/`backtest_metrics`: `log_backtest_run`
raises `BacktestRegistryRequired` unless called inside an active
`BacktestRunRegistry` context/decorator, so no run can escape logging.
"""

from __future__ import annotations

from infrastructure.backtest.dataset_version_repository import (
    PostgresDatasetVersionRepository,
    compute_dataset_content_hash,
    create_dataset_version,
    hash_row,
)
from infrastructure.backtest.historical_feed import (
    FeedExhausted,
    HistoricalFeed,
    load_candle_series_from_dataset_version,
)
from infrastructure.backtest.market_data_view import CursorMarketDataView
from infrastructure.backtest.run_registry import (
    BacktestRegistryRequired,
    BacktestRunRegistry,
    log_backtest_run,
)

__all__ = [
    "PostgresDatasetVersionRepository",
    "compute_dataset_content_hash",
    "create_dataset_version",
    "hash_row",
    "BacktestRegistryRequired",
    "BacktestRunRegistry",
    "CursorMarketDataView",
    "FeedExhausted",
    "HistoricalFeed",
    "load_candle_series_from_dataset_version",
    "log_backtest_run",
]
