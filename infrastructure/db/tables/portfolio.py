"""Group E · Portfolio & Accounting: Account, Position, LedgerEntry, EquitySnapshot.

DATABASE.md §E, entities 15–18.

`EquitySnapshot`'s primary key is composite `(id, ts)`, not the bare `id`
DATABASE.md states. TimescaleDB requires every unique/primary-key
constraint on a hypertable to include the partitioning column; `id`
remains globally unique in practice (server-generated UUID default), so
this is the standard, minimal TimescaleDB accommodation, not a change to
what the table means or how it's queried.
"""

from __future__ import annotations

import sqlalchemy as sa

from infrastructure.db.tables._common import created_at_column, uuid_pk
from infrastructure.db.tables._metadata import metadata

account = sa.Table(
    "account",
    metadata,
    uuid_pk(),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("venue_id", sa.Uuid(as_uuid=True), sa.ForeignKey("venue.id"), nullable=False),
    sa.Column("base_currency", sa.String, nullable=False),
    created_at_column(),
    sa.UniqueConstraint("venue_id", "name", name="uq_account_venue_id_name"),
)

position = sa.Table(
    "position",
    metadata,
    uuid_pk(),
    sa.Column("account_id", sa.Uuid(as_uuid=True), sa.ForeignKey("account.id"), nullable=False),
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column(
        "strategy_instance_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("strategy_instance.id"),
        nullable=False,
    ),
    sa.Column("qty", sa.Numeric, nullable=False),
    sa.Column("avg_entry_price", sa.Numeric, nullable=False),
    sa.Column("realized_pnl", sa.Numeric, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "account_id",
        "instrument_id",
        "strategy_instance_id",
        name="uq_position_account_instrument_strategy_instance",
    ),
)

ledger_entry = sa.Table(
    "ledger_entry",
    metadata,
    uuid_pk(),
    sa.Column("account_id", sa.Uuid(as_uuid=True), sa.ForeignKey("account.id"), nullable=False),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("debit_account", sa.String, nullable=False),
    sa.Column("credit_account", sa.String, nullable=False),
    sa.Column("amount", sa.Numeric, nullable=False),
    sa.Column("currency", sa.String, nullable=False),
    sa.Column("ref_fill_id", sa.Uuid(as_uuid=True), sa.ForeignKey("fill.id"), nullable=True),
    sa.CheckConstraint("amount > 0", name="ck_ledger_entry_amount_positive"),
)

sa.Index("ix_ledger_entry_account_id_ts", ledger_entry.c.account_id, ledger_entry.c.ts.desc())

equity_snapshot = sa.Table(
    "equity_snapshot",
    metadata,
    sa.Column(
        "id",
        sa.Uuid(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column("account_id", sa.Uuid(as_uuid=True), sa.ForeignKey("account.id"), nullable=False),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("cash", sa.Numeric, nullable=False),
    sa.Column("positions_value", sa.Numeric, nullable=False),
    sa.Column("total_equity", sa.Numeric, nullable=False),
    sa.Column("drawdown_pct", sa.Numeric, nullable=False),
    sa.PrimaryKeyConstraint("id", "ts", name="pk_equity_snapshot"),
)

sa.Index(
    "ix_equity_snapshot_account_id_ts",
    equity_snapshot.c.account_id,
    equity_snapshot.c.ts.desc(),
)
