"""Live database tests for the Parquet Archival Pipeline (TASKS.md T-P1-11).

All five of T-P1-11's acceptance criteria are statements about Postgres
and Parquet-file state, so — per the same reasoning already established
for T-P1-02 through T-P1-09 — this suite spins up a real
`timescale/timescaledb` container via `testcontainers` (the identical
two-layer Docker-usability strategy duplicated across this repo's
integration tests) and exercises the real `archive_candles`,
`archive_trade_ticks`, and `run_nightly_archival` against it — real
Postgres rows, real Parquet files on the local filesystem (via
`tmp_path`), no mocking of SQLAlchemy or Alembic.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable — see test_db_migrations.py's module docstring for the
full rationale behind the two-layer strategy duplicated below.
"""

from __future__ import annotations

import contextlib
import hashlib
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import sqlalchemy as sa
import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.db.tables.backtest import dataset_version
from infrastructure.db.tables.market_data import candle as candle_table
from infrastructure.db.tables.market_data import trade_tick as trade_tick_table
from infrastructure.jobs.parquet_archival_job import (
    archive_candles,
    archive_trade_ticks,
    run_nightly_archival,
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
    would collide across tests within the same container. No test in
    this file asserts on the venue row's own `name` column — the
    literal `"binance"` passed to `archive_candles`/`run_nightly_archival`
    (`venue_name=...`) is only used for parquet path construction,
    independent of this row."""
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


def _insert_candles(
    conn: sa.Connection,
    *,
    instrument_id: uuid.UUID,
    interval: str,
    open_times: list[datetime],
    source: str = "test_fixture",
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
                "source": source,
            }
            for ot in open_times
        ],
    )


def _insert_trade_ticks(
    conn: sa.Connection, *, instrument_id: uuid.UUID, timestamps: list[datetime]
) -> None:
    conn.execute(
        sa.insert(trade_tick_table),
        [
            {
                "instrument_id": instrument_id,
                "ts": ts,
                "venue_trade_id": str(i),
                "price": Decimal("50000.00000001"),
                "qty": Decimal("0.001"),
                "side": "buy",
            }
            for i, ts in enumerate(timestamps)
        ],
    )


def _candle_count_in_range(
    conn: sa.Connection, *, instrument_id: uuid.UUID, start: datetime, end: datetime
) -> int:
    return conn.execute(
        sa.select(sa.func.count()).where(
            candle_table.c.instrument_id == instrument_id,
            candle_table.c.open_time >= start,
            candle_table.c.open_time < end,
        )
    ).scalar_one()


def test_archival_leaves_zero_rows_in_the_archived_range(
    conn: sa.Connection, tmp_path: Path
) -> None:
    """TASKS.md T-P1-11 acceptance criterion, verbatim: "After archival,
    querying the archived date range from Postgres returns zero rows.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    month_start = datetime(2026, 1, 1, tzinfo=UTC)
    open_times = [month_start + timedelta(minutes=i) for i in range(5)]
    _insert_candles(conn, instrument_id=instrument_id, interval="1m", open_times=open_times)

    result = archive_candles(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        venue_name="binance",
        symbol="BTCUSDT",
        range_start=month_start,
        range_end=month_start + timedelta(days=31),
        base_path=tmp_path,
    )

    assert len(result.partitions) == 1
    assert result.partitions[0].checksum_ok is True
    assert (
        _candle_count_in_range(
            conn,
            instrument_id=instrument_id,
            start=month_start,
            end=month_start + timedelta(days=31),
        )
        == 0
    )


def test_the_parquet_file_exists_with_the_correct_row_count(
    conn: sa.Connection, tmp_path: Path
) -> None:
    """TASKS.md T-P1-11 acceptance criterion, verbatim: "The Parquet file
    exists at the correct path with the correct row count.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="ETHUSDT")

    month_start = datetime(2026, 2, 1, tzinfo=UTC)
    open_times = [month_start + timedelta(minutes=i) for i in range(7)]
    _insert_candles(conn, instrument_id=instrument_id, interval="1m", open_times=open_times)

    result = archive_candles(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        venue_name="binance",
        symbol="ETHUSDT",
        range_start=month_start,
        range_end=month_start + timedelta(days=28),
        base_path=tmp_path,
    )

    expected_path = tmp_path / "binance" / "ETHUSDT" / "2026" / "02" / "data.parquet"
    assert result.partitions[0].path == expected_path
    assert expected_path.exists()
    table = pq.read_table(expected_path)
    assert table.num_rows == 7
    assert result.partitions[0].row_count == 7


def test_content_hash_in_dataset_version_matches_the_parquet_files_sha256(
    conn: sa.Connection, tmp_path: Path
) -> None:
    """TASKS.md T-P1-11 acceptance criterion, verbatim: "Content hash in
    `DatasetVersion` matches the Parquet file's SHA-256.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    month_start = datetime(2026, 3, 1, tzinfo=UTC)
    open_times = [month_start + timedelta(minutes=i) for i in range(3)]
    _insert_candles(conn, instrument_id=instrument_id, interval="1m", open_times=open_times)

    result = archive_candles(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        venue_name="binance",
        symbol="BTCUSDT",
        range_start=month_start,
        range_end=month_start + timedelta(days=31),
        base_path=tmp_path,
    )

    partition = result.partitions[0]
    on_disk_hash = hashlib.sha256(partition.path.read_bytes()).hexdigest()
    assert partition.content_hash == on_disk_hash

    row = conn.execute(
        sa.select(dataset_version.c.content_hash).where(
            dataset_version.c.id == partition.dataset_version_id
        )
    ).one()
    assert row.content_hash == on_disk_hash


def test_a_failed_checksum_aborts_deletion_and_emits_an_alert(
    conn: sa.Connection, tmp_path: Path
) -> None:
    """TASKS.md T-P1-11 acceptance criterion, verbatim: "A failed checksum
    aborts deletion and emits an alert.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    month_start = datetime(2026, 4, 1, tzinfo=UTC)
    open_times = [month_start + timedelta(minutes=i) for i in range(4)]
    _insert_candles(conn, instrument_id=instrument_id, interval="1m", open_times=open_times)

    def corrupting_read_bytes(path: Path) -> bytes:
        return path.read_bytes() + b"\x00corruption"

    with structlog.testing.capture_logs() as logs:
        result = archive_candles(
            conn,
            instrument_id=instrument_id,
            interval="1m",
            venue_name="binance",
            symbol="BTCUSDT",
            range_start=month_start,
            range_end=month_start + timedelta(days=30),
            base_path=tmp_path,
            read_bytes=corrupting_read_bytes,
        )

    assert len(result.partitions) == 1
    assert result.partitions[0].checksum_ok is False
    assert result.partitions[0].deleted is False
    assert result.partitions[0].dataset_version_id is None

    # Deletion aborted: the source rows are all still there.
    assert (
        _candle_count_in_range(
            conn,
            instrument_id=instrument_id,
            start=month_start,
            end=month_start + timedelta(days=30),
        )
        == 4
    )

    # No DatasetVersion was ever registered for this (non-matching) hash.
    assert (
        conn.execute(
            sa.select(sa.func.count()).where(
                dataset_version.c.content_hash == result.partitions[0].content_hash
            )
        ).scalar_one()
        == 0
    )

    events = [entry["event"] for entry in logs]
    assert "parquet_archival_checksum_failed" in events


def test_rerunning_archival_for_an_already_archived_period_is_idempotent(
    conn: sa.Connection, tmp_path: Path
) -> None:
    """TASKS.md T-P1-11 acceptance criterion, verbatim: "Re-running the
    archival job for an already-archived period is idempotent (no
    duplicate files, no error).\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    month_start = datetime(2026, 5, 1, tzinfo=UTC)
    open_times = [month_start + timedelta(minutes=i) for i in range(6)]
    _insert_candles(conn, instrument_id=instrument_id, interval="1m", open_times=open_times)

    kwargs = {
        "instrument_id": instrument_id,
        "interval": "1m",
        "venue_name": "binance",
        "symbol": "BTCUSDT",
        "range_start": month_start,
        "range_end": month_start + timedelta(days=31),
        "base_path": tmp_path,
    }

    first = archive_candles(conn, **kwargs)  # type: ignore[arg-type]
    assert len(first.partitions) == 1
    assert first.partitions[0].row_count == 6

    path = first.partitions[0].path
    original_bytes = path.read_bytes()

    second = archive_candles(conn, **kwargs)  # type: ignore[arg-type]

    assert second.partitions == []  # nothing left to archive — a clean no-op
    assert path.read_bytes() == original_bytes  # file untouched, not rewritten/duplicated
    assert list(path.parent.iterdir()) == [path]  # no duplicate file alongside it


def test_trade_tick_archival_leaves_zero_rows_and_matches_row_count(
    conn: sa.Connection, tmp_path: Path
) -> None:
    """`trade_tick` archival exercised end-to-end (T-P1-11 names "candle
    and tick data" explicitly)."""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    month_start = datetime(2026, 6, 1, tzinfo=UTC)
    timestamps = [month_start + timedelta(seconds=i) for i in range(9)]
    _insert_trade_ticks(conn, instrument_id=instrument_id, timestamps=timestamps)

    result = archive_trade_ticks(
        conn,
        instrument_id=instrument_id,
        venue_name="binance",
        symbol="BTCUSDT",
        range_start=month_start,
        range_end=month_start + timedelta(days=30),
        base_path=tmp_path,
    )

    assert len(result.partitions) == 1
    assert result.partitions[0].row_count == 9
    assert result.partitions[0].checksum_ok is True

    remaining = conn.execute(
        sa.select(sa.func.count()).where(trade_tick_table.c.instrument_id == instrument_id)
    ).scalar_one()
    assert remaining == 0

    table = pq.read_table(result.partitions[0].path)
    assert table.num_rows == 9
    assert table.column("price")[0].as_py() == "50000.00000001"


def test_run_nightly_archival_archives_both_candle_and_trade_tick_older_than_90_days(
    conn: sa.Connection, tmp_path: Path
) -> None:
    """TASKS.md T-P1-11's own literal framing: "a nightly job that
    exports candle and tick data older than 90 days.\""""
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    now = datetime(2026, 6, 1, tzinfo=UTC)
    old_month_start = datetime(2026, 1, 1, tzinfo=UTC)  # well older than 90 days before `now`
    recent_ts = now - timedelta(days=1)  # inside the 90-day retention window — must survive

    _insert_candles(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        open_times=[old_month_start, old_month_start + timedelta(minutes=1), recent_ts],
    )

    candle_result, tick_result = run_nightly_archival(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        venue_name="binance",
        symbol="BTCUSDT",
        archive_from=datetime(2020, 1, 1, tzinfo=UTC),
        base_path=tmp_path,
        retention_days=90,
        now=lambda: now,
    )

    assert candle_result.partitions[0].row_count == 2  # only the two old-month bars
    assert tick_result.partitions == []  # no trade_tick rows were ever inserted

    # The recent bar (inside the 90-day retention window) must survive.
    remaining = conn.execute(
        sa.select(sa.func.count()).where(candle_table.c.instrument_id == instrument_id)
    ).scalar_one()
    assert remaining == 1
    surviving_open_time = conn.execute(
        sa.select(candle_table.c.open_time).where(candle_table.c.instrument_id == instrument_id)
    ).scalar_one()
    assert surviving_open_time == recent_ts
