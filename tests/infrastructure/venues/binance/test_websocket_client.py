"""Tests for infrastructure/venues/binance/websocket_client.py (TASKS.md T-P1-07).

Every test here runs a *real* local WebSocket server via `websockets.serve`
— an in-process, pure-Python asyncio server bound to `127.0.0.1` on an
OS-assigned free port. This is the acceptance criteria's own "mock WS
server", and unlike the Postgres-backed integration suites elsewhere in
this repo, it needs no Docker at all: every test in this file genuinely
runs and is verified in any environment with Python installed.

Following this repo's established convention (see tests/domain/test_ports.py,
tests/infrastructure/test_logging.py): async code is exercised from plain
`def test_...()` functions via an inner `async def run(): ...` +
`asyncio.run(run())` — this project has no `pytest-asyncio` dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable

import structlog
import websockets

from infrastructure.venues.binance.websocket_client import (
    BinanceWebSocketClient,
    FeedStale,
    backoff_delay,
)


class _RecordingEventBus:
    """A minimal `EventBus` (structurally, per T-P0-07's `@runtime_checkable`
    Protocol) that records every published event for assertions."""

    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, event: object) -> None:
        self.published.append((topic, event))

    async def subscribe(
        self, topic: str, handler: Callable[[object], Awaitable[None]]
    ) -> None:
        return None


async def _free_port_url(server: websockets.WebSocketServer) -> str:
    port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return f"ws://127.0.0.1:{port}"


# --- acceptance criterion: 1000 messages received without drops ------------


def test_1000_messages_are_all_received_in_order_without_drops() -> None:
    """TASKS.md T-P1-07 acceptance criterion, verbatim: "Unit test with a
    mock WS server: 1000 messages received without drops.\""""

    async def server_handler(ws: websockets.WebSocketServerProtocol) -> None:
        for i in range(1000):
            await ws.send(json.dumps({"seq": i}))
        await ws.close()

    async def run() -> None:
        received: list[int] = []
        done = asyncio.Event()

        async def on_message(raw: str) -> None:
            payload = json.loads(raw)
            received.append(payload["seq"])
            if len(received) == 1000:
                client.stop()
                done.set()

        async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
            url = await _free_port_url(server)
            client = BinanceWebSocketClient(
                url=url, event_bus=_RecordingEventBus(), on_message=on_message
            )
            task = asyncio.ensure_future(client.run())
            await asyncio.wait_for(done.wait(), timeout=10.0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)

        assert received == list(range(1000))

    asyncio.run(run())


# --- acceptance criterion: staleness triggers FeedStale + reconnect --------


def test_silence_past_the_threshold_triggers_feed_stale_and_a_reconnect() -> None:
    """TASKS.md T-P1-07 acceptance criterion, verbatim: "Simulating socket
    silence for > threshold seconds triggers a `FeedStale` event and a
    reconnect attempt.\""""

    async def silent_handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(json.dumps({"seq": 0}))
        await asyncio.sleep(10.0)  # never sends again; outlives the test

    async def run() -> None:
        event_bus = _RecordingEventBus()

        async def on_message(raw: str) -> None:
            return None

        async with websockets.serve(silent_handler, "127.0.0.1", 0) as server:
            url = await _free_port_url(server)
            client = BinanceWebSocketClient(
                url=url,
                event_bus=event_bus,
                on_message=on_message,
                stale_threshold_seconds=0.1,
                stale_check_interval_seconds=0.02,
                initial_backoff_seconds=0.05,
                max_backoff_seconds=0.1,
            )
            task = asyncio.ensure_future(client.run())
            await asyncio.sleep(0.6)  # well past one stale-detect + reconnect cycle
            client.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        feed_stale_events = [e for topic, e in event_bus.published if topic == "feed_stale"]
        assert len(feed_stale_events) >= 1
        assert isinstance(feed_stale_events[0], FeedStale)
        assert feed_stale_events[0].idle_seconds > 0.1
        assert client.connection_attempts >= 2  # the original connection, plus a reconnect

    asyncio.run(run())


# --- acceptance criterion: forced close reconnects within 5 seconds -------


def test_a_forcefully_closed_connection_triggers_reconnect_within_5_seconds() -> None:
    """TASKS.md T-P1-07 acceptance criterion, verbatim: "A forcefully
    closed connection triggers reconnect within 5 seconds." Uses this
    client's *default* backoff settings — proving the default
    configuration itself satisfies the criterion, not a test-only
    fast-tracked one."""

    async def closes_immediately_handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.close()

    async def run() -> None:
        async def on_message(raw: str) -> None:
            return None

        async with websockets.serve(closes_immediately_handler, "127.0.0.1", 0) as server:
            url = await _free_port_url(server)
            client = BinanceWebSocketClient(
                url=url, event_bus=_RecordingEventBus(), on_message=on_message
            )
            start = time.monotonic()
            task = asyncio.ensure_future(client.run())

            while client.connection_attempts < 2 and time.monotonic() - start < 5.0:
                await asyncio.sleep(0.01)
            elapsed = time.monotonic() - start

            client.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert client.connection_attempts >= 2
        assert elapsed < 5.0

    asyncio.run(run())


# --- acceptance criterion: exponential backoff, jittered -------------------


def test_backoff_delay_is_always_positive() -> None:
    for attempt in range(10):
        assert backoff_delay(attempt) > 0


def test_backoff_delay_minimum_increases_with_attempt_until_capped() -> None:
    """With jitter fixed at its minimum (0.0), the *floor* of each
    attempt's delay must strictly increase — proving the exponential
    growth itself, independent of randomness."""
    floors = [
        backoff_delay(attempt, initial_seconds=1.0, max_seconds=100.0, jitter=lambda: 0.0)
        for attempt in range(6)
    ]
    assert floors == sorted(floors)
    assert len(set(floors)) == len(floors)  # strictly increasing, no ties


def test_backoff_delay_respects_the_configured_cap() -> None:
    delay = backoff_delay(20, initial_seconds=1.0, max_seconds=30.0, jitter=lambda: 1.0)
    assert delay <= 30.0


def test_backoff_delay_is_jittered_not_identical_across_calls() -> None:
    """TASKS.md T-P1-07 acceptance criterion, verbatim: "Reconnect uses
    exponential backoff: delays are non-zero, increasing, and jittered
    (not identical across reconnects).\""""
    delays = {backoff_delay(3) for _ in range(20)}
    assert len(delays) > 1  # real randomness: astronomically unlikely to collide


def test_backoff_delay_is_non_zero_even_at_attempt_zero_with_no_jitter() -> None:
    assert backoff_delay(0, initial_seconds=1.0, jitter=lambda: 0.0) > 0


# --- sequence tracking (description: "tracks sequence numbers where available") --


def test_a_sequence_gap_is_logged_as_a_warning() -> None:
    async def gappy_handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(json.dumps({"u": 1}))
        await ws.send(json.dumps({"u": 2}))
        await ws.send(json.dumps({"u": 5}))  # gap: 3, 4 skipped
        await ws.close()

    async def run() -> tuple[list[dict[str, object]], list[str]]:
        received: list[dict[str, object]] = []
        done = asyncio.Event()

        async def on_message(raw: str) -> None:
            received.append(json.loads(raw))
            if len(received) == 3:
                client.stop()
                done.set()

        async with websockets.serve(gappy_handler, "127.0.0.1", 0) as server:
            url = await _free_port_url(server)
            client = BinanceWebSocketClient(
                url=url,
                event_bus=_RecordingEventBus(),
                on_message=on_message,
                sequence_field="u",
            )
            with structlog.testing.capture_logs() as logs:
                task = asyncio.ensure_future(client.run())
                await asyncio.wait_for(done.wait(), timeout=10.0)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(task, timeout=2.0)

        gap_events = [
            entry["event"] for entry in logs if entry.get("event") == "sequence_gap_detected"
        ]
        return received, gap_events

    received, gap_events = asyncio.run(run())
    assert received == [{"u": 1}, {"u": 2}, {"u": 5}]
    assert len(gap_events) == 1


def test_no_sequence_field_configured_means_no_gap_tracking_at_all() -> None:
    async def handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(json.dumps({"u": 1}))
        await ws.send(json.dumps({"u": 99}))
        await ws.close()

    async def run() -> list[str]:
        received: list[dict[str, object]] = []
        done = asyncio.Event()

        async def on_message(raw: str) -> None:
            received.append(json.loads(raw))
            if len(received) == 2:
                client.stop()
                done.set()

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            url = await _free_port_url(server)
            client = BinanceWebSocketClient(
                url=url, event_bus=_RecordingEventBus(), on_message=on_message
            )
            with structlog.testing.capture_logs() as logs:
                task = asyncio.ensure_future(client.run())
                await asyncio.wait_for(done.wait(), timeout=10.0)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(task, timeout=2.0)

        return [entry["event"] for entry in logs]

    events = asyncio.run(run())
    assert "sequence_gap_detected" not in events


# --- heartbeat: sends a ping frame on the configured interval --------------


def test_heartbeat_pings_are_sent_on_the_configured_interval() -> None:
    async def handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.wait_closed()

    async def run() -> None:
        ping_count = 0
        original_ping = websockets.WebSocketClientProtocol.ping

        async def counting_ping(self: websockets.WebSocketClientProtocol) -> object:
            nonlocal ping_count
            ping_count += 1
            return await original_ping(self)

        async def on_message(raw: str) -> None:
            return None

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            url = await _free_port_url(server)
            client = BinanceWebSocketClient(
                url=url,
                event_bus=_RecordingEventBus(),
                on_message=on_message,
                heartbeat_interval_seconds=0.05,
                stale_threshold_seconds=100.0,
            )
            task = asyncio.ensure_future(client.run())
            await asyncio.sleep(0.001)  # let the connection establish
            websockets.WebSocketClientProtocol.ping = counting_ping  # type: ignore[method-assign]
            try:
                await asyncio.sleep(0.3)
            finally:
                websockets.WebSocketClientProtocol.ping = original_ping  # type: ignore[method-assign]
            client.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert ping_count >= 2

    asyncio.run(run())
