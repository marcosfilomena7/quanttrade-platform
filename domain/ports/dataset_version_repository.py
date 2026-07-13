"""DatasetVersionRepository port — read access to persisted `DatasetVersion`
records (TASKS.md T-P1-12).

"Expose a `DatasetVersionRepository.get(id)` that returns the version
record, enabling backtests to pin a dataset version and be exactly
reproducible." Synchronous, like `Clock.now()` and `MarketDataView.bars()`
(T-P0-07) — a Postgres lookup for an offline/batch backtest-reproducibility
concern, not part of the async realtime pipeline `EventBus`/
`MarketDataFeed`/`VenuePort` serve.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from domain.dataset_version import DatasetVersion


@runtime_checkable
class DatasetVersionRepository(Protocol):
    """Read-only access to persisted `DatasetVersion` records, by id."""

    def get(self, dataset_version_id: UUID) -> DatasetVersion | None: ...
