"""Live database tests for the Universe Snapshot Job (TASKS.md T-P1-03).

All four of T-P1-03's acceptance criteria are statements about database
state across multiple runs/dates, so — per the same reasoning already
established for T-P1-02 — this suite spins up a real
`timescale/timescaledb` container via `testcontainers` (the identical
two-layer Docker-usability strategy used by
`tests/infrastructure/test_db_migrations.py` and
`tests/infrastructure/jobs/test_reference_data_importer_integration.py`),
applies the real baseline migration, and exercises
`capture_universe_snapshot` / `query_tradeable_instruments` against it —
real Postgres, real `instrument`/`universe_snapshot` rows, no mocking.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable — see test_db_migrations.py's module docstring for the
full rationale behind the two-layer strategy duplicated below.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.jobs.universe_snapshot_job import (
    capture_universe_snapshot,
    query_tradeable_instruments,
)

try:
    from testcontainers.postgres import PostgresContainer

    _container_probe = PostgresContainer(
        image="timescale/timescaledb:2.17.2-pg16", driver="psycopg"
    )
    _container_probe.get_docker_client().client.ping()
    _DOCKER_AVAILABLE = True
except Exception:  # noqa: BLE001 — any failure here just means "skip this module"
    _DOCKER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _DOCKER_AVAILABLE, reason="Docker is not available in this environment"
)

REPO_ROOT = Path(__file__).resolve().parents[3]

_CONNECTIVITY_CHECK_ATTEMPTS = 5
_CONNECTIVITY_CHECK_DELAY_SECONDS = 1.0


@pytest.fixture(scope="module")
def db_engine() -> Iterator[sa.Engine]:
    """A running TimescaleDB container with `alembic upgrade head` already
    applied. See test_db_migrations.py::db_engine for why this performs a
    real host-side connectivity check rather than trusting the
    container's own internal readiness probe."""
    try:
        container = PostgresContainer(
            image="timescale/timescaledb:2.17.2-pg16", driver="psycopg"
        ).start()
    except Exception as exc:  # noqa: BLE001 — unusable environment, not a test failure
        pytest.skip(f"TimescaleDB container could not be started: {exc!r}")

    url = container.get_connection_url()
    engine = sa.create_engine(url)

    unreachable: Exception | None = None
    for attempt in range(_CONNECTIVITY_CHECK_ATTEMPTS):
        try:
            with engine.connect():
                pass
            unreachable = None
            break
        except Exception as exc:  # noqa: BLE001 — see docstring
            unreachable = exc
            if attempt + 1 < _CONNECTIVITY_CHECK_ATTEMPTS:
                time.sleep(_CONNECTIVITY_CHECK_DELAY_SECONDS)

    if unreachable is not None:
        engine.dispose()
        with contextlib.suppress(Exception):
            container.stop()
        pytest.skip(
            "TimescaleDB container started but its host-mapped port is not "
            f"reachable from the test process: {unreachable!r}"
        )

    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
    except Exception as exc:  # noqa: BLE001 — see docstring
        engine.dispose()
        with contextlib.suppress(Exception):
            container.stop()
        pytest.skip(f"alembic upgrade against the TimescaleDB container failed: {exc!r}")

    yield engine

    engine.dispose()
    container.stop()


@pytest.fixture
def conn(db_engine: sa.Engine) -> Iterator[sa.Connection]:
    """One connection per test, with its own transaction rolled back at the
    end so tests don't leak venue/instrument/snapshot rows into each other."""
    with db_engine.connect() as connection:
        yield connection
        connection.rollback()


def _insert_venue(conn: sa.Connection, *, name: str = "binance") -> uuid.UUID:
    venue_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO venue (id, name, venue_type, api_base_url, capabilities, "
            "fee_schedule, status) VALUES (:id, :name, 'cex', 'https://api.binance.com', "
            "'{}', '{}', 'active')"
        ),
        {"id": venue_id, "name": name},
    )
    return venue_id


def _insert_instrument(
    conn: sa.Connection,
    *,
    venue_id: uuid.UUID,
    symbol: str = "BTCUSDT",
    status: str = "trading",
) -> uuid.UUID:
    instrument_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO instrument (id, venue_id, symbol, asset_class, base_currency, "
            "quote_currency, tick_size, lot_size, min_notional, status, listed_at, updated_at) "
            "VALUES (:id, :venue_id, :symbol, 'spot', 'BTC', 'USDT', 0.01, 0.00001, 10, "
            ":status, now(), now())"
        ),
        {"id": instrument_id, "venue_id": venue_id, "symbol": symbol, "status": status},
    )
    return instrument_id


def _set_instrument_status(conn: sa.Connection, instrument_id: uuid.UUID, status: str) -> None:
    conn.execute(
        text("UPDATE instrument SET status = :status, updated_at = now() WHERE id = :id"),
        {"status": status, "id": instrument_id},
    )


def test_capture_inserts_one_row_per_instrument_for_the_venue(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")
    _insert_instrument(conn, venue_id=venue_id, symbol="ETHUSDT")

    result = capture_universe_snapshot(
        conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 1)
    )

    assert result.captured == 2
    assert result.already_captured == 0

    count = conn.execute(
        text(
            "SELECT count(*) FROM universe_snapshot "
            "WHERE venue_id = :venue_id AND snapshot_date = :d"
        ),
        {"venue_id": venue_id, "d": date(2026, 1, 1)},
    ).scalar_one()
    assert count == 2


