"""Live database test for load_candle_series_from_dataset_version
(TASKS.md T-P2-03).

T-P2-03's third acceptance criterion is a statement about Postgres-
loaded data, so — per the same reasoning already established for
T-P1-02 through T-P2-01 — this suite spins up a real
`timescale/timescaledb` container via `testcontainers` (the identical
two-layer Docker-usability strategy duplicated across this repo's
integration tests) and exercises the real `load_candle_series_from_
dataset_version` (backed by `create_dataset_version`, T-P1-12) against
it — real Postgres rows, no mocking of SQLAlchemy or Alembic.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable — see test_db_migrations.py's module docstring for the
full rationale behind the two-layer strategy duplicated below.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.backtest.dataset_version_repository import create_dataset_version, hash_row
from infrastructure.backtest.historical_feed import (
    FeedExhausted,
    HistoricalFeed,
    load_candle_series_from_dataset_version,
)
from infrastructure.db.tables.market_data import candle as candle_table

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


def _insert_instrument(conn: sa.Connection, *, venue_id: uuid.UUID, symbol: str) -> uuid.UUID:
    instrument_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO instrument (id, venue_id, symbol, asset_class, base_currency, "
            "quote_currency, tick_size, lot_size, min_notional, status, listed_at, updated_at) "
            "VALUES (:id, :venue_id, :symbol, 'spot', 'BTC', 'USDT', 0.01, 0.00001, 10, "
            "'trading', now(), now())"
        ),
        {"id": instrument_id, "venue_id": venue_id, "symbol": symbol},
    )
    return instrument_id


def _insert_candles(
    conn: sa.Connection, *, instrument_id: uuid.UUID, interval: str, open_times: list[datetime]
) -> None:
    conn.execute(
        sa.insert(candle_table),
        [
            {
                "instrument_id": instrument_id,
                "interval": interval,
                "open_time": ot,
                "open": Decimal("100.00"),
                "high": Decimal("101.00"),
                "low": Decimal("99.00"),
                "close": Decimal("100.50"),
                "volume": Decimal("10.5"),
                "trade_count": 5,
                "is_closed": True,
                "source": "test_fixture",
            }
            for ot in open_times
        ],
    )


def test_loading_a_dataset_version_with_a_known_content_hash_produces_events_deterministically(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P2-03 acceptance criterion, verbatim: "Loading a
    DatasetVersion with a known content hash produces events
    deterministically.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    base = datetime(2026, 1, 1, tzinfo=UTC)
    one_minute_open_times = [base + i * timedelta(minutes=1) for i in range(5)]
    _insert_candles(
        conn, instrument_id=instrument_id, interval="1m", open_times=one_minute_open_times
    )

    dataset_version = create_dataset_version(
        conn,
        symbol_set=[instrument_id],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 2),
        row_count=5,
        sample_hashes=[hash_row({"open_time": ot.isoformat()}) for ot in one_minute_open_times],
    )

    first_load = load_candle_series_from_dataset_version(
        conn, dataset_version=dataset_version, intervals=["1m"]
    )
    second_load = load_candle_series_from_dataset_version(
        conn, dataset_version=dataset_version, intervals=["1m"]
    )

    assert first_load.keys() == second_load.keys() == {("BTCUSDT", "1m")}
    assert first_load[("BTCUSDT", "1m")] == second_load[("BTCUSDT", "1m")]
    assert [c.open_time for c in first_load[("BTCUSDT", "1m")]] == one_minute_open_times

    # The loaded series drives HistoricalFeed identically both times.
    async def drain(series: dict[tuple[str, str], list[object]]) -> list[object]:
        feed = HistoricalFeed(series)  # type: ignore[arg-type]
        events = []
        while True:
            try:
                events.append(await feed.next_event())
            except FeedExhausted:
                return events

    first_events = asyncio.run(drain(first_load))
    second_events = asyncio.run(drain(second_load))
    assert first_events == second_events
    assert len(first_events) == 5


def test_load_candle_series_only_includes_the_requested_intervals(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="ETHUSDT")

    base = datetime(2026, 2, 1, tzinfo=UTC)
    _insert_candles(conn, instrument_id=instrument_id, interval="1m", open_times=[base])
    _insert_candles(conn, instrument_id=instrument_id, interval="1h", open_times=[base])

    dataset_version = create_dataset_version(
        conn,
        symbol_set=[instrument_id],
        date_range_start=date(2026, 2, 1),
        date_range_end=date(2026, 2, 1),
        row_count=2,
        sample_hashes=["h1", "h2"],
    )

    loaded = load_candle_series_from_dataset_version(
        conn, dataset_version=dataset_version, intervals=["1m"]
    )
    assert set(loaded.keys()) == {("ETHUSDT", "1m")}


def test_load_candle_series_only_includes_symbols_in_the_dataset_versions_symbol_set(
    conn: sa.Connection,
) -> None:
    venue_id = _insert_venue(conn)
    included_instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")
    excluded_instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="XRPUSDT")

    base = datetime(2026, 3, 1, tzinfo=UTC)
    _insert_candles(conn, instrument_id=included_instrument_id, interval="1m", open_times=[base])
    _insert_candles(conn, instrument_id=excluded_instrument_id, interval="1m", open_times=[base])

    dataset_version = create_dataset_version(
        conn,
        symbol_set=[included_instrument_id],  # XRPUSDT deliberately not included
        date_range_start=date(2026, 3, 1),
        date_range_end=date(2026, 3, 1),
        row_count=1,
        sample_hashes=["h1"],
    )

    loaded = load_candle_series_from_dataset_version(
        conn, dataset_version=dataset_version, intervals=["1m"]
    )
    assert set(loaded.keys()) == {("BTCUSDT", "1m")}
