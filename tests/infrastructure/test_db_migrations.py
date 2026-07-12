"""Live database tests for the T-P0-11 baseline schema migration.

Spins up a real `timescale/timescaledb` container via `testcontainers`
(ARCHITECTURE.md §3.9: "Integration | ≥80% | pytest + testcontainers |
Real Postgres, real Redis") and runs the actual Alembic migration against
it — no mocking of SQLAlchemy, psycopg, or Alembic itself. Every test in
this module is skipped, not failed, when Docker isn't available (checked
once, at collection time), so `make test` stays green in environments
without a Docker daemon while still providing full, real verification
wherever Docker *is* present.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

try:
    from testcontainers.postgres import PostgresContainer

    _container_probe = PostgresContainer(
        image="timescale/timescaledb:2.17.2-pg16", driver="psycopg"
    )
    # get_docker_client() only builds a client bound to the docker
    # socket/API — it never itself talks to the daemon, so it can succeed
    # even where a docker socket/context is present but the daemon isn't
    # actually reachable (observed on GitHub Actions runners: the client
    # constructs fine, then the first real container operation fails with
    # "connection refused" against a PostgreSQL that was never started).
    # `.ping()` performs one real round trip to the daemon and raises if
    # it can't be reached — that is the actual "is Docker usable" check.
    _container_probe.get_docker_client().client.ping()
    _DOCKER_AVAILABLE = True
except Exception:  # noqa: BLE001 — any failure here just means "skip this module"
    _DOCKER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _DOCKER_AVAILABLE, reason="Docker is not available in this environment"
)

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def db_engine() -> Iterator[sa.Engine]:
    """A running TimescaleDB container with `alembic upgrade head` already applied."""
    with PostgresContainer(
        image="timescale/timescaledb:2.17.2-pg16", driver="psycopg"
    ) as postgres:
        url = postgres.get_connection_url()

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")

        engine = sa.create_engine(url)
        yield engine
        engine.dispose()


def _insert_minimal_order_parents(conn: sa.Connection) -> dict[str, uuid.UUID]:
    """Insert the FK parent chain an Order row needs: venue, instrument,
    strategy, strategy_instance, signal, order_intent, risk_decision."""
    venue_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO venue (id, name, venue_type, api_base_url, capabilities, "
            "fee_schedule, status) VALUES (:id, :name, 'cex', 'https://x', '{}', '{}', 'active')"
        ),
        {"id": venue_id, "name": f"venue-{venue_id}"},
    )

    instrument_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO instrument (id, venue_id, symbol, asset_class, base_currency, "
            "quote_currency, tick_size, lot_size, min_notional, status, listed_at, updated_at) "
            "VALUES (:id, :venue_id, 'BTCUSDT', 'spot', 'BTC', 'USDT', 0.1, 0.001, 10, "
            "'trading', now(), now())"
        ),
        {"id": instrument_id, "venue_id": venue_id},
    )

    strategy_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO strategy (id, name, code_hash, params_schema) "
            "VALUES (:id, :name, :hash, '{}')"
        ),
        {"id": strategy_id, "name": f"strat-{strategy_id}", "hash": "abc123"},
    )

    strategy_instance_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO strategy_instance (id, strategy_id, params, status, "
            "allocated_capital, capacity_usd, started_at) "
            "VALUES (:id, :strategy_id, '{}', 'running', 1000, 1000, now())"
        ),
        {"id": strategy_instance_id, "strategy_id": strategy_id},
    )

    order_intent_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO order_intent (id, strategy_instance_id, instrument_id, side, "
            "target_qty, order_type) VALUES (:id, :sid, :iid, 'buy', 1, 'market')"
        ),
        {"id": order_intent_id, "sid": strategy_instance_id, "iid": instrument_id},
    )

    risk_decision_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO risk_decision (id, order_intent_id, ts, approved, "
            "rules_evaluated, limits_config_version) "
            "VALUES (:id, :oiid, now(), true, '[]', 'v1')"
        ),
        {"id": risk_decision_id, "oiid": order_intent_id},
    )

    return {
        "venue_id": venue_id,
        "instrument_id": instrument_id,
        "strategy_instance_id": strategy_instance_id,
        "risk_decision_id": risk_decision_id,
    }


def _insert_order(
    conn: sa.Connection, parents: dict[str, uuid.UUID], client_order_id: str
) -> uuid.UUID:
    order_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO \"order\" (id, client_order_id, venue_id, instrument_id, "
            "strategy_instance_id, risk_decision_id, side, order_type, qty, status, tif, "
            "updated_at) VALUES (:id, :coid, :venue_id, :instrument_id, "
            ":strategy_instance_id, :risk_decision_id, 'buy', 'market', 1, 'pending_new', "
            "'gtc', now())"
        ),
        {"id": order_id, "coid": client_order_id, **parents},
    )
    return order_id


def test_alembic_upgrade_head_runs_against_a_clean_database_with_zero_errors(
    db_engine: sa.Engine,
) -> None:
    with db_engine.connect() as conn:
        table_count = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name != 'alembic_version'"
            )
        ).scalar_one()
    assert table_count == 24


def test_order_venue_id_client_order_id_unique_violation(db_engine: sa.Engine) -> None:
    with db_engine.connect() as conn:
        parents = _insert_minimal_order_parents(conn)
        _insert_order(conn, parents, "dup-client-order-id")
        conn.commit()

        with pytest.raises(IntegrityError, match="UniqueViolation|unique constraint"):
            _insert_order(conn, parents, "dup-client-order-id")
            conn.commit()
        conn.rollback()


def test_fill_venue_id_venue_fill_id_unique_violation(db_engine: sa.Engine) -> None:
    with db_engine.connect() as conn:
        parents = _insert_minimal_order_parents(conn)
        order_id = _insert_order(conn, parents, "order-for-fill-test")
        conn.commit()

        def insert_fill() -> None:
            conn.execute(
                text(
                    "INSERT INTO fill (id, order_id, venue_id, venue_fill_id, qty, price, "
                    "fee, fee_currency, is_maker, ts) VALUES (:id, :order_id, :venue_id, "
                    "'dup-venue-fill-id', 1, 100, 0.1, 'USDT', false, now())"
                ),
                {"id": uuid.uuid4(), "order_id": order_id, "venue_id": parents["venue_id"]},
            )

        insert_fill()
        conn.commit()

        with pytest.raises(IntegrityError, match="UniqueViolation|unique constraint"):
            insert_fill()
            conn.commit()
        conn.rollback()


@pytest.mark.parametrize(
    ("table", "insert_sql", "params_factory"),
    [
        (
            "candle",
            "INSERT INTO candle (instrument_id, interval, open_time, open, high, low, "
            "close, volume, trade_count, is_closed, source) VALUES (:instrument_id, "
            "'1m', now(), 100, 100, 100, 100, 1, 1, true, 'test')",
            None,
        ),
        (
            "trade_tick",
            "INSERT INTO trade_tick (instrument_id, ts, venue_trade_id, price, qty, side) "
            "VALUES (:instrument_id, now(), :vtid, 100, 1, 'buy')",
            None,
        ),
    ],
)
def test_candle_and_trade_tick_are_timescaledb_hypertables(
    db_engine: sa.Engine, table: str, insert_sql: str, params_factory: None
) -> None:
    with db_engine.connect() as conn:
        parents = _insert_minimal_order_parents(conn)
        conn.execute(
            text(insert_sql), {"instrument_id": parents["instrument_id"], "vtid": str(uuid.uuid4())}
        )
        conn.commit()

        schema = conn.execute(
            text(f"SELECT tableoid::regnamespace::text FROM {table} LIMIT 1")  # noqa: S608
        ).scalar_one()
    assert schema == "_timescaledb_internal"


def test_equity_snapshot_is_a_timescaledb_hypertable(db_engine: sa.Engine) -> None:
    with db_engine.connect() as conn:
        venue_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO venue (id, name, venue_type, api_base_url, capabilities, "
                "fee_schedule, status) VALUES (:id, :name, 'cex', 'https://x', '{}', '{}', "
                "'active')"
            ),
            {"id": venue_id, "name": f"venue-{venue_id}"},
        )
        account_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO account (id, name, venue_id, base_currency) "
                "VALUES (:id, :name, :venue_id, 'USDT')"
            ),
            {"id": account_id, "name": f"acct-{account_id}", "venue_id": venue_id},
        )
        conn.execute(
            text(
                "INSERT INTO equity_snapshot (id, account_id, ts, cash, positions_value, "
                "total_equity, drawdown_pct) VALUES (:id, :account_id, now(), 1000, 0, "
                "1000, 0)"
            ),
            {"id": uuid.uuid4(), "account_id": account_id},
        )
        conn.commit()

        schema = conn.execute(
            text("SELECT tableoid::regnamespace::text FROM equity_snapshot LIMIT 1")
        ).scalar_one()
    assert schema == "_timescaledb_internal"


def test_alembic_downgrade_reverses_cleanly(db_engine: sa.Engine) -> None:
    """Deliberately the last test defined in this module: pytest's
    documented default is to collect and run tests in source order within
    a file, and this one drops every table the shared, module-scoped
    `db_engine` fixture provides — every test above depends on that
    schema already existing. Restores it afterward as a safety net."""
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", str(db_engine.url))

    command.downgrade(config, "-1")

    with db_engine.connect() as conn:
        table_count = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name != 'alembic_version'"
            )
        ).scalar_one()
    assert table_count == 0

    # Restore schema for any tests that might run after this in the same module.
    command.upgrade(config, "head")