def test_running_on_consecutive_days_produces_two_rows_per_instrument(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-03 acceptance criterion, verbatim: "Running the job
    on consecutive days produces two rows per instrument (one per date),
    not one.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id)

    capture_universe_snapshot(conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 1))
    capture_universe_snapshot(conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 2))

    count = conn.execute(
        text("SELECT count(*) FROM universe_snapshot WHERE instrument_id = :id"),
        {"id": instrument_id},
    ).scalar_one()
    assert count == 2

    dates = sorted(
        row[0]
        for row in conn.execute(
            text("SELECT snapshot_date FROM universe_snapshot WHERE instrument_id = :id"),
            {"id": instrument_id},
        )
    )
    assert dates == [date(2026, 1, 1), date(2026, 1, 2)]


def test_running_twice_on_the_same_day_is_idempotent_and_append_only(
    conn: sa.Connection,
) -> None:
    """DATABASE.md's UniverseSnapshot constraint: "append-only, no
    update/delete." A second capture for a date already captured must
    change nothing, not overwrite it."""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, status="trading")

    first = capture_universe_snapshot(conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 1))
    assert first.captured == 1

    # A status flip *after* the first capture must not retroactively
    # change what was already recorded for that date.
    _set_instrument_status(conn, instrument_id, "halted")

    second = capture_universe_snapshot(
        conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 1)
    )
    assert second.captured == 0
    assert second.already_captured == 1

    row = conn.execute(
        text(
            "SELECT is_tradeable FROM universe_snapshot "
            "WHERE instrument_id = :id AND snapshot_date = :d"
        ),
        {"id": instrument_id, "d": date(2026, 1, 1)},
    ).one()
    assert row.is_tradeable is True  # untouched by the later status flip

    count = conn.execute(
        text("SELECT count(*) FROM universe_snapshot WHERE instrument_id = :id"),
        {"id": instrument_id},
    ).scalar_one()
    assert count == 1  # no duplicate row


def test_delisting_between_two_runs_flips_is_tradeable_on_the_second_snapshot(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-03 acceptance criterion, verbatim: "An instrument
    delisted between two runs has `is_tradeable = false` on the second
    snapshot.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, status="trading")

    capture_universe_snapshot(conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 1))
    _set_instrument_status(conn, instrument_id, "delisted")
    capture_universe_snapshot(conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 2))

    first_day = conn.execute(
        text(
            "SELECT is_tradeable FROM universe_snapshot "
            "WHERE instrument_id = :id AND snapshot_date = :d"
        ),
        {"id": instrument_id, "d": date(2026, 1, 1)},
    ).one()
    second_day = conn.execute(
        text(
            "SELECT is_tradeable FROM universe_snapshot "
            "WHERE instrument_id = :id AND snapshot_date = :d"
        ),
        {"id": instrument_id, "d": date(2026, 1, 2)},
    ).one()

    assert first_day.is_tradeable is True
    assert second_day.is_tradeable is False


def test_query_tradeable_instruments_returns_the_correct_set_for_a_date(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-03 acceptance criterion, verbatim: "A query `SELECT
    instrument_id FROM universe_snapshot WHERE date = $1 AND is_tradeable
    = true` returns the correct set for any historical date.\""""
    venue_id = _insert_venue(conn)
    tradeable_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT", status="trading")
    halted_id = _insert_instrument(conn, venue_id=venue_id, symbol="XYZUSDT", status="halted")

    capture_universe_snapshot(conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 1))

    tradeable = set(query_tradeable_instruments(conn, date(2026, 1, 1)))
    assert tradeable_id in tradeable
    assert halted_id not in tradeable


def test_point_in_time_query_returns_different_sets_across_two_dates_when_delisted(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-03's named integration-test requirement, verbatim:
    "Integration test verifies the point-in-time query returns different
    sets for two different dates when one instrument was delisted.\""""
    venue_id = _insert_venue(conn)
    stays_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT", status="trading")
    delisted_id = _insert_instrument(conn, venue_id=venue_id, symbol="XYZUSDT", status="trading")

    day1 = date(2026, 1, 1)
    day2 = date(2026, 1, 2)

    capture_universe_snapshot(conn=conn, venue_id=venue_id, snapshot_date=day1)
    _set_instrument_status(conn, delisted_id, "delisted")
    capture_universe_snapshot(conn=conn, venue_id=venue_id, snapshot_date=day2)

    day1_tradeable = set(query_tradeable_instruments(conn, day1))
    day2_tradeable = set(query_tradeable_instruments(conn, day2))

    assert day1_tradeable == {stays_id, delisted_id}
    assert day2_tradeable == {stays_id}
    assert day1_tradeable != day2_tradeable


def test_capture_with_no_instruments_for_the_venue_is_a_no_op(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    result = capture_universe_snapshot(
        conn=conn, venue_id=venue_id, snapshot_date=date(2026, 1, 1)
    )
    assert result.captured == 0
    assert result.already_captured == 0


def test_snapshot_date_defaults_to_now_when_not_given(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    _insert_instrument(conn, venue_id=venue_id)
    fixed_now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

    result = capture_universe_snapshot(conn=conn, venue_id=venue_id, now=lambda: fixed_now)

    assert result.snapshot_date == date(2026, 3, 15)
