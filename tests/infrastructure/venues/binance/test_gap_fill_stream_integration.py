"""Live database test for GapFillingCandleStream (TASKS.md T-P1-09).

TASKS.md's third T-P1-09 acceptance criterion is a statement about
database state across many reconnects — per the same reasoning already
established for T-P1-02 through T-P1-06, this suite spins up a real
`timescale/timescaledb` container via `testcontainers` (the identical
two-layer Docker-usability strategy duplicated across this repo's
integration tests) and exercises the real `backfill_candles` (T-P1-04)
and `run_validation_suite` (T-P1-05) against it through
`GapFillingCandleStream` (T-P1-09) and a real local `websockets.serve`
server driving the real `BinanceWebSocketClient` (T-P1-07) — no mocking
of SQLAlchemy, Alembic, or the WS protocol itself. `BinanceRestClient` is
backed by an `httpx.MockTransport` that computes klines mathematically
from the request's own `startTime`/`endTime`/`limit` params, exactly as
in test_ohlcv_backfill_job_integration.py — no real network call to
Binance.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable — see test_db_migrations.py's module docstring for the
full rationale behind the two-layer strategy duplicated below.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
import websockets
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.jobs.gap_detection import detect_gaps
from infrastructure.venues.binance.client import BinanceRestClient
from infrastructure.venues.binance.gap_fill_stream import GapFillingCandleStream, make_gap_fill
from infrastructure.venues.binance.websocket_client import BinanceWebSocketClient

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

REPO_ROOT = Path(__file__).resolve().parents[4]

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


def _kline_handler(step: timedelta) -> Callable[[httpx.Request], httpx.Response]:
    """Mock Binance klines handler: computes bars mathematically from the
    request's own startTime/endTime/limit, ascending and contiguous by
    `step` — identical in shape to test_ohlcv_backfill_job_integration.py's
    handler, just without any deliberate skip range (this suite's gaps
    come entirely from real WS disconnects, not from a REST-side hole)."""
    step_ms = int(step.total_seconds() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        start_ms = int(params["startTime"])
        end_ms = int(params["endTime"])
        limit = int(params["limit"])

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

    return handler


def _rest_client(handler: Callable[[httpx.Request], httpx.Response]) -> BinanceRestClient:
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.binance.com"
    )
    return BinanceRestClient(http_client=http_client, api_key="test-key", api_secret="test-secret")


class _RecordingEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, event: object) -> None:
        self.published.append((topic, event))

    async def subscribe(
        self, topic: str, handler: Callable[[object], Awaitable[None]]
    ) -> None:
        return None


def _kline_frame(*, open_time_ms: int) -> str:
    return json.dumps(
        {
            "e": "kline",
            "E": open_time_ms,
            "s": "BTCUSDT",
            "k": {
                "t": open_time_ms,
                "T": open_time_ms + 59_999,
                "s": "BTCUSDT",
                "i": "1m",
                "f": 100,
                "L": 200,
                "o": "100.00",
                "c": "100.50",
                "h": "101.00",
                "l": "99.00",
                "v": "10.5",
                "n": 50,
                "x": True,
                "q": "525000.00",
                "V": "5.0",
                "Q": "250000.00",
                "B": "0",
            },
        }
    )


def _free_port_url(server: websockets.WebSocketServer) -> str:
    port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return f"ws://127.0.0.1:{port}"


def test_10_forced_reconnects_over_10_minutes_of_market_time_leave_zero_gaps(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-09 acceptance criterion, verbatim: "Integration test:
    simulate 10 forced reconnects over 10 minutes; verify zero gaps in the
    stored candle data afterward."

    Ten WS sessions, each contributing one closed 1-minute bar and then
    forcibly disconnecting (except the last), span 20 simulated minutes
    of market time with a genuine 1-minute hole between every session —
    a hole only T-P1-04's REST gap-fill (triggered by T-P1-09's reconnect
    handling) ever covers. Zero gaps afterward proves the gap-fill
    actually ran for every one of the 10 reconnects, not just some.
    """
    venue_id = _insert_venue(conn)
    instrument_id = _insert_instrument(conn, venue_id=venue_id, symbol="BTCUSDT")

    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    step = timedelta(minutes=1)
    block = 2  # minutes between each session's bar: a real 1-minute hole in between
    num_reconnects = 10
    num_sessions = num_reconnects + 1  # 11 connections total

    def _ms(dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    sim_time: list[datetime] = [epoch]
    rest_client = _rest_client(_kline_handler(step))
    gap_fill = make_gap_fill(
        rest_client=rest_client,
        conn=conn,
        venue_id=venue_id,
        instrument_id=instrument_id,
        symbol="BTCUSDT",
        interval="1m",
        now=lambda: sim_time[0],
    )

    bus = _RecordingEventBus()
    stream = GapFillingCandleStream(
        instrument_id=instrument_id, event_bus=bus, gap_fill=gap_fill, now=lambda: sim_time[0]
    )

    session_counter = [0]

    async def server_handler(ws: websockets.WebSocketServerProtocol) -> None:
        i = session_counter[0]
        session_counter[0] += 1
        if i >= num_sessions:
            return  # any stray extra connection after the test has already stopped

        if i >= 1:
            # "now" at the moment this reconnect's gap-fill runs: one
            # minute before this session's own bar, so REST covers
            # exactly the 1-minute hole and WS covers the rest.
            sim_time[0] = epoch + step * (i * block - 1)

        open_time = epoch + step * (i * block)
        await ws.send(_kline_frame(open_time_ms=_ms(open_time)))

        if i < num_sessions - 1:
            await ws.close()  # forced disconnect -> triggers the next reconnect
        else:
            await ws.wait_closed()

    async def run() -> None:
        done = asyncio.Event()
        message_count = 0

        async def on_message(raw: str) -> None:
            nonlocal message_count
            await stream.on_message(raw)
            message_count += 1
            if message_count == num_sessions:
                client.stop()
                done.set()

        async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
            url = _free_port_url(server)
            client = BinanceWebSocketClient(
                url=url,
                event_bus=bus,
                on_message=on_message,
                initial_backoff_seconds=0.01,
                max_backoff_seconds=0.02,
            )
            stream.bind(client)
            task = asyncio.ensure_future(client.run())
            await asyncio.wait_for(done.wait(), timeout=30.0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(run())

    assert client_connection_attempts_reached(stream) == num_sessions
    assert stream.gap_fill_failures == 0

    last_open_time = epoch + step * ((num_sessions - 1) * block)
    result = detect_gaps(
        conn,
        instrument_id=instrument_id,
        interval="1m",
        window_start=epoch,
        window_end=last_open_time,
    )
    assert result.missing_count == 0
    assert result.gaps == []

    candle_closed_events = [e for topic, e in bus.published if topic == "candle_closed"]
    assert len(candle_closed_events) == num_sessions  # every WS-published bar reached the bus


def client_connection_attempts_reached(stream: GapFillingCandleStream) -> int:
    """The number of distinct connection generations `stream` observed —
    read via its private client reference, purely for this test's own
    sanity assertion that all 11 sessions were actually visited."""
    client = stream._client  # noqa: SLF001 — test-only introspection
    assert client is not None
    return client.connection_attempts
