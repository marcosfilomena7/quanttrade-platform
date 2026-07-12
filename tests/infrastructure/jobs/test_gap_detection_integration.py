"""Live database tests for Gap Detection and Auto-Backfill (TASKS.md T-P1-06).

All three of T-P1-06's acceptance criteria are statements about database
state (rows deleted and re-detected, a backfill that closes the gap, a
maintenance window classified differently), so — per the same reasoning
already established for T-P1-02 through T-P1-05 — this suite spins up a
real `timescale/timescaledb` container via `testcontainers` (the
identical two-layer Docker-usability strategy used throughout this
repo's integration tests), applies the real baseline + T-P1-04 + T-P1-05
migrations (T-P1-06 adds no migration of its own — see
`gap_detection.py`'s module docstring), and exercises
`detect_gaps`/`run_gap_scan_and_backfill` against it — real Postgres,
real `candle`/`data_quality_event` rows, no mocking of SQLAlchemy or
Alembic. `BinanceRestClient` is backed by an `httpx.MockTransport` for
the auto-backfill scenario — no real network call to Binance.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable — see test_db_migrations.py's module docstring for the
full rationale behind the two-layer strategy duplicated below.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.jobs.gap_detection import (
    MaintenanceWindow,
    detect_gaps,
    run_gap_scan_and_backfill,
)
from infrastructure.venues.binance.client import BinanceRestClient

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
    """A running TimescaleDB container with `alembic upgrade head`
    already applied. See test_db_migrations.py::db_engine for why this
    performs a real host-side connectivity check rather than trusting
    the container's own internal readiness probe."""
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
    """One connection per test, with its own transaction rolled back at
    the end so tests don't leak rows into each other."""
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
    conn.commit()
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
    conn.commit()
    return instrument_id


def _fill_clean_candles(
    conn: sa.Connection, *, instrument_id: uuid.UUID, start: datetime, end: datetime
) -> None:
    """Insert one well-formed 1m candle per minute in [start, end], directly
    via SQL — this suite is testing the *detector*, not the backfill
    fetch path, so seed data is inserted straight into `candle` rather
    than routed through a mocked venue call."""
    t = start
    while t <= end:
        conn.execute(
            text(
                "INSERT INTO candle (instrument_id, interval, open_time, open, high, low, "
                "close, volume, trade_count, is_closed, source) VALUES (:id, '1m', :ot, "
                "100, 101, 99, 100.5, 10, 5, true, 'test_seed')"
            ),
            {"id": instrument_id, "ot": t},
        )
        t += timedelta(minutes=1)
    conn.commit()


def _delete_candles_at(
    conn: sa.Connection, *, instrument_id: uuid.UUID, open_times: list[datetime]
) -> None:
    conn.execute(
        text(
            "DELETE FROM candle WHERE instrument_id = :id AND interval = '1m' "
            "AND open_time = ANY(:open_times)"
        ),
        {"id": instrument_id, "open_times": open_times},
    )
    conn.commit()


def _rest_client(handler: httpx.MockTransport | None = None) -> BinanceRestClient:
    def default_handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        start_ms = int(params["startTime"])
        end_ms = int(params["endTime"])
        limit = int(params["limit"])
        step_ms = 60_000

        bars: list[list[object]] = []
        t = start_ms
        while t <= end_ms and len(bars) < limit:
            bars.append(
                [
                    t,
                    "100.00000000",
                    "101.00000000",
                    "99.00000000",
                    "100.50000000",
                    "10.00000000",
                    t + step_ms - 1,
                    "1000.00000000",
                    5,
                    "5.00000000",
                    "500.00000000",
                    "0",
                ]
            )
            t += step_ms
        return httpx.Response(200, json=bars)

    http_client = httpx.Client(
        transport=httpx.MockTransport(default_handler), base_url="https://api.binance.com"
    )
    return BinanceRestClient(http_client=http_client, api_key="test-key", api_secret="test-secret")


