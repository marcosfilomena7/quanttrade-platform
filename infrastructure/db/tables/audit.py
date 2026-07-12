"""Group F · Audit: EventLog.

DATABASE.md §F, entity 19. "**FK:** none, by design. A foreign key here
would let a bug in some other table's deletion cascade corrupt the one
table that must never be corrupted. `aggregate_id` references other
entities logically, not referentially." TASKS.md T-P0-11 acceptance
criterion: "EventLog has no FK columns."

`seq` is a server-generated `BIGINT GENERATED ALWAYS AS IDENTITY` — a
plain, monotonically increasing global sequence, matching "monotonic,
global, PK" without the application needing to compute or coordinate it.

Append-only enforcement via revoked UPDATE/DELETE grants at the database-
role level (DATABASE.md: "enforced by revoking UPDATE/DELETE grants
entirely at the database-user level") is deliberately *not* implemented
here — it requires defining and managing a restricted application
database role, which is an operational/deployment concern with no
acceptance criterion in this task, not part of a baseline schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from infrastructure.db.tables._metadata import metadata

event_log = sa.Table(
    "event_log",
    metadata,
    sa.Column("seq", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("event_type", sa.String, nullable=False),
    sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("prev_hash", sa.LargeBinary, nullable=False),
    sa.Column("hash", sa.LargeBinary, nullable=False),
)

sa.Index("ix_event_log_aggregate_id_seq", event_log.c.aggregate_id, event_log.c.seq)
