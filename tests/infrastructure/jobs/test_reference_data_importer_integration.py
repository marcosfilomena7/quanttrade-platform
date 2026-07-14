"""Live database tests for the Reference Data Importer (TASKS.md T-P1-02).

"Integration test runs against the test Postgres instance (testcontainers
or local dev-compose)." Spins up a real `timescale/timescaledb` container
(the same image and two-layer Docker-usability strategy established by
`tests/infrastructure/test_db_migrations.py` for T-P0-11), applies the
real baseline migration, and runs `import_reference_data` against it —
against a real `instrument` table, with a real `venue` row, verifying
idempotency and change detection as actual database state, not a mock.

`BinanceRestClient` itself is still backed by an `httpx.MockTransport`
(no real network call to Binance) — this test is "live" with respect to
Postgres, exactly as T-P1-02 asks for, not with respect to the venue.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable — see `tests/infrastructure/test_db_migrations.py`'s
module docstring for the full rationale behind the two-layer strategy
duplicated below.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.jobs.reference_data_importer import import_reference_data
from infrastructure.observability.metrics import reference_data_changed
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
    end so tests don't leak instrument/venue rows into each other."""
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


def _exchange_info_body(symbols: list[dict[str, object]]) -> dict[str, object]:
    return {"timezone": "UTC", "serverTime": 1735689600000, "symbols": symbols}


def _btc_symbol(*, tick_size: str = "0.01000000", status: str = "TRADING") -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "status": status,
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "baseAssetPrecision": 8,
        "quoteAssetPrecision": 8,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": tick_size},
            {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "10.00000000"},
        ],
    }


def _eth_symbol() -> dict[str, object]:
    return {
        "symbol": "ETHUSDT",
        "status": "TRADING",
        "baseAsset": "ETH",
        "quoteAsset": "USDT",
        "baseAssetPrecision": 8,
        "quoteAssetPrecision": 8,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00010000"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "10.00000000"},
        ],
    }


def _rest_client(body: dict[str, object]) -> BinanceRestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.binance.com"
    )
    return BinanceRestClient(http_client=http_client, api_key="test-key", api_secret="test-secret")


def _query_instrument(conn: sa.Connection, venue_id: uuid.UUID, symbol: str) -> sa.Row[object]:
    row = conn.execute(
        text(
            "SELECT id, tick_size, lot_size, min_notional, status, listed_at, updated_at "
            "FROM instrument WHERE venue_id = :venue_id AND symbol = :symbol"
        ),
        {"venue_id": venue_id, "symbol": symbol},
    ).one()
    return row


def test_inserts_new_instruments_with_decimal_typed_numeric_fields(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    rest_client = _rest_client(_exchange_info_body([_btc_symbol(), _eth_symbol()]))

    result = import_reference_data(rest_client=rest_client, conn=conn, venue_id=venue_id)

    assert result.inserted == 2
    assert result.updated == 0
    assert result.skipped == 0

    row = _query_instrument(conn, venue_id, "BTCUSDT")
    assert row.tick_size == Decimal("0.01000000")
    assert isinstance(row.tick_size, Decimal)
    assert row.lot_size == Decimal("0.00001000")
    assert isinstance(row.lot_size, Decimal)
    assert row.min_notional == Decimal("10.00000000")
    assert isinstance(row.min_notional, Decimal)
    assert row.status == "trading"


def test_running_twice_with_unchanged_data_is_idempotent(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    rest_client = _rest_client(_exchange_info_body([_btc_symbol(), _eth_symbol()]))

    first = import_reference_data(rest_client=rest_client, conn=conn, venue_id=venue_id)
    second = import_reference_data(rest_client=rest_client, conn=conn, venue_id=venue_id)

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.updated == 2

    count = conn.execute(
        text("SELECT count(*) FROM instrument WHERE venue_id = :venue_id"), {"venue_id": venue_id}
    ).scalar_one()
    assert count == 2  # no duplicates


def test_running_twice_preserves_the_original_id_and_listed_at(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    rest_client = _rest_client(_exchange_info_body([_btc_symbol()]))
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)

    import_reference_data(
        rest_client=rest_client, conn=conn, venue_id=venue_id, now=lambda: fixed_now
    )
    first_row = _query_instrument(conn, venue_id, "BTCUSDT")

    later = datetime(2026, 1, 2, tzinfo=UTC)
    import_reference_data(rest_client=rest_client, conn=conn, venue_id=venue_id, now=lambda: later)
    second_row = _query_instrument(conn, venue_id, "BTCUSDT")

    assert second_row.id == first_row.id
    assert second_row.listed_at == first_row.listed_at
    assert second_row.updated_at > first_row.updated_at


def test_a_tick_size_change_is_detected_logged_and_metered(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)

    first_client = _rest_client(_exchange_info_body([_btc_symbol(tick_size="0.01000000")]))
    import_reference_data(rest_client=first_client, conn=conn, venue_id=venue_id)

    before = _sample_reference_data_changed(venue="binance", symbol="BTCUSDT", field="tick_size")

    second_client = _rest_client(_exchange_info_body([_btc_symbol(tick_size="0.02000000")]))
    with structlog.testing.capture_logs() as captured_logs:
        result = import_reference_data(rest_client=second_client, conn=conn, venue_id=venue_id)

    assert result.changed_fields == 1

    change_events = [e for e in captured_logs if e.get("event") == "reference_data_changed"]
    assert len(change_events) == 1
    assert change_events[0]["field"] == "tick_size"
    assert change_events[0]["symbol"] == "BTCUSDT"
    assert change_events[0]["log_level"] == "warning"

    after = _sample_reference_data_changed(venue="binance", symbol="BTCUSDT", field="tick_size")
    assert after == before + 1

    row = _query_instrument(conn, venue_id, "BTCUSDT")
    assert row.tick_size == Decimal("0.02000000")


def test_no_change_detected_when_data_is_identical(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    rest_client = _rest_client(_exchange_info_body([_btc_symbol()]))

    import_reference_data(rest_client=rest_client, conn=conn, venue_id=venue_id)
    result = import_reference_data(rest_client=rest_client, conn=conn, venue_id=venue_id)

    assert result.changed_fields == 0


def test_a_symbol_missing_a_required_filter_is_skipped_not_fatal(conn: sa.Connection) -> None:
    venue_id = _insert_venue(conn)
    malformed = {
        "symbol": "BROKENUSDT",
        "status": "TRADING",
        "baseAsset": "BROKEN",
        "quoteAsset": "USDT",
        "baseAssetPrecision": 8,
        "quoteAssetPrecision": 8,
        "filters": [],  # missing every required filter
    }
    rest_client = _rest_client(_exchange_info_body([_btc_symbol(), malformed]))

    result = import_reference_data(rest_client=rest_client, conn=conn, venue_id=venue_id)

    assert result.inserted == 1
    assert result.skipped == 1

    count = conn.execute(
        text("SELECT count(*) FROM instrument WHERE venue_id = :venue_id"), {"venue_id": venue_id}
    ).scalar_one()
    assert count == 1


def _sample_reference_data_changed(*, venue: str, symbol: str, field: str) -> float:
    labels = {"venue": venue, "symbol": symbol, "field": field}
    for family in reference_data_changed.collect():
        for s in family.samples:
            if s.name == "reference_data_changed_total" and s.labels == labels:
                return s.value
    return 0.0
