"""Live database tests for the Candle Data Validation Suite (TASKS.md T-P1-05).

All four of T-P1-05's acceptance criteria are statements about database
state (a quarantine record, a retained-but-flagged candle, a clean
30-day dataset, synthetic corruption caught), so — per the same
reasoning already established for T-P1-02/03/04 — this suite spins up a
real `timescale/timescaledb` container via `testcontainers` (the
identical two-layer Docker-usability strategy used throughout this
repo's integration tests), applies the real baseline + T-P1-04 + T-P1-05
migrations, and exercises `validate_and_ingest_candles` /
`run_validation_suite` against it — real Postgres, real
`candle`/`data_quality_event` rows, no mocking of SQLAlchemy or Alembic.

The acceptance criterion "running the suite against the 30-day BTC
dataset from T-P1-04" is satisfied literally: this suite calls T-P1-04's
own `backfill_candles` (unmodified) against a mocked Binance klines
transport to produce that dataset, then validates it — reusing, not
duplicating, T-P1-04's already-tested behavior.

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
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.jobs.ohlcv_backfill_job import backfill_candles
from infrastructure.observability.metrics import data_quality_violations_total
from infrastructure.validation.candle_validation import (
    CandleRecord,
    run_validation_suite,
    validate_and_ingest_candles,
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
    already applied (baseline + T-P1-04 + T-P1-05). See
    test_db_migrations.py::db_engine for why this performs a real
    host-side connectivity check rather than trusting the container's
    own internal readiness probe."""
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
    """Some validation functions under test commit their own writes
    internally (legitimate production behavior), so a plain
    `connection.rollback()` cannot undo them. Truncating every table
    (except `alembic_version`) after each test is what actually gives
    each test a clean slate, regardless of what the code under test
    committed."""
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
    has a UNIQUE constraint, and some validation functions under test
    commit their own writes internally, so a fixed literal like
    `"binance"` would collide across tests within the same container."""
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


def _candle_row(conn: sa.Connection, instrument_id: uuid.UUID) -> sa.Row[object]:
    return conn.execute(
        text(
            "SELECT open, high, low, close, volume, is_closed FROM candle "
            "WHERE instrument_id = :id ORDER BY open_time LIMIT 1"
        ),
        {"id": instrument_id},
    ).one()


def _sample_data_quality_violations_total(*, check: str, severity: str) -> float:
    labels = {"check": check, "severity": severity}
    for family in data_quality_violations_total.collect():
        for s in family.samples:
            if s.name == "data_quality_violations_total" and s.labels == labels:
                return s.value
    return 0.0


def test_a_high_less_than_close_candle_produces_a_quarantine_record_and_metric(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-05 acceptance criterion, verbatim: "Injecting a
    candle with `high < close` produces a quarantine record and a metric
    increment.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    before = _sample_data_quality_violations_total(check="ohlc_invariant", severity="quarantined")

    bad_candle = CandleRecord(
        instrument_id=instrument_id,
        interval="1m",
        open_time=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("100"),  # high < close below
        low=Decimal("99"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )

    result = validate_and_ingest_candles(conn, [bad_candle])

    assert len(result.quarantined) == 1
    assert result.quarantined[0].check == "ohlc_invariant"
    assert result.retained_candles == []

    event = conn.execute(
        text(
            "SELECT check_name, severity FROM data_quality_event "
            "WHERE instrument_id = :id AND check_name = 'ohlc_invariant'"
        ),
        {"id": instrument_id},
    ).one()
    assert event.check_name == "ohlc_invariant"
    assert event.severity == "quarantined"

    candle_count = conn.execute(
        text("SELECT count(*) FROM candle WHERE instrument_id = :id"), {"id": instrument_id}
    ).scalar_one()
    assert candle_count == 0  # never reached candle — the CHECK constraint would reject it anyway

    after = _sample_data_quality_violations_total(check="ohlc_invariant", severity="quarantined")
    assert after == before + 1


def test_a_20_sigma_price_spike_is_flagged_but_the_candle_is_retained(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-05 acceptance criterion, verbatim: "A 20 sigma price
    spike is flagged but the candle is retained (not deleted).\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    price = Decimal("100")
    for m in range(25):
        price += Decimal("0.01") if m % 2 == 0 else Decimal("-0.01")
        candles.append(
            CandleRecord(
                instrument_id=instrument_id,
                interval="1m",
                open_time=base_time + timedelta(minutes=m),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=Decimal("10"),
            )
        )
    spike_price = price * Decimal("50")  # a genuine 20-sigma-scale dislocation
    candles.append(
        CandleRecord(
            instrument_id=instrument_id,
            interval="1m",
            open_time=base_time + timedelta(minutes=25),
            open=price,
            high=spike_price + 1,
            low=price - 1,
            close=spike_price,
            volume=Decimal("10"),
        )
    )

    result = validate_and_ingest_candles(
        conn, candles, price_move_window=20, price_move_sigma_threshold=Decimal("10")
    )

    spike_violations = [v for v in result.flagged if v.check == "price_move_sigma"]
    assert len(spike_violations) == 1

    # Retained — not deleted: every one of the 26 candles, including the
    # spike itself, made it into `candle`.
    candle_count = conn.execute(
        text("SELECT count(*) FROM candle WHERE instrument_id = :id"), {"id": instrument_id}
    ).scalar_one()
    assert candle_count == 26

    spike_row = conn.execute(
        text(
            "SELECT close FROM candle WHERE instrument_id = :id "
            "ORDER BY open_time DESC LIMIT 1"
        ),
        {"id": instrument_id},
    ).one()
    assert spike_row.close == spike_price


def test_running_the_suite_against_the_30_day_btc_dataset_from_t_p1_04_is_clean(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-05 acceptance criterion, verbatim: "Running the
    suite against the 30-day BTC dataset from T-P1-04 produces zero
    violations (clean data).\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    range_start = datetime(2026, 1, 1, tzinfo=UTC)
    range_end = range_start + timedelta(days=30) - timedelta(minutes=1)

    def clean_kline_handler(request: httpx.Request) -> httpx.Response:
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
        transport=httpx.MockTransport(clean_kline_handler), base_url="https://api.binance.com"
    )
    rest_client = BinanceRestClient(
        http_client=http_client, api_key="test-key", api_secret="test-secret"
    )

    backfill_result = backfill_candles(
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
    assert backfill_result.upserted == 30 * 24 * 60  # T-P1-04's own acceptance criterion, reused

    validation_result = run_validation_suite(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        window_start=range_start,
        window_end=range_end,
    )

    assert validation_result.quarantined == []
    assert validation_result.flagged == []

    event_count = conn.execute(
        text("SELECT count(*) FROM data_quality_event WHERE instrument_id = :id"),
        {"id": instrument_id},
    ).scalar_one()
    assert event_count == 0


def test_synthetic_gap_is_caught_as_a_missing_interval(conn: sa.Connection) -> None:
    """TASKS.md T-P1-05 acceptance criterion, verbatim (gap case):
    "Injecting synthetic corruption (gap, ...) is caught in each
    corresponding test.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    candles = _clean_candles(instrument_id, base_time, count=5, skip_minutes={2})

    result = validate_and_ingest_candles(conn, candles)

    gap_violations = [v for v in result.flagged if v.check == "missing_interval"]
    assert len(gap_violations) == 1


