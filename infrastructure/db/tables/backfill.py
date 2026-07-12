"""Candle backfill progress checkpoint (TASKS.md T-P1-04).

Not one of DATABASE.md's 23 entities (+ trade_tick, T-P0-11's 24th) — the
historical OHLCV backfill job's own acceptance criteria require it
explicitly: "log progress to a checkpoint table so interrupted runs
resume from the last successful chunk" and "killing the process
mid-backfill and restarting resumes from the last checkpoint, not from
the beginning." Resuming across a process restart is only possible with
state that survives the process, so a dedicated table — not an in-memory
structure, and not inferring progress from `MAX(candle.open_time)`, which
cannot distinguish "this range is still in progress" from "this range is
finished but legitimately shorter than requested" — is required.

One row per `(venue_id, instrument_id, interval, range_start, range_end)`
— the natural key of one backfill *request*. Unlike `universe_snapshot`
(T-P1-03, append-only by DATABASE.md's own explicit constraint), this
table is a live job-progress record and is mutated in place as a backfill
advances — there is no historical-record reason to forbid updates here.
"""

from __future__ import annotations

import sqlalchemy as sa

from infrastructure.db.tables._metadata import metadata

backfill_checkpoint_status = sa.Enum(
    "in_progress", "completed", name="backfill_checkpoint_status"
)

candle_backfill_checkpoint = sa.Table(
    "candle_backfill_checkpoint",
    metadata,
    sa.Column("venue_id", sa.Uuid(as_uuid=True), sa.ForeignKey("venue.id"), nullable=False),
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column("interval", sa.String, nullable=False),
    sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_completed_open_time", sa.DateTime(timezone=True), nullable=True),
    sa.Column("status", backfill_checkpoint_status, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint(
        "venue_id", "instrument_id", "interval", "range_start", "range_end",
        name="pk_candle_backfill_checkpoint",
    ),
)
