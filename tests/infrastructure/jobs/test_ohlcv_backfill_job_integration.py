"""Live database tests for the Historical OHLCV Backfill Job (TASKS.md T-P1-04).

All four of T-P1-04's acceptance criteria are statements about database
state across multiple runs, so — per the same reasoning already
established for T-P1-02/T-P1-03 — this suite spins up a real
`timescale/timescaledb` container via `testcontainers` (the identical
two-layer Docker-usability strategy used throughout this repo's
integration tests), applies the real baseline + T-P1-04 migrations, and
exercises `backfill_candles` against it — real Postgres, real
`candle`/`candle_backfill_checkpoint` rows, no mocking of SQLAlchemy or
Alembic. `BinanceRestClient` itself is backed by an `httpx.MockTransport`
that computes klines mathematically from the request's own
`startTime`/`endTime`/`limit` params — no real network call to Binance,
and no need to hold 43,200 precomputed bars in memory.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable — see test_db_migrations.py's module docstring for the
full rationale behind the two-layer strategy duplicated below.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.jobs.ohlcv_backfill_job import backfill_candles
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
    """A running TimescaleDB container with `alembic upgrade head` already
    applied (baseline + T-P1-04's checkpoint table). See
    test_db_migrations.py::db_engine for why this performs a real
    host-side connectivity check rather than trusting the container's own
    internal readiness probe."""
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


def _truncate_all_tables(conn: sa.Connection) -> None:
    """Some job functions under test commit their own writes internally
    (legitimate production behavior), so a plain `connection.rollback()`
    cannot undo them. Truncating every table (except `alembic_version`)
    after each test is what actually gives each test a clean slate,
    regardless of what the code under test committed."""
    tables = conn.execute(
        text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename != 'alembic_version'"
        )
    ).scalars().all()
    if tables:
        quoted = ", ".join(f'"{name}"' for name in tables)
        conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        conn.commit()


@pytest.fixture
def conn(db_engine: sa.Engine) -> Iterator[sa.Connection]:
    """One connection per test. Rolls back any uncommitted work first,
    then truncates every table — see `_truncate_all_tables` for why a
    rollback alone isn't sufficient here."""
    with db_engine.connect() as connection:
        yield connection
        connection.rollback()
        _truncate_all_tables(connection)


def _insert_venue(conn: sa.Connection, *, name: str | None = None) -> uuid.UUID:
    """`name` defaults to a fresh, per-call unique value: `venue.name`
    has a UNIQUE constraint, and some job functions under test commit
    their own writes internally, so a fixed literal like `"binance"`
    would collide across tests within the same container."""
    venue_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO venue (id, name, venue_type, api_base_url, capabilities, "
            "fee_schedule, status) VALUES (:id, :name, 'cex', 'https://api.binance.com', "
            "'{}', '{}', 'active')"
        ),
        {"id": venue_id, "name": name if name is not None else f"binance-{venue_id}"},
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


def _kline_handler(
    step: timedelta, *, skip_ranges: list[tuple[datetime, datetime]] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """A mock Binance klines handler: computes bars mathematically from
    the request's own startTime/endTime/limit, ascending, contiguous by
    `step` — optionally omitting any open_time inside `skip_ranges`, to
    simulate a genuine gap in the venue's own historical data."""
    step_ms = int(step.total_seconds() * 1000)
    skips = skip_ranges or []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        start_ms = int(params["startTime"])
        end_ms = int(params["endTime"])
        limit = int(params["limit"])

        bars: list[list[object]] = []
        t = start_ms
        while t <= end_ms and len(bars) < limit:
            open_time = datetime.fromtimestamp(t / 1000, tz=UTC)
            skipped = any(skip_start <= open_time < skip_end for skip_start, skip_end in skips)
            if not skipped:
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

    return handler


def _rest_client(handler: Callable[[httpx.Request], httpx.Response]) -> BinanceRestClient:
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.binance.com"
    )
    return BinanceRestClient(http_client=http_client, api_key="test-key", api_secret="test-secret")


def _candle_count(conn: sa.Connection, instrument_id: uuid.UUID) -> int:
    return conn.execute(
        text("SELECT count(*) FROM candle WHERE instrument_id = :id"), {"id": instrument_id}
    ).scalar_one()


def test_backfill_30_days_of_1m_btcusdt_produces_43200_rows_with_no_gaps(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-04 acceptance criterion, verbatim: "Integration
    test: backfill 30 days of 1m BTC/USDT candles, then verify row count
    matches `30 * 24 * 60 = 43,200` (no gaps).\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    range_start = datetime(2026, 1, 1, tzinfo=UTC)
    range_end = range_start + timedelta(days=30) - timedelta(minutes=1)

    rest_client = _rest_client(_kline_handler(timedelta(minutes=1)))
    result = backfill_candles(
        rest_client=rest_client,
        conn=conn,
        venue_id=venue_id,
        instrument_id=instrument_id,
        symbol="BTCUSDT",
        interval="1m",
        range_start=range_start,
        range_end=range_end,
        now=lambda: range_end + timedelta(days=1),
    )

    assert result.upserted == 43_200
    assert result.gaps_detected == 0
    assert result.completed is True
    assert _candle_count(conn, instrument_id) == 43_200

    distinct_open_times = conn.execute(
        text("SELECT count(DISTINCT open_time) FROM candle WHERE instrument_id = :id"),
        {"id": instrument_id},
    ).scalar_one()
    assert distinct_open_times == 43_200  # no duplicate open_times either


def test_backfilling_the_same_range_twice_produces_no_duplicates(conn: sa.Connection) -> None:
    """TASKS.md T-P1-04 acceptance criterion, verbatim: "Backfilling the
    same range twice produces no duplicates (idempotent).\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    range_start = datetime(2026, 1, 1, tzinfo=UTC)
    range_end = range_start + timedelta(hours=2) - timedelta(minutes=1)
    rest_client = _rest_client(_kline_handler(timedelta(minutes=1)))

    kwargs = {
        "rest_client": rest_client,
        "conn": conn,
        "venue_id": venue_id,
        "instrument_id": instrument_id,
        "symbol": "BTCUSDT",
        "interval": "1m",
        "range_start": range_start,
        "range_end": range_end,
        "now": lambda: range_end + timedelta(hours=1),
    }

    first = backfill_candles(**kwargs)  # type: ignore[arg-type]
    second = backfill_candles(**kwargs)  # type: ignore[arg-type]

    assert first.upserted == 120
    assert first.completed is True
    assert second.upserted == 0  # already-completed checkpoint short-circuits
    assert second.completed is True

    assert _candle_count(conn, instrument_id) == 120  # not 240


def test_killing_process_mid_backfill_and_restarting_resumes_from_checkpoint(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-04 acceptance criterion, verbatim: "Killing the
    process mid-backfill and restarting resumes from the last checkpoint,
    not from the beginning.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    range_start = datetime(2026, 1, 1, tzinfo=UTC)
    range_end = range_start + timedelta(hours=3) - timedelta(minutes=1)  # 180 bars, 3 chunks of 60

    call_count = 0
    working_handler = _kline_handler(timedelta(minutes=1))

    def crashes_on_second_call(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ConnectionError("simulated process kill mid-backfill")
        return working_handler(request)

    crashing_client = _rest_client(crashes_on_second_call)

    with pytest.raises(ConnectionError, match="simulated process kill"):
        backfill_candles(
            rest_client=crashing_client,
            conn=conn,
            venue_id=venue_id,
            instrument_id=instrument_id,
            symbol="BTCUSDT",
            interval="1m",
            range_start=range_start,
            range_end=range_end,
            chunk_size=60,
            now=lambda: range_end + timedelta(hours=1),
        )

    # Only the first chunk (60 bars) made it in before the simulated crash.
    assert _candle_count(conn, instrument_id) == 60

    checkpoint = conn.execute(
        text(
            "SELECT last_completed_open_time, status FROM candle_backfill_checkpoint "
            "WHERE venue_id = :venue_id AND instrument_id = :instrument_id AND interval = '1m' "
            "AND range_start = :range_start AND range_end = :range_end"
        ),
        {
            "venue_id": venue_id,
            "instrument_id": instrument_id,
            "range_start": range_start,
            "range_end": range_end,
        },
    ).one()
    assert checkpoint.status == "in_progress"
    assert checkpoint.last_completed_open_time == range_start + timedelta(minutes=59)

    # "Restart the process": a fresh, working client, same call.
    requested_start_times: list[str] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requested_start_times.append(dict(request.url.params)["startTime"])
        return working_handler(request)

    resumed_client = _rest_client(recording_handler)
    result = backfill_candles(
        rest_client=resumed_client,
        conn=conn,
        venue_id=venue_id,
        instrument_id=instrument_id,
        symbol="BTCUSDT",
        interval="1m",
        range_start=range_start,
        range_end=range_end,
        chunk_size=60,
        now=lambda: range_end + timedelta(hours=1),
    )

    expected_resume_point = range_start + timedelta(minutes=60)
    assert result.resumed_from == expected_resume_point
    assert requested_start_times[0] == str(int(expected_resume_point.timestamp() * 1000))
    assert result.completed is True

    assert _candle_count(conn, instrument_id) == 180  # full range, no duplicates, no gap


def test_all_ohlcv_values_are_stored_as_numeric_decimal_not_float(conn: sa.Connection) -> None:
    """TASKS.md T-P1-04 acceptance criterion, verbatim: "All OHLCV values
    stored as `NUMERIC`, not `FLOAT`.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    range_start = datetime(2026, 1, 1, tzinfo=UTC)
    range_end = range_start + timedelta(minutes=4)
    rest_client = _rest_client(_kline_handler(timedelta(minutes=1)))

    backfill_candles(
        rest_client=rest_client,
        conn=conn,
        venue_id=venue_id,
        instrument_id=instrument_id,
        symbol="BTCUSDT",
        interval="1m",
        range_start=range_start,
        range_end=range_end,
        now=lambda: range_end + timedelta(hours=1),
    )

    row = conn.execute(
        text(
            "SELECT open, high, low, close, volume FROM candle "
            "WHERE instrument_id = :id ORDER BY open_time LIMIT 1"
        ),
        {"id": instrument_id},
    ).one()
    for value in row:
        assert isinstance(value, Decimal)
        assert not isinstance(value, float)
    assert row.open == Decimal("100.00000000")
    assert row.high == Decimal("101.00000000")


def test_a_gap_in_venue_data_is_detected_and_logged_as_a_warning(conn: sa.Connection) -> None:
    """Not one of the four literal acceptance-criterion bullets, but part
    of T-P1-04's own description: "Run gap detection after each chunk and
    alert on detected gaps.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    range_start = datetime(2026, 1, 1, tzinfo=UTC)
    range_end = range_start + timedelta(minutes=9)
    gap_start = range_start + timedelta(minutes=3)
    gap_end = range_start + timedelta(minutes=6)  # bars at minutes 3, 4, 5 are missing

    rest_client = _rest_client(
        _kline_handler(timedelta(minutes=1), skip_ranges=[(gap_start, gap_end)])
    )

    with structlog.testing.capture_logs() as captured_logs:
        result = backfill_candles(
            rest_client=rest_client,
            conn=conn,
            venue_id=venue_id,
            instrument_id=instrument_id,
            symbol="BTCUSDT",
            interval="1m",
            range_start=range_start,
            range_end=range_end,
            now=lambda: range_end + timedelta(hours=1),
        )

    assert result.gaps_detected == 1
    assert result.upserted == 7  # 10 requested minutes minus the 3 skipped

    gap_events = [e for e in captured_logs if e.get("event") == "candle_gap_detected"]
    assert len(gap_events) == 1
    assert gap_events[0]["log_level"] == "warning"
    assert gap_events[0]["symbol"] == "BTCUSDT"
