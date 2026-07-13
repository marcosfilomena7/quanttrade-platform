"""DatasetVersion creation and repository (TASKS.md T-P1-12).

"Implement `DatasetVersion` creation: when a dataset is finalized (either
from backfill or archival), compute a content hash over `(symbol_set,
date_range, row_count, sample_hashes)` and store it in the
`dataset_version` table. Expose a `DatasetVersionRepository.get(id)` that
returns the version record, enabling backtests to pin a dataset version
and be exactly reproducible."

Design decisions, and why:

- **A new, general-purpose content hash — not a reuse or a retrofit of
  T-P1-11's Parquet-file SHA-256.** T-P1-11's own acceptance criterion
  ("Content hash in DatasetVersion matches the Parquet file's SHA-256")
  is already satisfied by that task's own, narrower, already-shipped
  implementation (`infrastructure/jobs/parquet_archival_job.py`), which
  this task does not modify — "do not change any behavior from T-P0-01
  through T-P1-11" forecloses retrofitting it to use this task's own,
  differently-shaped formula. T-P1-12's own literal formula —
  `(symbol_set, date_range, row_count, sample_hashes)` — is a distinct,
  general-purpose scheme any *future* caller (backfill, archival, or a
  Phase 2 backtest dataset-finalization step) can adopt; this task
  implements and exposes that scheme without forcing already-working
  code to switch to it.
- **"sample_hashes" means one hash per row in the dataset, not a
  statistical subsample.** TASKS.md's own acceptance criterion is
  unconditional: "Modifying one row in the dataset produces a different
  hash" — no caveat for "if that row happened to be sampled." A
  statistical subsample would silently fail this exact criterion for any
  modified row that falls outside the sample. Reading "sample" as "one
  hash *sample* per row" (a per-row fingerprint), not "a sample of rows,"
  is the only interpretation consistent with the acceptance criterion as
  written. `hash_row` is the per-row fingerprint helper; callers supply
  the resulting list, in a caller-chosen but consistent row order (e.g.
  ordered by timestamp), as `sample_hashes`.
- **`symbol_set` is hashed as a sorted set; `sample_hashes` is hashed in
  the caller's given order.** DATABASE.md describes `symbol_set` as
  "list of instrument_ids included" — membership, not a meaningful
  sequence; sorting before hashing means two exports naming the same
  instruments in a different collection order still produce the same
  hash. `sample_hashes`, by contrast, is a per-row fingerprint sequence
  where order reflects the dataset's own row order — preserving it means
  a reordering of the same rows (a genuinely different dataset shape) is
  also detected, not just a value change within one row.
- **`hash_row` uses `default=str` in its JSON serialization** so a raw DB
  row's `Decimal`/`UUID`/`datetime` values (none of which `json.dumps`
  handles natively) serialize to their exact string form without this
  helper needing to know a specific row shape — it works for `Candle`
  rows, `TradeTick` rows, or any other mapping a future caller finalizes.
- **`DatasetVersion` (`domain/dataset_version.py`) and
  `DatasetVersionRepository` (`domain/ports/dataset_version_repository.py`)
  follow the exact established pattern from T-P0-07**: a plain, frozen
  `@dataclass(slots=True)` value type, and a `@runtime_checkable Protocol`
  port with zero implementation, both in `domain/`, so a future
  domain-level or application-level consumer (the eventual Backtest
  Engine, ARCHITECTURE.md M13) can type-reference them without importing
  `infrastructure/`. `.get()` is synchronous, matching `Clock.now()` and
  `MarketDataView.bars()` — like those two, this is a batch/offline
  lookup, not part of the async realtime pipeline.
- **`create_dataset_version` is idempotent via `ON CONFLICT (content_hash)
  DO NOTHING`**, mirroring T-P1-11's own resolution to the identical
  underlying problem (a hash-identical re-finalization of the same
  dataset must not raise) — implemented independently here, since this
  task's hash formula (and therefore its own conflict target) is a
  separate concern from T-P1-11's.
- **Placed in a new `infrastructure/backtest/` package**, not
  `infrastructure/jobs/`. DATABASE.md groups `DatasetVersion` under
  "G · Backtesting & Research," and ARCHITECTURE.md names the eventual
  consumer "M13 · Backtest Engine" (Phase 2). No backtest-related
  infrastructure package exists yet; this is the natural first piece of
  it, not a fit for the venue-data-pipeline-shaped `infrastructure/jobs/`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domain.dataset_version import DatasetVersion
from infrastructure.db.tables.backtest import dataset_version as dataset_version_table


def hash_row(row: Mapping[str, object]) -> str:
    """A per-row content fingerprint: canonical JSON (sorted keys, `str`
    fallback for `Decimal`/`UUID`/`datetime`/... values), SHA-256'd."""
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_dataset_content_hash(
    *,
    symbol_set: Sequence[UUID],
    date_range_start: date,
    date_range_end: date,
    row_count: int,
    sample_hashes: Sequence[str],
) -> str:
    """TASKS.md T-P1-12's literal formula: a content hash over
    `(symbol_set, date_range, row_count, sample_hashes)`. `symbol_set` is
    sorted (a set, not a meaningful sequence); `sample_hashes` preserves
    the caller's own row order (see this module's docstring)."""
    canonical = {
        "symbol_set": sorted(str(s) for s in symbol_set),
        "date_range_start": date_range_start.isoformat(),
        "date_range_end": date_range_end.isoformat(),
        "row_count": row_count,
        "sample_hashes": list(sample_hashes),
    }
    canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _row_to_dataset_version(row: sa.Row[Any]) -> DatasetVersion:
    return DatasetVersion(
        id=UUID(str(row.id)),
        content_hash=row.content_hash,
        symbol_set=tuple(UUID(s) for s in row.symbol_set),
        date_range_start=row.date_range_start,
        date_range_end=row.date_range_end,
        created_at=row.created_at,
    )


