"""Group A · Reference & Market Data (part 2): Candle, TradeTick.

DATABASE.md §A entity 4 (Candle). `TradeTick` is *not* one of DATABASE.md's
23 entities — its own "Out of Scope for MVP" table explicitly defers it
("No microstructure/execution-cost strategy exists yet to consume it").
TASKS.md T-P0-11 nonetheless names "TradeTick (hypertable)" twice — once
in its own entity list, once in an acceptance criterion ("Candle,
TradeTick, and EquitySnapshot are TimescaleDB hypertables") — so excluding
it would leave that criterion untestable. This module includes it using
the exact field list ARCHITECTURE.md §7.2 gives: "instrument_id, ts,
price, qty, side, venue_trade_id | Hypertable. Highest volume." — the one
place a concrete shape for it exists at all. `venue_trade_id` is folded
into the primary key (mirroring Candle's own `(instrument_id, interval,
open_time)` idempotent-upsert design) so a duplicate backfill of the same
trade is a harmless no-op rather than a new row.

`interval` is a plain `sa.String`, not a Postgres enum, unlike every other
enum-typed column in this schema — deliberately: DATABASE.md writes it as
"enum{1m,5m,1h,1d,...}", and the trailing "..." is the one place in the
whole document signaling an open-ended, not a closed, set of values.
"""

from __future__ import annotations

import sqlalchemy as sa

from infrastructure.db.tables._common import ORDER_SIDE_ENUM, created_at_column
from infrastructure.db.tables._metadata import metadata

candle = sa.Table(
    "candle",
    metadata,
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column("interval", sa.String, nullable=False),
    sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column("open", sa.Numeric, nullable=False),
    sa.Column("high", sa.Numeric, nullable=False),
    sa.Column("low", sa.Numeric, nullable=False),
    sa.Column("close", sa.Numeric, nullable=False),
    sa.Column("volume", sa.Numeric, nullable=False),
    sa.Column("trade_count", sa.Integer, nullable=False),
    sa.Column("is_closed", sa.Boolean, nullable=False),
    sa.Column("source", sa.String, nullable=False),
    created_at_column("inserted_at"),
    sa.PrimaryKeyConstraint("instrument_id", "interval", "open_time", name="pk_candle"),
    sa.CheckConstraint("high >= greatest(open, close)", name="ck_candle_high_vs_open_close"),
    sa.CheckConstraint("low <= least(open, close)", name="ck_candle_low_vs_open_close"),
    sa.CheckConstraint("high >= low", name="ck_candle_high_vs_low"),
    sa.CheckConstraint("volume >= 0", name="ck_candle_volume_nonneg"),
)

sa.Index("ix_candle_instrument_open_time", candle.c.instrument_id, candle.c.open_time.desc())

trade_tick = sa.Table(
    "trade_tick",
    metadata,
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("venue_trade_id", sa.String, nullable=False),
    sa.Column("price", sa.Numeric, nullable=False),
    sa.Column("qty", sa.Numeric, nullable=False),
    sa.Column("side", ORDER_SIDE_ENUM, nullable=False),
    sa.PrimaryKeyConstraint("instrument_id", "ts", "venue_trade_id", name="pk_trade_tick"),
    sa.CheckConstraint("price > 0", name="ck_trade_tick_price_positive"),
    sa.CheckConstraint("qty > 0", name="ck_trade_tick_qty_positive"),
)
