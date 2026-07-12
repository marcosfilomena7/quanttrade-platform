"""Group D · Execution: Order, OrderEvent, Fill, ReconciliationCheck.

DATABASE.md §D, entities 11–14. Carries the two most important indexes in
the schema: `UNIQUE(order.venue_id, order.client_order_id)` — makes
double-submission a database-level impossibility — and
`UNIQUE(fill.venue_id, fill.venue_fill_id)` — the exactly-once fill
guarantee. Both are TASKS.md T-P0-11 acceptance criteria, not incidental.
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

# DATABASE.md's Order.status matches domain/order.py's OrderStatus exactly
# (T-P0-05): pending_new, sent, acked, partially_filled, filled,
# pending_cancel, canceled, rejected, expired, unknown.
_OPEN_ORDER_STATUSES = (
    "pending_new",
    "sent",
    "acked",
    "partially_filled",
    "pending_cancel",
    "unknown",
)

order = sa.Table(
    "order",
    metadata,
    uuid_pk(),
    sa.Column("client_order_id", sa.String, nullable=False),
    sa.Column("venue_order_id", sa.String, nullable=True),
    sa.Column("venue_id", sa.Uuid(as_uuid=True), sa.ForeignKey("venue.id"), nullable=False),
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column(
        "strategy_instance_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("strategy_instance.id"),
        nullable=False,
    ),
    sa.Column(
        "risk_decision_id", sa.Uuid(as_uuid=True), sa.ForeignKey("risk_decision.id"), nullable=False
    ),
    sa.Column("side", ORDER_SIDE_ENUM, nullable=False),
    sa.Column("order_type", ORDER_TYPE_ENUM, nullable=False),
    sa.Column("qty", sa.Numeric, nullable=False),
    sa.Column("limit_price", sa.Numeric, nullable=True),
    sa.Column("filled_qty", sa.Numeric, nullable=False, server_default=sa.text("0")),
    sa.Column("avg_fill_price", sa.Numeric, nullable=True),
    sa.Column(
        "status",
        sa.Enum(
            "pending_new",
            "sent",
            "acked",
            "partially_filled",
            "filled",
            "pending_cancel",
            "canceled",
            "rejected",
            "expired",
            "unknown",
            name="order_status",
        ),
        nullable=False,
    ),
    sa.Column("tif", sa.Enum("gtc", "ioc", "fok", name="order_tif"), nullable=False),
    sa.Column("parent_order_id", sa.Uuid(as_uuid=True), sa.ForeignKey("order.id"), nullable=True),
    created_at_column(),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("venue_id", "client_order_id", name="uq_order_venue_id_client_order_id"),
    sa.CheckConstraint("filled_qty <= qty", name="ck_order_filled_qty_le_qty"),
    sa.CheckConstraint("filled_qty >= 0", name="ck_order_filled_qty_nonneg"),
)

sa.Index(
    "ix_order_status_open",
    order.c.status,
    postgresql_where=(order.c.status.in_(_OPEN_ORDER_STATUSES)),
)
sa.Index(
    "ix_order_strategy_instance_created_at", order.c.strategy_instance_id, order.c.created_at.desc()
)

order_event = sa.Table(
    "order_event",
    metadata,
    uuid_pk(),
    sa.Column("order_id", sa.Uuid(as_uuid=True), sa.ForeignKey("order.id"), nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column(
        "event_type",
        sa.Enum(
            "created",
            "sent",
            "acked",
            "rejected",
            "partially_filled",
            "filled",
            "cancel_requested",
            "canceled",
            "expired",
            "adopted",
            name="order_event_type",
        ),
        nullable=False,
    ),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("order_id", "seq", name="uq_order_event_order_id_seq"),
)

fill = sa.Table(
    "fill",
    metadata,
    uuid_pk(),
    sa.Column("order_id", sa.Uuid(as_uuid=True), sa.ForeignKey("order.id"), nullable=False),
    # Denormalized from Order, not itself a foreign key (DATABASE.md lists
    # only order_id -> Order.id under Fill's FK section) — its only purpose
    # is making UNIQUE(venue_id, venue_fill_id) declarable at all.
    sa.Column("venue_id", sa.Uuid(as_uuid=True), nullable=False),
    sa.Column("venue_fill_id", sa.String, nullable=False),
    sa.Column("qty", sa.Numeric, nullable=False),
    sa.Column("price", sa.Numeric, nullable=False),
    sa.Column("fee", sa.Numeric, nullable=False),
    sa.Column("fee_currency", sa.String, nullable=False),
    sa.Column("is_maker", sa.Boolean, nullable=False),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("venue_id", "venue_fill_id", name="uq_fill_venue_id_venue_fill_id"),
    sa.CheckConstraint("qty > 0", name="ck_fill_qty_positive"),
    sa.CheckConstraint("price > 0", name="ck_fill_price_positive"),
)

sa.Index("ix_fill_order_id", fill.c.order_id)

reconciliation_check = sa.Table(
    "reconciliation_check",
    metadata,
    uuid_pk(),
    sa.Column("account_id", sa.Uuid(as_uuid=True), sa.ForeignKey("account.id"), nullable=False),
    sa.Column("venue_id", sa.Uuid(as_uuid=True), sa.ForeignKey("venue.id"), nullable=False),
    sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("drift_detected", sa.Boolean, nullable=False),
    sa.Column("discrepancies", JSONB, nullable=False),
    sa.Column(
        "resolution",
        sa.Enum(
            "none_needed", "auto_adopted", "halted_pending_review", name="reconciliation_resolution"
        ),
        nullable=False,
    ),
)

sa.Index("ix_reconciliation_check_ran_at", reconciliation_check.c.ran_at.desc())
sa.Index(
    "ix_reconciliation_check_drift_detected",
    reconciliation_check.c.drift_detected,
    postgresql_where=(reconciliation_check.c.drift_detected.is_(True)),
)
