"""Point-in-Time Universe Snapshot Job (TASKS.md T-P1-03).

"Implement a daily job that inserts one `UniverseSnapshot` row per active
instrument per venue (date, venue_id, instrument_id, is_tradeable). This
captures which symbols were listed and tradeable on each calendar day.
Must run on day one, before any backtesting, because the data is
unrecoverable retroactively. Schedule immediately alongside the reference
data importer."

Design decisions, and why:

- **"Active instrument" means every instrument this venue has on record**
  (any status), not only those currently `"trading"`. The second
  acceptance criterion — "An instrument delisted between two runs has
  `is_tradeable = false` on the second snapshot" — only makes sense if a
  delisted instrument still *gets* a snapshot row, just with
  `is_tradeable = false`. Omitting non-trading instruments entirely would
  make that criterion impossible to satisfy, and would silently break the
  survivorship-bias defense DATABASE.md names as this table's entire
  purpose ("a delisted symbol's history becomes unrecoverable the moment
  the exchange stops returning it").
- **`is_tradeable = (instrument.status == "trading")`.** The `instrument`
  table's `status` is already the authoritative, current-as-of-today
  fact (kept in sync by the reference data importer, T-P1-02); this job
  reads it fresh on every run and does not re-derive or cache it.
- **Upsert uses `ON CONFLICT (snapshot_date, venue_id, instrument_id) DO
  NOTHING`, never `DO UPDATE`.** DATABASE.md's own constraint for this
  table is explicit: "append-only, no update/delete." A second run for a
  date that already has a captured snapshot must leave that historical
  record exactly as first written, in contrast to the reference-data
  importer's `instrument` table (T-P1-02), which is a *current-state*
  table and is deliberately upserted with `DO UPDATE`.
- **The query criterion is implemented literally.** TASKS.md's acceptance
  criterion is `SELECT instrument_id FROM universe_snapshot WHERE date =
  $1 AND is_tradeable = true` — no venue filter. The real column is
  `snapshot_date` (DATABASE.md / T-P0-11), not `date`; the task's "date"
  is shorthand for that column, not a literal different one.
  `query_tradeable_instruments` mirrors this exact `WHERE` clause with no
  added parameters, since the acceptance criterion names none.
- **No new Prometheus metric or structured warning log.** Unlike T-P1-02
  (whose acceptance criteria explicitly name a `reference_data_changed`
  metric and warning), nothing in T-P1-03 asks for either — adding one
  here would be unrequested scope.
- **Nothing here calls Binance.** The job only reads the already-synced
  `instrument` table; "Schedule immediately alongside the reference data
  importer" describes the operational run order (this job runs right
  after T-P1-02's importer each day), not a second venue fetch.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domain.instrument import InstrumentStatus
from infrastructure.db.tables.reference import instrument as instrument_table
from infrastructure.db.tables.reference import universe_snapshot as universe_snapshot_table


@dataclass(frozen=True)
class UniverseSnapshotResult:
    """Summary of one `capture_universe_snapshot` run."""

    snapshot_date: date
    captured: int
    already_captured: int


def is_tradeable(status: InstrumentStatus) -> bool:
    """`True` only for `"trading"` — every other status (`"halted"`,
    `"delisted"`) is not tradeable."""
    return status == "trading"


def capture_universe_snapshot(
    *,
    conn: sa.Connection,
    venue_id: UUID,
    snapshot_date: date | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> UniverseSnapshotResult:
    """Insert one `universe_snapshot` row per instrument this venue has on
    record, for `snapshot_date` (defaults to `now().date()`).

    Idempotent per calendar day: a second call for a date that has
    already been captured changes nothing (`ON CONFLICT DO NOTHING`),
    per this table's append-only design. Commits the transaction on the
    given connection before returning.
    """
    ts = now()
    effective_date = snapshot_date if snapshot_date is not None else ts.date()

    instruments = conn.execute(
        sa.select(instrument_table.c.id, instrument_table.c.status).where(
            instrument_table.c.venue_id == venue_id
        )
    ).all()

    if not instruments:
        return UniverseSnapshotResult(snapshot_date=effective_date, captured=0, already_captured=0)

    rows = [
        {
            "snapshot_date": effective_date,
            "venue_id": venue_id,
            "instrument_id": row.id,
            "is_tradeable": is_tradeable(row.status),
            "captured_at": ts,
        }
        for row in instruments
    ]

    stmt = (
        pg_insert(universe_snapshot_table)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["snapshot_date", "venue_id", "instrument_id"])
        .returning(universe_snapshot_table.c.id)
    )

    captured = len(conn.execute(stmt).all())
    conn.commit()

    return UniverseSnapshotResult(
        snapshot_date=effective_date,
        captured=captured,
        already_captured=len(rows) - captured,
    )


def query_tradeable_instruments(conn: sa.Connection, snapshot_date: date) -> Sequence[UUID]:
    """`SELECT instrument_id FROM universe_snapshot WHERE date = $1 AND
    is_tradeable = true` (TASKS.md T-P1-03's acceptance criterion,
    verbatim, against the real `snapshot_date` column)."""
    result = conn.execute(
        sa.select(universe_snapshot_table.c.instrument_id).where(
            universe_snapshot_table.c.snapshot_date == snapshot_date,
            universe_snapshot_table.c.is_tradeable.is_(True),
        )
    )
    return [row.instrument_id for row in result]
