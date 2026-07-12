"""Structural tests for infrastructure/db/tables/ that need no database at all.

These check properties of the `sa.Table` objects themselves — foreign key
sets, unique constraints, primary key columns — which SQLAlchemy exposes
as plain Python attributes without ever opening a connection. They run
unconditionally (no Docker required) and complement
`test_db_migrations.py`'s live, testcontainers-backed verification of the
same acceptance criteria against a real, running database.
"""

from __future__ import annotations

import sqlalchemy as sa

from infrastructure.db.tables import metadata


def _table(name: str) -> sa.Table:
    return metadata.tables[name]


def test_exactly_25_tables_are_registered() -> None:
    """23 DATABASE.md entities + trade_tick (T-P0-11, see market_data.py's
    docstring) + candle_backfill_checkpoint (T-P1-04, see backfill.py's
    docstring)."""
    assert len(metadata.tables) == 25


def test_risk_limit_config_is_permanently_excluded() -> None:
    """DATABASE.md: "no RiskLimitConfig table... a quiet architectural
    reversal" — must never exist, under any spelling."""
    names = {name.lower() for name in metadata.tables}
    assert "risk_limit_config" not in names
    assert "risklimitconfig" not in names


def test_all_23_database_md_entities_plus_trade_tick_and_checkpoint_are_present() -> None:
    expected = {
        "venue",
        "instrument",
        "universe_snapshot",
        "candle",
        "trade_tick",
        "strategy",
        "strategy_instance",
        "signal",
        "order_intent",
        "risk_decision",
        "system_halt_event",
        "order",
        "order_event",
        "fill",
        "reconciliation_check",
        "account",
        "position",
        "ledger_entry",
        "equity_snapshot",
        "event_log",
        "dataset_version",
        "backtest_run",
        "backtest_trade",
        "backtest_metrics",
        "candle_backfill_checkpoint",
    }
    assert set(metadata.tables.keys()) == expected


def test_event_log_has_no_foreign_key_columns() -> None:
    """DATABASE.md: "FK: none, by design." TASKS.md T-P0-11 acceptance
    criterion: "EventLog has no FK columns.\""""
    event_log = _table("event_log")
    assert event_log.foreign_keys == set()
    assert event_log.foreign_key_constraints == set()


def test_order_has_unique_constraint_on_venue_id_and_client_order_id() -> None:
    order = _table("order")
    unique_constraints = [c for c in order.constraints if isinstance(c, sa.UniqueConstraint)]
    expected_columns = {"venue_id", "client_order_id"}
    matching = [
        c for c in unique_constraints if {col.name for col in c.columns} == expected_columns
    ]
    assert len(matching) == 1, "expected exactly one UNIQUE(venue_id, client_order_id) on order"


def test_fill_has_unique_constraint_on_venue_id_and_venue_fill_id() -> None:
    fill = _table("fill")
    unique_constraints = [c for c in fill.constraints if isinstance(c, sa.UniqueConstraint)]
    expected_columns = {"venue_id", "venue_fill_id"}
    matching = [
        c for c in unique_constraints if {col.name for col in c.columns} == expected_columns
    ]
    assert len(matching) == 1, "expected exactly one UNIQUE(venue_id, venue_fill_id) on fill"


def test_candle_primary_key_includes_its_hypertable_partition_column() -> None:
    """TimescaleDB requires the partition column in every unique/PK
    constraint on a hypertable — `open_time` must be part of candle's PK."""
    candle = _table("candle")
    pk_columns = {col.name for col in candle.primary_key.columns}
    assert pk_columns == {"instrument_id", "interval", "open_time"}


def test_trade_tick_primary_key_includes_its_hypertable_partition_column() -> None:
    trade_tick = _table("trade_tick")
    pk_columns = {col.name for col in trade_tick.primary_key.columns}
    assert pk_columns == {"instrument_id", "ts", "venue_trade_id"}


def test_equity_snapshot_primary_key_includes_its_hypertable_partition_column() -> None:
    """Documented deviation from DATABASE.md's bare "PK: id" — see
    portfolio.py's module docstring for why."""
    equity_snapshot = _table("equity_snapshot")
    pk_columns = {col.name for col in equity_snapshot.primary_key.columns}
    assert pk_columns == {"id", "ts"}


def test_candle_backfill_checkpoint_primary_key_is_its_natural_request_key() -> None:
    """T-P1-04: one checkpoint row per distinct backfill request — see
    backfill.py's module docstring."""
    checkpoint = _table("candle_backfill_checkpoint")
    pk_columns = {col.name for col in checkpoint.primary_key.columns}
    assert pk_columns == {"venue_id", "instrument_id", "interval", "range_start", "range_end"}
