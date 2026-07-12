"""Tests for infrastructure/venues/binance/gap_fill_stream.py (TASKS.md T-P1-09).

Following this repo's established convention (see
tests/infrastructure/venues/binance/test_websocket_client.py and
test_candle_stream.py): async code is exercised from plain `def
test_...()` functions via an inner `async def run(): ...` + `asyncio.run
(run())`; no `pytest-asyncio` dependency. Reconnect scenarios are driven
through a *real* local `websockets.serve` server and the actual
`BinanceWebSocketClient` (T-P1-07) — a genuine forced disconnect, not a
simulated one — matching the bar this repo's Phase 1 test suites have
held since T-P1-07 (real, runtime-verified, no Docker needed for the
unit-level acceptance criteria).

The third acceptance criterion ("Integration test: simulate 10 forced
reconnects over 10 minutes; verify zero gaps in the stored candle data
afterward") is a database-state assertion and lives in
test_gap_fill_stream_integration.py, gated on Docker per this repo's
existing convention.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import structlog
import websockets

from infrastructure.venues.binance.candle_stream import CandleClosed
from infrastructure.venues.binance.gap_fill_stream import GapFillingCandleStream
from infrastructure.venues.binance.websocket_client import BinanceWebSocketClient

_INSTRUMENT_ID = uuid4()


class _RecordingEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, event: object) -> None:
        self.published.append((topic, event))

    async def subscribe(
        self, topic: str, handler: Callable[[object], Awaitable[None]]
    ) -> None:
        return None


def _kline_frame(*, open_time_ms: int, is_closed: bool) -> str:
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
                "o": "50000.00",
                "c": "50010.00",
                "h": "50020.00",
                "l": "49990.00",
                "v": "10.5",
                "n": 50,
                "x": is_closed,
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


# --- acceptance criterion 1: reconnect gap-fills before next CandleClosed --


def test_a_forced_disconnect_gap_fills_before_the_next_candle_closed_event_publishes() -> None:
    """TASKS.md T-P1-09 acceptance criterion, verbatim: "Forcing a WS
    disconnect while the client is running results in a gap-fill covering
    the disconnected interval before the next `CandleClosed` event is
    published."""

    async def session_1_handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(_kline_frame(open_time_ms=1_700_000_000_000, is_closed=True))
        await ws.close()  # forced disconnect

    async def session_2_handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(_kline_frame(open_time_ms=1_700_000_120_000, is_closed=True))
        await ws.wait_closed()

    handlers = [session_1_handler, session_2_handler]

    async def server_handler(ws: websockets.WebSocketServerProtocol) -> None:
        handler = handlers.pop(0)
        await handler(ws)

    async def run() -> None:
        bus = _RecordingEventBus()
        gap_fill_calls: list[tuple[datetime, datetime]] = []
        order_log: list[str] = []

        async def gap_fill(range_start: datetime, range_end: datetime) -> None:
            gap_fill_calls.append((range_start, range_end))
            order_log.append("gap_fill")

        stream = GapFillingCandleStream(
            instrument_id=_INSTRUMENT_ID, event_bus=bus, gap_fill=gap_fill
        )

        done = asyncio.Event()
        message_count = 0

        async def on_message(raw: str) -> None:
            nonlocal message_count
            await stream.on_message(raw)
            message_count += 1
            if message_count == 1:
                order_log.append("published:1")
            elif message_count == 2:
                order_log.append("published:2")
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
            await asyncio.wait_for(done.wait(), timeout=10.0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # The first bar published with no reconnect involved.
        assert len(bus.published) == 2
        first_topic, first_event = bus.published[0]
        assert first_topic == "candle_closed"
        assert isinstance(first_event, CandleClosed)
        assert first_event.candle.open_time == datetime.fromtimestamp(
            1_700_000_000_000 / 1000, tz=UTC
        )

        # Exactly one gap-fill call, covering the disconnected interval:
        # [last_known_bar_ts, now] anchored at the bar published before the
        # forced disconnect.
        assert len(gap_fill_calls) == 1
        gap_start, gap_end = gap_fill_calls[0]
        assert gap_start == datetime.fromtimestamp(1_700_000_000_000 / 1000, tz=UTC)

        # The gap-fill call happened strictly before the second CandleClosed
        # was published — never after.
        assert order_log.index("gap_fill") < order_log.index("published:2")

    asyncio.run(run())


def test_the_first_connection_is_not_treated_as_a_reconnect() -> None:
    """The very first WS connection has no prior connection to have
    dropped, so it must not trigger a gap-fill."""

    async def handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(_kline_frame(open_time_ms=1_700_000_000_000, is_closed=True))
        await ws.wait_closed()

    async def run() -> None:
        bus = _RecordingEventBus()
        gap_fill_calls: list[tuple[datetime, datetime]] = []

        async def gap_fill(range_start: datetime, range_end: datetime) -> None:
            gap_fill_calls.append((range_start, range_end))

        stream = GapFillingCandleStream(
            instrument_id=_INSTRUMENT_ID, event_bus=bus, gap_fill=gap_fill
        )
        done = asyncio.Event()

        async def on_message(raw: str) -> None:
            await stream.on_message(raw)
            client.stop()
            done.set()

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            url = _free_port_url(server)
            client = BinanceWebSocketClient(url=url, event_bus=bus, on_message=on_message)
            stream.bind(client)
            task = asyncio.ensure_future(client.run())
            await asyncio.wait_for(done.wait(), timeout=10.0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert gap_fill_calls == []
        assert len(bus.published) == 1

    asyncio.run(run())


# --- acceptance criterion 2: gap-fill failure keeps publication halted, logs --


def test_a_failed_gap_fill_keeps_publication_halted_and_logs_the_failure() -> None:
    """TASKS.md T-P1-09 acceptance criterion, verbatim: "If the gap-fill
    itself fails (REST 5xx), publication remains halted and the failure is
    logged."""

    async def session_1_handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(_kline_frame(open_time_ms=1_700_000_000_000, is_closed=True))
        await ws.close()

    async def session_2_handler(ws: websockets.WebSocketServerProtocol) -> None:
        # Two closed bars arrive after the reconnect; both must be
        # suppressed since the gap-fill for this reconnect fails.
        await ws.send(_kline_frame(open_time_ms=1_700_000_120_000, is_closed=True))
        await ws.send(_kline_frame(open_time_ms=1_700_000_180_000, is_closed=True))
        await ws.wait_closed()

    handlers = [session_1_handler, session_2_handler]

    async def server_handler(ws: websockets.WebSocketServerProtocol) -> None:
        handler = handlers.pop(0)
        await handler(ws)

    class _SimulatedServerError(Exception):
        pass

    async def run() -> tuple[list[str], int]:
        bus = _RecordingEventBus()

        async def failing_gap_fill(range_start: datetime, range_end: datetime) -> None:
            raise _SimulatedServerError("simulated REST 5xx")

        stream = GapFillingCandleStream(
            instrument_id=_INSTRUMENT_ID, event_bus=bus, gap_fill=failing_gap_fill
        )
        done = asyncio.Event()
        message_count = 0

        async def on_message(raw: str) -> None:
            nonlocal message_count
            await stream.on_message(raw)
            message_count += 1
            if message_count == 3:
                client.stop()
                done.set()

        with structlog.testing.capture_logs() as logs:
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
                await asyncio.wait_for(done.wait(), timeout=10.0)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        events = [entry["event"] for entry in logs]
        return events, stream.gap_fill_failures

    events, gap_fill_failures = asyncio.run(run())

    assert "gap_fill_failed_publication_remains_halted" in events
    assert gap_fill_failures == 1


def test_missing_last_known_bar_ts_skips_gap_fill_and_resumes_immediately() -> None:
    """A reconnect before any candle has ever been published has nothing
    to anchor a gap-fill range to; publication resumes without calling
    `gap_fill` at all."""

    async def session_1_handler(ws: websockets.WebSocketServerProtocol) -> None:
        # Only a *partial* candle before the forced disconnect — no
        # CandleClosed has ever been published yet.
        await ws.send(_kline_frame(open_time_ms=1_700_000_000_000, is_closed=False))
        await ws.close()

    async def session_2_handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(_kline_frame(open_time_ms=1_700_000_060_000, is_closed=True))
        await ws.wait_closed()

    handlers = [session_1_handler, session_2_handler]

    async def server_handler(ws: websockets.WebSocketServerProtocol) -> None:
        handler = handlers.pop(0)
        await handler(ws)

    async def run() -> None:
        bus = _RecordingEventBus()
        gap_fill_calls: list[tuple[datetime, datetime]] = []

        async def gap_fill(range_start: datetime, range_end: datetime) -> None:
            gap_fill_calls.append((range_start, range_end))

        stream = GapFillingCandleStream(
            instrument_id=_INSTRUMENT_ID, event_bus=bus, gap_fill=gap_fill
        )
        done = asyncio.Event()
        message_count = 0

        async def on_message(raw: str) -> None:
            nonlocal message_count
            await stream.on_message(raw)
            message_count += 1
            if message_count == 2:
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
            await asyncio.wait_for(done.wait(), timeout=10.0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert gap_fill_calls == []
        assert len(bus.published) == 1  # only the second session's closed candle

    asyncio.run(run())


def test_on_message_before_bind_raises_runtime_error() -> None:
    async def run() -> None:
        bus = _RecordingEventBus()

        async def gap_fill(range_start: datetime, range_end: datetime) -> None:
            return None

        stream = GapFillingCandleStream(
            instrument_id=_INSTRUMENT_ID, event_bus=bus, gap_fill=gap_fill
        )
        try:
            await stream.on_message(_kline_frame(open_time_ms=1, is_closed=True))
        except RuntimeError as exc:
            assert "bind" in str(exc)
            return
        raise AssertionError("expected RuntimeError")

    asyncio.run(run())
