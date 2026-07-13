"""Parquet Archival Pipeline — Hot → Cold Tier (TASKS.md T-P1-11).

"Implement a nightly job that exports candle and tick data older than 90
days from Postgres to Parquet files on object storage (S3 or local
filesystem for dev), partitioned as `venue/instrument/year/month/
data.parquet`. After a successful export and checksum verification,
delete the corresponding Postgres rows. Register the export as a
`DatasetVersion` with a content hash."

Design decisions, and why:

- **Local filesystem, not S3.** TASKS.md itself names "S3 or local
  filesystem for dev" as an explicit either/or. This repo has no AWS
  SDK dependency, no S3-compatible test infrastructure (no MinIO/
  LocalStack in `docker-compose.yml`), and no acceptance criterion
  mentions S3 specifically ("the correct path," not "the correct S3
  key"). `base_path: Path` is the archival root; every function here is
  file-path-based, so a later task could swap in an S3-backed
  implementation (e.g. by making `write_bytes`/`read_bytes` upload to
  and fetch from a bucket) without touching this module's own querying,
  partitioning, checksum, or `DatasetVersion` logic — but that swap
  itself is not implemented here, since nothing asks for it yet.
- **`candle` AND `trade_tick` are both archived — `trade_tick` is a
  real, already-migrated table, not new scope.** DATABASE.md's own
  "Deferred Entities" table lists `TradeTick` as waiting on "no
  microstructure/execution-cost strategy... yet" — but
  `infrastructure/db/tables/market_data.py`'s own docstring records that
  T-P0-11 already created it anyway, because T-P0-11's own acceptance
  criteria named it explicitly ("Candle, TradeTick, and EquitySnapshot
  are TimescaleDB hypertables"). The table exists in the real, migrated
  schema today; archiving it is querying and deleting from a table that
  is already there, not inventing one.
- **Decimal-valued columns (`open`/`high`/`low`/`close`/`volume`,
  `price`/`qty`) are stored in Parquet as strings, not `float` or a
  fixed-scale `decimal128`.** ARCHITECTURE.md §3.3's Decimal-exactness
  rule applies to archived data exactly as much as to live data. Parquet
  has no arbitrary-precision decimal type; picking one fixed
  `decimal128(precision, scale)` risks silently truncating or
  overflowing a value from an instrument whose `tick_size`/`lot_size`
  happens to need more digits than that fixed choice allows. A string
  column round-trips the exact value with zero precision loss — a
  consumer that needs a `Decimal` back gets one via `Decimal(value)`,
  the same pattern this repo already uses for Decimal fields inside
  `data_quality_event.details` JSONB (see `candle_validation.py`).
- **`archive_candles` is scoped per `(instrument_id, interval)`, exactly
  like T-P1-04's `backfill_candles` and T-P1-05's `run_validation_suite`.**
  The `candle` table's own primary key is `(instrument_id, interval,
  open_time)` — a single instrument has one row per interval per
  timestamp (a `1m` bar and a `1h` bar can share an `open_time`).
  Archiving "all intervals mixed into one Parquet file" was never asked
  for and would conflate distinct series; matching the established
  per-interval scoping keeps this consistent with every other candle
  operation in this codebase. `trade_tick` has no `interval` concept at
  all, so `archive_trade_ticks` has no such parameter.
- **A [range_start, range_end) window is split into one Parquet file per
  calendar (year, month) it touches — not one file for the whole
  window.** "Partitioned as `venue/instrument/year/month/data.parquet`"
  names an exact path template with `year` and `month` as path
  segments; a single file spanning several months would need to live at
  several of those paths simultaneously, which is meaningless. Each
  month's slice is queried, written, verified, and deleted
  independently, so a checksum failure in one month never blocks
  archiving the others.
- **No new checkpoint table.** T-P1-04's `candle_backfill_checkpoint`
  exists because that job's own acceptance criteria explicitly require
  crash-recovery ("killing the process mid-backfill... resumes from the
  last checkpoint"). T-P1-11 has no equivalent criterion — only "the
  same period, re-run, is idempotent." Since a successful run deletes
  its own source rows, a partition with zero remaining rows is
  unambiguously "already archived": the job queries it, finds nothing,
  and skips it entirely (no file rewritten, no `DatasetVersion` row, no
  delete attempted) — idempotency falls directly out of the delete step
  itself, with no extra bookkeeping.
- **A content-hash collision on `dataset_version` (its own `UNIQUE
  (content_hash)` constraint) is resolved with `ON CONFLICT DO NOTHING`,
  returning the existing row's `id`.** An identical re-export (the exact
  same bytes, byte-for-byte) is exactly what "idempotent, no error"
  requires; failing loudly on a second, byte-identical export would
  violate the acceptance criterion, not enforce it.
- **Checksum verification re-reads the file from disk and compares its
  SHA-256 against the hash of the bytes that were serialized in
  memory — not a check against some externally-supplied hash.** This is
  literally "checksum verification" of the *write* itself: did the
  bytes that landed on disk match what was meant to be written. Deletion
  and `DatasetVersion` registration only happen if they match;
  `write_bytes`/`read_bytes` are injectable (defaulting to real
  `Path.write_bytes`/`read_bytes`) purely so a test can simulate a
  corrupted write without needing to actually corrupt a disk.
- **"Trigger a P1 alert" (on checksum failure) is a structured `error`
  log line, not a `data_quality_event` row.** Unlike T-P1-06's gap
  alerts (a genuine candle/interval concept), `data_quality_event`'s
  schema requires a non-null `interval` — meaningless for `trade_tick`,
  which has no interval at all — and a Parquet write/read mismatch is an
  infrastructure failure, not a T-P1-05-style content violation of a
  specific candle. Forcing it into that table's shape for the sake of
  reuse would be a worse fit than a plain structured log entry, which
  is exactly the "alert == structured log" precedent already established
  by T-P1-04/06/10's own alerts. Unlike T-P1-10's watchdog, this is not
  claimed as literally "P1" — ARCHITECTURE.md's own named P1 conditions
  ("position drift, kill switch fired, risk breach, auth failure, data
  feed down > 60s") don't include this one, so the log doesn't overclaim
  a severity nothing in the docs assigns it.
- **No default `range_start` ("archive everything since the beginning of
  time").** Inventing an arbitrary epoch constant would be exactly the
  kind of unrequested assumption the "DATABASE.md takes precedence over
  assumptions" instruction warns against; the caller supplies the window
  explicitly, the same way `backfill_candles` requires an explicit
  `range_start`/`range_end` rather than assuming one.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from infrastructure.db.tables.backtest import dataset_version
from infrastructure.db.tables.market_data import candle as candle_table
from infrastructure.db.tables.market_data import trade_tick as trade_tick_table

_logger = structlog.get_logger()

_DEFAULT_RETENTION_DAYS = 90


def _default_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _default_read_bytes(path: Path) -> bytes:
    return path.read_bytes()


@dataclass(frozen=True)
class PartitionResult:
    """The outcome of archiving one calendar (year, month) partition."""

    year: int
    month: int
    path: Path
    row_count: int
    content_hash: str
    checksum_ok: bool
    dataset_version_id: UUID | None
    deleted: bool


@dataclass(frozen=True)
class ArchivalResult:
    """The outcome of one `archive_candles`/`archive_trade_ticks` call,
    potentially spanning several calendar-month partitions."""

    partitions: list[PartitionResult]

    @property
    def any_checksum_failures(self) -> bool:
        return any(not p.checksum_ok for p in self.partitions)


def _month_boundaries(
    range_start: datetime, range_end: datetime
) -> list[tuple[datetime, datetime]]:
    """`[window_start, window_end)` pairs for every calendar (year, month)
    `[range_start, range_end)` touches, each clipped to that overall range."""
    boundaries: list[tuple[datetime, datetime]] = []
    cursor = range_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < range_end:
        next_month = (
            cursor.replace(year=cursor.year + 1, month=1)
            if cursor.month == 12
            else cursor.replace(month=cursor.month + 1)
        )
        window_start = max(cursor, range_start)
        window_end = min(next_month, range_end)
        if window_start < window_end:
            boundaries.append((window_start, window_end))
        cursor = next_month
    return boundaries


def _candle_rows_to_table(rows: Sequence[sa.Row[Any]]) -> pa.Table:
    return pa.table(
        {
            "instrument_id": pa.array([str(r.instrument_id) for r in rows], type=pa.string()),
            "interval": pa.array([r.interval for r in rows], type=pa.string()),
            "open_time": pa.array([r.open_time for r in rows], type=pa.timestamp("us", tz="UTC")),
            "open": pa.array([str(r.open) for r in rows], type=pa.string()),
            "high": pa.array([str(r.high) for r in rows], type=pa.string()),
            "low": pa.array([str(r.low) for r in rows], type=pa.string()),
            "close": pa.array([str(r.close) for r in rows], type=pa.string()),
            "volume": pa.array([str(r.volume) for r in rows], type=pa.string()),
            "trade_count": pa.array([r.trade_count for r in rows], type=pa.int64()),
            "is_closed": pa.array([r.is_closed for r in rows], type=pa.bool_()),
            "source": pa.array([r.source for r in rows], type=pa.string()),
        }
    )


def _trade_tick_rows_to_table(rows: Sequence[sa.Row[Any]]) -> pa.Table:
    return pa.table(
        {
            "instrument_id": pa.array([str(r.instrument_id) for r in rows], type=pa.string()),
            "ts": pa.array([r.ts for r in rows], type=pa.timestamp("us", tz="UTC")),
            "venue_trade_id": pa.array([r.venue_trade_id for r in rows], type=pa.string()),
            "price": pa.array([str(r.price) for r in rows], type=pa.string()),
            "qty": pa.array([str(r.qty) for r in rows], type=pa.string()),
            "side": pa.array([r.side for r in rows], type=pa.string()),
        }
    )


def _register_dataset_version(
    conn: sa.Connection,
    *,
    content_hash: str,
    instrument_id: UUID,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
) -> UUID:
    stmt = (
        pg_insert(dataset_version)
        .values(
            id=uuid4(),
            content_hash=content_hash,
            symbol_set=[str(instrument_id)],
            date_range_start=window_start.date(),
            date_range_end=(window_end - timedelta(microseconds=1)).date(),
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["content_hash"])
        .returning(dataset_version.c.id)
    )
    inserted = conn.execute(stmt).one_or_none()
    if inserted is not None:
        return UUID(str(inserted.id))

    existing = conn.execute(
        sa.select(dataset_version.c.id).where(dataset_version.c.content_hash == content_hash)
    ).one()
    return UUID(str(existing.id))


def _archive_partition(
    conn: sa.Connection,
    *,
    table: pa.Table,
    delete_stmt: sa.sql.Delete,
    path: Path,
    instrument_id: UUID,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
    write_bytes: Callable[[Path, bytes], None],
    read_bytes: Callable[[Path], bytes],
) -> PartitionResult:
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    serialized = buffer.getvalue()
    expected_hash = hashlib.sha256(serialized).hexdigest()

    write_bytes(path, serialized)
    actual_bytes = read_bytes(path)
    actual_hash = hashlib.sha256(actual_bytes).hexdigest()

    row_count = table.num_rows

    if actual_hash != expected_hash:
        _logger.error(
            "parquet_archival_checksum_failed",
            instrument_id=str(instrument_id),
            path=str(path),
            expected_hash=expected_hash,
            actual_hash=actual_hash,
        )
        return PartitionResult(
            year=window_start.year,
            month=window_start.month,
            path=path,
            row_count=row_count,
            content_hash=actual_hash,
            checksum_ok=False,
            dataset_version_id=None,
            deleted=False,
        )

    dataset_version_id = _register_dataset_version(
        conn,
        content_hash=actual_hash,
        instrument_id=instrument_id,
        window_start=window_start,
        window_end=window_end,
        now=now,
    )
    conn.execute(delete_stmt)
    conn.commit()

    return PartitionResult(
        year=window_start.year,
        month=window_start.month,
        path=path,
        row_count=row_count,
        content_hash=actual_hash,
        checksum_ok=True,
        dataset_version_id=dataset_version_id,
        deleted=True,
    )


def archive_candles(
    conn: sa.Connection,
    *,
    instrument_id: UUID,
    interval: str,
    venue_name: str,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    base_path: Path,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    write_bytes: Callable[[Path, bytes], None] = _default_write_bytes,
    read_bytes: Callable[[Path], bytes] = _default_read_bytes,
) -> ArchivalResult:
    """Archive `candle` rows for `(instrument_id, interval)` in
    `[range_start, range_end)`, one Parquet file per calendar month
    touched, at `base_path/venue_name/symbol/year/month/data.parquet`."""
    ts = now()
    partitions: list[PartitionResult] = []

    for window_start, window_end in _month_boundaries(range_start, range_end):
        rows = conn.execute(
            sa.select(candle_table)
            .where(
                candle_table.c.instrument_id == instrument_id,
                candle_table.c.interval == interval,
                candle_table.c.open_time >= window_start,
                candle_table.c.open_time < window_end,
            )
            .order_by(candle_table.c.open_time)
        ).all()

        if not rows:
            continue

        path = (
            base_path
            / venue_name
            / symbol
            / str(window_start.year)
            / f"{window_start.month:02d}"
            / "data.parquet"
        )
        delete_stmt = sa.delete(candle_table).where(
            candle_table.c.instrument_id == instrument_id,
            candle_table.c.interval == interval,
            candle_table.c.open_time >= window_start,
            candle_table.c.open_time < window_end,
        )

        partitions.append(
            _archive_partition(
                conn,
                table=_candle_rows_to_table(rows),
                delete_stmt=delete_stmt,
                path=path,
                instrument_id=instrument_id,
                window_start=window_start,
                window_end=window_end,
                now=ts,
                write_bytes=write_bytes,
                read_bytes=read_bytes,
            )
        )

    return ArchivalResult(partitions=partitions)


def archive_trade_ticks(
    conn: sa.Connection,
    *,
    instrument_id: UUID,
    venue_name: str,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    base_path: Path,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    write_bytes: Callable[[Path, bytes], None] = _default_write_bytes,
    read_bytes: Callable[[Path], bytes] = _default_read_bytes,
) -> ArchivalResult:
    """Archive `trade_tick` rows for `instrument_id` in `[range_start,
    range_end)`, one Parquet file per calendar month touched, at
    `base_path/venue_name/symbol/year/month/data.parquet`."""
    ts = now()
    partitions: list[PartitionResult] = []

    for window_start, window_end in _month_boundaries(range_start, range_end):
        rows = conn.execute(
            sa.select(trade_tick_table)
            .where(
                trade_tick_table.c.instrument_id == instrument_id,
                trade_tick_table.c.ts >= window_start,
                trade_tick_table.c.ts < window_end,
            )
            .order_by(trade_tick_table.c.ts)
        ).all()

        if not rows:
            continue

        path = (
            base_path
            / venue_name
            / symbol
            / str(window_start.year)
            / f"{window_start.month:02d}"
            / "data.parquet"
        )
        delete_stmt = sa.delete(trade_tick_table).where(
            trade_tick_table.c.instrument_id == instrument_id,
            trade_tick_table.c.ts >= window_start,
            trade_tick_table.c.ts < window_end,
        )

        partitions.append(
            _archive_partition(
                conn,
                table=_trade_tick_rows_to_table(rows),
                delete_stmt=delete_stmt,
                path=path,
                instrument_id=instrument_id,
                window_start=window_start,
                window_end=window_end,
                now=ts,
                write_bytes=write_bytes,
                read_bytes=read_bytes,
            )
        )

    return ArchivalResult(partitions=partitions)


def run_nightly_archival(
    conn: sa.Connection,
    *,
    instrument_id: UUID,
    interval: str,
    venue_name: str,
    symbol: str,
    archive_from: datetime,
    base_path: Path,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[ArchivalResult, ArchivalResult]:
    """The nightly entry point: archives both `candle` and `trade_tick`
    rows in `[archive_from, now - retention_days)` for one `(instrument,
    interval)` pair — TASKS.md's literal "candle and tick data older
    than 90 days." Scheduling ("nightly") is an external, operational
    concern, matching this repo's own established precedent
    (T-P1-02/03/04/06's identical scoping decision).
    """
    ts = now()
    cutoff = ts - timedelta(days=retention_days)

    candle_result = archive_candles(
        conn,
        instrument_id=instrument_id,
        interval=interval,
        venue_name=venue_name,
        symbol=symbol,
        range_start=archive_from,
        range_end=cutoff,
        base_path=base_path,
        now=lambda: ts,
    )
    tick_result = archive_trade_ticks(
        conn,
        instrument_id=instrument_id,
        venue_name=venue_name,
        symbol=symbol,
        range_start=archive_from,
        range_end=cutoff,
        base_path=base_path,
        now=lambda: ts,
    )
    return candle_result, tick_result
