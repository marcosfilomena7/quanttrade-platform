"""DatasetVersion domain value — an immutable, content-hashed pointer to
exactly which historical data a backtest used (TASKS.md T-P1-12).

DATABASE.md §G, entity 20: "Content-hashed, immutable pointer to exactly
which historical data a backtest used... without pinning, a 'reproduced'
backtest run months later may silently use corrected/backfilled data and
produce a different result with no way to explain the discrepancy."

Fields mirror DATABASE.md's table exactly: `id`, `content_hash`,
`symbol_set` (list of instrument_ids), `date_range_start`/`date_range_end`
(dates, not timestamps — DATABASE.md types them `date`), `created_at`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """A content-hashed, immutable snapshot descriptor.

    `content_hash` is unique per distinct dataset content (TASKS.md
    T-P1-12: "two identical dataset exports produce the same content
    hash... modifying one row produces a different hash") — the
    invariant enforced by DATABASE.md's own `UNIQUE(content_hash)`
    constraint, mirrored here as a basic non-empty check since this
    type has no database connection to enforce uniqueness itself.
    """

    id: UUID
    content_hash: str
    symbol_set: tuple[UUID, ...]
    date_range_start: date
    date_range_end: date
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.content_hash:
            raise ValueError("DatasetVersion.content_hash must be non-empty")
        if not self.symbol_set:
            raise ValueError("DatasetVersion.symbol_set must be non-empty")
        if self.date_range_start > self.date_range_end:
            raise ValueError("DatasetVersion.date_range_start must be <= date_range_end")
