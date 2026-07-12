"""Group B · Strategy & Signals: Strategy, StrategyInstance, Signal.

DATABASE.md §B, entities 5–7.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from infrastructure.db.tables._common import created_at_column, uuid_pk
from infrastructure.db.tables._metadata import metadata

strategy = sa.Table(
    "strategy",
    metadata,
    uuid_pk(),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("code_hash", sa.String, nullable=False),
    sa.Column("params_schema", JSONB, nullable=False),
    created_at_column(),
    sa.UniqueConstraint("name", "code_hash", name="uq_strategy_name_code_hash"),
)

strategy_instance = sa.Table(
    "strategy_instance",
    metadata,
    uuid_pk(),
    sa.Column("strategy_id", sa.Uuid(as_uuid=True), sa.ForeignKey("strategy.id"), nullable=False),
    sa.Column("params", JSONB, nullable=False),
    sa.Column(
        "status",
        sa.Enum(
            "registered",
            "validated",
            "initialized",
            "warming_up",
            "ready",
            "running",
            "paused",
            "draining",
            "stopped",
            "faulted",
            name="strategy_instance_status",
        ),
        nullable=False,
    ),
    sa.Column("allocated_capital", sa.Numeric, nullable=False),
    sa.Column("capacity_usd", sa.Numeric, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "allocated_capital >= 0", name="ck_strategy_instance_allocated_capital_nonneg"
    ),
    sa.CheckConstraint("capacity_usd >= 0", name="ck_strategy_instance_capacity_usd_nonneg"),
)

sa.Index(
    "ix_strategy_instance_status_running",
    strategy_instance.c.status,
    postgresql_where=(strategy_instance.c.status == "running"),
)

signal = sa.Table(
    "signal",
    metadata,
    uuid_pk(),
    sa.Column(
        "strategy_instance_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("strategy_instance.id"),
        nullable=False,
    ),
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "direction", sa.Enum("long", "short", "flat", name="signal_direction"), nullable=False
    ),
    sa.Column("strength", sa.Numeric, nullable=False),
    sa.Column("metadata", JSONB, nullable=False),
    created_at_column(),
    sa.CheckConstraint("strength BETWEEN 0 AND 1", name="ck_signal_strength_bounded"),
)

sa.Index("ix_signal_strategy_instance_ts", signal.c.strategy_instance_id, signal.c.ts.desc())