def create_dataset_version(
    conn: sa.Connection,
    *,
    symbol_set: Sequence[UUID],
    date_range_start: date,
    date_range_end: date,
    row_count: int,
    sample_hashes: Sequence[str],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DatasetVersion:
    """Finalize a dataset: compute its content hash (T-P1-12's own
    formula) and persist a `DatasetVersion` row. Idempotent: an identical
    prior export (same content hash) returns the existing record rather
    than raising a unique-constraint error.
    """
    content_hash = compute_dataset_content_hash(
        symbol_set=symbol_set,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        row_count=row_count,
        sample_hashes=sample_hashes,
    )
    stmt = (
        pg_insert(dataset_version_table)
        .values(
            id=uuid4(),
            content_hash=content_hash,
            symbol_set=[str(s) for s in symbol_set],
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            created_at=now(),
        )
        .on_conflict_do_nothing(index_elements=["content_hash"])
        .returning(dataset_version_table.c.id, dataset_version_table.c.created_at)
    )
    inserted = conn.execute(stmt).one_or_none()
    if inserted is not None:
        return DatasetVersion(
            id=UUID(str(inserted.id)),
            content_hash=content_hash,
            symbol_set=tuple(symbol_set),
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            created_at=inserted.created_at,
        )

    existing = conn.execute(
        sa.select(dataset_version_table).where(
            dataset_version_table.c.content_hash == content_hash
        )
    ).one()
    return _row_to_dataset_version(existing)


class PostgresDatasetVersionRepository:
    """`DatasetVersionRepository` (domain port) backed by the real
    `dataset_version` table."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def get(self, dataset_version_id: UUID) -> DatasetVersion | None:
        row = self._conn.execute(
            sa.select(dataset_version_table).where(
                dataset_version_table.c.id == dataset_version_id
            )
        ).one_or_none()
        if row is None:
            return None
        return _row_to_dataset_version(row)
