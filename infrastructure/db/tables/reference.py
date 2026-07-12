"""Group A · Reference & Market Data (part 1): Venue, Instrument, UniverseSnapshot.

DATABASE.md §A, entities 1–3.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from infrastructure.db.tables._common import created_at_column, uuid_pk
from infrastructure.db.tables._metadata import metadata

venue = sa.Table(
    "venue",
    metadata,
    uuid_pk(),
    sa.Column("name", sa.String, nullable=False, unique=True),
    sa.Column("venue_type", sa.Enum("cex", "dex", "broker", name="venue_type"), nullable=False),
    sa.Column("api_base_url", sa.String, nullable=False),
    sa.Column("capabilities", JSONB, nullable=False),
    sa.Column("fee_schedule", JSONB, nullable=False),
    sa.Column("status", sa.Enum("active", "disabled", name="venue_status"), nullable=False),
    created_at_column(),
)

instrument = sa.Table(
    "instrument",
    metadata,
    uuid_pk(),
    sa.Column("venue_id", sa.Uuid(as_uuid=True), sa.ForeignKey("venue.id"), nullable=False),
    sa.Column("symbol", sa.String, nullable=False),
    sa.Column("asset_class", sa.Enum("spot", name="asset_class"), nullable=False),
    sa.Column("base_currency", sa.String, nullable=False),
    sa.Column("quote_currency", sa.String, nullable=False),
    sa.Column("tick_size", sa.Numeric, nullable=False),
    sa.Column("lot_size", sa.Numeric, nullable=False),
    sa.Column("min_notional", sa.Numeric, nullable=False),
    sa.Column("max_order_size", sa.Numeric, nullable=True),
    sa.Column(
        "status", sa.Enum("trading", "halted", "delisted", name="instrument_status"), nullable=False
    ),
    sa.Column("listed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delisted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("venue_id", "symbol", name="uq_instrument_venue_id_symbol"),
    sa.CheckConstraint("tick_size > 0", name="ck_instrument_tick_size_positive"),
    sa.CheckConstraint("lot_size > 0", name="ck_instrument_lot_size_positive"),
    sa.CheckConstraint("min_notional > 0", name="ck_instrument_min_notional_positive"),
)

sa.Index(
    "ix_instrument_status_trading",
    instrument.c.status,
    postgresql_where=(instrument.c.status == "trading"),
)

universe_snapshot = sa.Table(
    "universe_snapshot",
    metadata,
    uuid_pk(),
    sa.Column("snapshot_date", sa.Date, nullable=False),
    sa.Column("venue_id", sa.Uuid(as_uuid=True), sa.ForeignKey("venue.id"), nullable=False),
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column("is_tradeable", sa.Boolean, nullable=False),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "snapshot_date",
        "venue_id",
        "instrument_id",
        name="uq_universe_snapshot_date_venue_instrument",
    ),
)

sa.Index(
    "ix_universe_snapshot_date_venue",
    universe_snapshot.c.snapshot_date,
    universe_snapshot.c.venue_id,
)