def test_synthetic_inverted_ohlc_is_caught(conn: sa.Connection) -> None:
    """TASKS.md T-P1-05 acceptance criterion, verbatim (inverted-OHLC
    case): "Injecting synthetic corruption (..., inverted OHLC, ...) is
    caught in each corresponding test.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    candles = _clean_candles(instrument_id, base_time, count=3)
    inverted = CandleRecord(
        instrument_id=instrument_id,
        interval="1m",
        open_time=base_time + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("95"),  # high < low: fully inverted
        low=Decimal("105"),
        close=Decimal("100"),
        volume=Decimal("10"),
    )
    candles[1] = inverted

    result = validate_and_ingest_candles(conn, candles)

    ohlc_violations = [v for v in result.quarantined if v.check == "ohlc_invariant"]
    assert len(ohlc_violations) == 1
    assert len(result.retained_candles) == 2  # the two clean candles, not the inverted one


def test_synthetic_non_monotonic_timestamp_is_caught(conn: sa.Connection) -> None:
    """TASKS.md T-P1-05 acceptance criterion, verbatim (non-monotonic
    case): "Injecting synthetic corruption (..., non-monotonic
    timestamp) is caught in each corresponding test.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    candles = _clean_candles(instrument_id, base_time, count=4)
    # Duplicate the second bar's timestamp onto the third, breaking strict monotonicity.
    candles[2] = CandleRecord(
        instrument_id=instrument_id,
        interval="1m",
        open_time=candles[1].open_time,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
    )

    result = validate_and_ingest_candles(conn, candles)

    mono_violations = [v for v in result.flagged if v.check == "timestamp_monotonic"]
    assert len(mono_violations) == 1


def _clean_candles(
    instrument_id: uuid.UUID,
    base_time: datetime,
    *,
    count: int,
    skip_minutes: set[int] | None = None,
) -> list[CandleRecord]:
    skip = skip_minutes or set()
    return [
        CandleRecord(
            instrument_id=instrument_id,
            interval="1m",
            open_time=base_time + timedelta(minutes=m),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("10"),
        )
        for m in range(count)
        if m not in skip
    ]
