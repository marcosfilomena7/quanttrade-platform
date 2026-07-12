"""Shared column factories and enum types reused across table definitions.

DATABASE.md's field lists say "decimal" for every money/quantity field with
no precision/scale bound — mapped to unbounded `sa.Numeric()`, matching
ARCHITECTURE.md §3.3's "Decimal everywhere" with no artificial truncation.
"jsonb" maps to Postgres's native `JSONB` (not generic `sa.JSON`), and
"timestamp" maps to `sa.DateTime(timezone=True)` — every timestamp in this
schema is timezone-aware, matching how `domain/` already treats time.

`created_at`-style columns get a `server_default=now()` — these are pure
row-insertion audit metadata, not a business event time. Every other
timestamp (`ts`, `triggered_at`, `started_at`, ...) is deliberately left
with no default: ARCHITECTURE.md §4.7 requires business timestamps to come
from the application's injected `Clock`, never a wall clock — and
`now()` on the database server *is* a wall clock. Only bookkeeping about
when a row was physically inserted is exempt from that rule.

Column factory functions return a fresh `sa.Column` on every call — a
`Column` instance can only belong to one `Table`, so these cannot be
module-level singletons the way the shared enums below are.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

# Enums reused verbatim, by identical value set, across more than one table.
# Reusing the same `sa.Enum` object (rather than redeclaring the same name
# in multiple places) is what lets SQLAlchemy/Alembic emit the Postgres
# `CREATE TYPE` for it exactly once instead of colliding on a duplicate name.
ORDER_SIDE_ENUM = sa.Enum("buy", "sell", name="order_side")
ORDER_TYPE_ENUM = sa.Enum("market", "limit", name="order_type")


def uuid_pk() -> sa.Column[UUID]:
    """A `UUID PRIMARY KEY` column, server-generated if not supplied."""
    return sa.Column(
        "id",
        sa.Uuid(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def created_at_column(name: str = "created_at") -> sa.Column[datetime]:
    """Pure row-insertion audit metadata — see module docstring."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
