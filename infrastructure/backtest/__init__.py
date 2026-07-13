"""Backtest & Research infrastructure (DATABASE.md Group G).

`DatasetVersion` creation and repository access (T-P1-12) is the first
piece of this package; the eventual Backtest Engine (ARCHITECTURE.md
M13, Phase 2) will add `BacktestRun`/`BacktestTrade`/`BacktestMetrics`
repositories alongside it.
"""

from __future__ import annotations

from infrastructure.backtest.dataset_version_repository import (
    PostgresDatasetVersionRepository,
    compute_dataset_content_hash,
    create_dataset_version,
    hash_row,
)

__all__ = [
    "PostgresDatasetVersionRepository",
    "compute_dataset_content_hash",
    "create_dataset_version",
    "hash_row",
]
