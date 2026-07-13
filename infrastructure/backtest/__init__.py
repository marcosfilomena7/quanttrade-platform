"""Backtest & Research infrastructure (DATABASE.md Group G).

`DatasetVersion` creation and repository access (T-P1-12) is the first
piece of this package; the eventual Backtest Engine (ARCHITECTURE.md
M13, Phase 2) will add `BacktestRun`/`BacktestTrade`/`BacktestMetrics`
repositories alongside it.

`CursorMarketDataView` (T-P2-02) is the `MarketDataView` port
implementation with lookahead prevention by construction: a read-only,
past-only cursor over a pre-known candle series, advanced by the
backtest loop via `advance(ts)`.
"""

from __future__ import annotations

from infrastructure.backtest.dataset_version_repository import (
    PostgresDatasetVersionRepository,
    compute_dataset_content_hash,
    create_dataset_version,
    hash_row,
)
from infrastructure.backtest.market_data_view import CursorMarketDataView

__all__ = [
    "PostgresDatasetVersionRepository",
    "compute_dataset_content_hash",
    "create_dataset_version",
    "hash_row",
    "CursorMarketDataView",
]
