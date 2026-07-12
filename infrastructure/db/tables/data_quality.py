"""Data quality violation log (TASKS.md T-P1-05).

Named literally in the task description: "Violations write to a
`data_quality_event` log table [and] emit metrics. Data is quarantined,
never silently dropped." Not one of DATABASE.md's original 23 (+
trade_tick, + candle_backfill_checkpoint) entities — like T-P1-04's
checkpoint table, this is a new table an explicit Phase 1 acceptance
criterion requires, not scope creep.

`check_name` is a plain `sa.String`, not a Postgres enum: it names one of
this suite's own validation rules (`infrastructure/validation/
candle_validation.py`), an open-ended and still-growing taxonomy —
ARCHITECTURE.md §11.4 lists two more checks (cross-source agreement,
bid<=ask) not yet implemented, since they need data (multi-venue quotes,
order books) this system doesn't have yet. `severity`, by contrast, is a
genuinely closed, fixed two-value domain (`"quarantined"` vs.
`"flagged"` — see candle_validation.py's module docstring for exactly
what separates them), so it is a Postgres enum, matching this schema's
convention for other truly-closed fields.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from infrastructure.db.tables._common import uuid_pk
from infrastructure.db.tables._metadata import metadata

data_quality_event_severity = sa.Enum(
    "quarantined", "flagged", name="data_quality_event_severity"
)

data_quality_event = sa.Table(
    "data_quality_event",
    metadata,
    uuid_pk(),
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column("interval", sa.String, nullable=False),
    sa.Column("check_name", sa.String, nullable=False),
    sa.Column("severity", data_quality_event_severity, nullable=False),
    sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column("details", JSONB, nullable=False),
    sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
)

sa.Index(
    "ix_data_quality_event_instrument_detected_at",
    data_quality_event.c.instrument_id,
    data_quality_event.c.detected_at.desc(),
)
sa.Index("ix_data_quality_event_check_name", data_quality_event.c.check_name)