def test_deleting_5_rows_and_running_the_detector_identifies_all_5(conn: sa.Connection) -> None:
    """TASKS.md T-P1-06 acceptance criterion, verbatim: "Manually deleting
    5 rows from the candle table and running the detector identifies all
    5 missing intervals.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    window_start = datetime(2026, 1, 1, tzinfo=UTC)
    window_end = window_start + timedelta(minutes=59)
    _fill_clean_candles(conn, instrument_id=instrument_id, start=window_start, end=window_end)

    deleted_times = [
        window_start + timedelta(minutes=m) for m in (3, 10, 11, 30, 55)
    ]  # 5 rows, some scattered, some contiguous
    _delete_candles_at(conn, instrument_id=instrument_id, open_times=deleted_times)

    result = detect_gaps(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        window_start=window_start,
        window_end=window_end,
    )

    assert result.missing_count == 5
    all_missing = sorted(t for gap in result.gaps for t in gap.missing_open_times)
    assert all_missing == sorted(deleted_times)


def test_auto_backfill_fills_the_gaps_and_a_second_scan_finds_zero(conn: sa.Connection) -> None:
    """TASKS.md T-P1-06 acceptance criterion, verbatim: "The auto-backfill
    job fills the gaps and the subsequent detector scan finds zero
    gaps.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    window_start = datetime(2026, 1, 1, tzinfo=UTC)
    window_end = window_start + timedelta(minutes=29)
    _fill_clean_candles(conn, instrument_id=instrument_id, start=window_start, end=window_end)
    _delete_candles_at(
        conn,
        instrument_id=instrument_id,
        open_times=[window_start + timedelta(minutes=m) for m in (10, 11, 12)],
    )

    rest_client = _rest_client()
    first_scan = run_gap_scan_and_backfill(
        conn,
        rest_client=rest_client,
        venue_id=venue_id,
        instrument_id=instrument_id,
        symbol="BTCUSDT",
        interval="1m",
        window_start=window_start,
        window_end=window_end,
        now=lambda: window_end + timedelta(days=1),
    )
    assert first_scan.missing_count == 3

    second_scan = detect_gaps(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        window_start=window_start,
        window_end=window_end,
    )
    assert second_scan.gaps == []
    assert second_scan.missing_count == 0


def test_gaps_spanning_a_known_maintenance_window_are_classified_separately(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-06 acceptance criterion, verbatim: "Gaps spanning a
    known exchange maintenance window are classified separately (not a
    data error).\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    window_start = datetime(2026, 1, 1, tzinfo=UTC)
    window_end = window_start + timedelta(minutes=39)
    _fill_clean_candles(conn, instrument_id=instrument_id, start=window_start, end=window_end)

    # A real maintenance-shaped gap (minutes 20-24) and an unrelated,
    # unexplained gap (minute 5) in the same window.
    _delete_candles_at(
        conn,
        instrument_id=instrument_id,
        open_times=[window_start + timedelta(minutes=m) for m in (5, 20, 21, 22, 23, 24)],
    )

    maintenance_windows = [
        MaintenanceWindow(
            start=window_start + timedelta(minutes=20),
            end=window_start + timedelta(minutes=24),
            reason="Binance scheduled maintenance",
        )
    ]

    result = detect_gaps(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        window_start=window_start,
        window_end=window_end,
        maintenance_windows=maintenance_windows,
    )

    assert len(result.gaps) == 2
    by_classification = {gap.classification: gap for gap in result.gaps}
    assert set(by_classification) == {"unexplained", "maintenance"}
    assert by_classification["unexplained"].missing_open_times == [
        window_start + timedelta(minutes=5)
    ]
    assert len(by_classification["maintenance"].missing_open_times) == 5


def test_all_detected_gaps_are_logged_to_data_quality_event(conn: sa.Connection) -> None:
    """TASKS.md T-P1-06's description, verbatim: "Log all detected gaps
    to `data_quality_event`." — including maintenance-classified ones."""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    window_start = datetime(2026, 1, 1, tzinfo=UTC)
    window_end = window_start + timedelta(minutes=19)
    _fill_clean_candles(conn, instrument_id=instrument_id, start=window_start, end=window_end)
    _delete_candles_at(
        conn,
        instrument_id=instrument_id,
        open_times=[window_start + timedelta(minutes=m) for m in (5, 15)],
    )

    maintenance_windows = [
        MaintenanceWindow(
            start=window_start + timedelta(minutes=15),
            end=window_start + timedelta(minutes=15),
            reason="Binance scheduled maintenance",
        )
    ]

    rest_client = _rest_client()
    run_gap_scan_and_backfill(
        conn,
        rest_client=rest_client,
        venue_id=venue_id,
        instrument_id=instrument_id,
        symbol="BTCUSDT",
        interval="1m",
        window_start=window_start,
        window_end=window_end,
        maintenance_windows=maintenance_windows,
        now=lambda: window_end + timedelta(days=1),
    )

    events = conn.execute(
        text(
            "SELECT check_name, severity, details FROM data_quality_event "
            "WHERE instrument_id = :id ORDER BY open_time"
        ),
        {"id": instrument_id},
    ).all()
    assert len(events) == 2
    assert all(e.check_name == "missing_interval" for e in events)
    assert all(e.severity == "flagged" for e in events)
    classifications = {e.details["classification"] for e in events}
    assert classifications == {"unexplained", "maintenance"}

    # Only the unexplained gap should have been auto-backfilled;
    # the maintenance gap's minute stays missing (nothing to fetch).
    remaining = detect_gaps(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        window_start=window_start,
        window_end=window_end,
    )
    assert remaining.missing_count == 1
    assert remaining.gaps[0].missing_open_times == [window_start + timedelta(minutes=15)]
