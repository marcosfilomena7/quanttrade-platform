"""Group C · Risk & Order Flow: OrderIntent, RiskDecision, SystemHaltEvent.

DATABASE.md §C, entities 8–10.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from infrastructure.db.tables._common import (
    ORDER_SIDE_ENUM,
    ORDER_TYPE_ENUM,
    created_at_column,
    uuid_pk,
)
from infrastructure.db.tables._metadata import metadata

order_intent = sa.Table(
    "order_intent",
    metadata,
    uuid_pk(),
    sa.Column("signal_id", sa.Uuid(as_uuid=True), sa.ForeignKey("signal.id"), nullable=True),
    sa.Column(
        "strategy_instance_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("strategy_instance.id"),
        nullable=False,
    ),
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column("side", ORDER_SIDE_ENUM, nullable=False),
    sa.Column("target_qty", sa.Numeric, nullable=False),
    sa.Column("order_type", ORDER_TYPE_ENUM, nullable=False),
    sa.Column("limit_price", sa.Numeric, nullable=True),
    created_at_column(),
    sa.CheckConstraint("target_qty <> 0", name="ck_order_intent_target_qty_nonzero"),
    sa.CheckConstraint(
        "order_type <> 'limit' OR limit_price IS NOT NULL",
        name="ck_order_intent_limit_price_required",
    ),
)

sa.Index(
    "ix_order_intent_strategy_instance_created_at",
    order_intent.c.strategy_instance_id,
    order_intent.c.created_at.desc(),
)

risk_decision = sa.Table(
    "risk_decision",
    metadata,
    uuid_pk(),
    sa.Column(
        "order_intent_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("order_intent.id"),
        nullable=False,
        unique=True,
    ),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("approved", sa.Boolean, nullable=False),
    sa.Column("rules_evaluated", JSONB, nullable=False),
    sa.Column("rejection_reason", sa.String, nullable=True),
    sa.Column("limits_config_version", sa.String, nullable=False),
    sa.CheckConstraint(
        "approved OR rejection_reason IS NOT NULL",
        name="ck_risk_decision_rejection_reason_required",
    ),
)

sa.Index("ix_risk_decision_approved_ts", risk_decision.c.approved, risk_decision.c.ts.desc())
sa.Index("ix_risk_decision_ts", risk_decision.c.ts.desc())

system_halt_event = sa.Table(
    "system_halt_event",
    metadata,
    uuid_pk(),
    sa.Column(
        "tier", sa.Enum("soft_halt", "hard_halt", "kill", name="system_halt_tier"), nullable=False
    ),
    sa.Column("trigger_reason", sa.String, nullable=False),
    sa.Column(
        "triggered_by",
        sa.Enum("system", "operator", name="system_halt_triggered_by"),
        nullable=False,
    ),
    sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "risk_decision_id", sa.Uuid(as_uuid=True), sa.ForeignKey("risk_decision.id"), nullable=True
    ),
    sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cleared_by", sa.String, nullable=True),
    sa.CheckConstraint(
        "cleared_at IS NULL OR cleared_by IS NOT NULL",
        name="ck_system_halt_event_cleared_by_required",
    ),
)

sa.Index("ix_system_halt_event_triggered_at", system_halt_event.c.triggered_at.desc())
sa.Index(
    "ix_system_halt_event_uncleared",
    system_halt_event.c.cleared_at,
    postgresql_where=(system_halt_event.c.cleared_at.is_(None)),
)
