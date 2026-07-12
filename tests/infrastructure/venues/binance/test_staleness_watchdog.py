"""Tests for infrastructure/venues/binance/staleness_watchdog.py (TASKS.md T-P1-10).

Following this repo's established convention (see
tests/infrastructure/venues/binance/test_websocket_client.py): async code
is exercised from plain `def test_...()` functions via an inner `async
def run(): ...` + `asyncio.run(run())`; no `pytest-asyncio` dependency.

The first acceptance criterion ("triggers... within 65 seconds") is
proven two ways rather than by literally sleeping 61+ real seconds in a
unit test: (a) a fast test with a sped-up threshold/check-interval
proves the firing mechanism itself works, driven through a *real* local
`websockets.serve` server and the real `BinanceWebSocketClient` (T-P1-07)
that gets stopped — matching the acceptance criterion's own literal
scenario; (b) a second, instant test proves the *default* configuration's
own constructor values (60s threshold + 1s check interval) mathematically
guarantee detection within 65 seconds, without needing to wait for it.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import websockets

from infrastructure.venues.binance.staleness_watchdog import (
    SymbolFeedStale,
    SymbolStalenessWatchdog,
)
from infrastructure.venues.binance.websocket_client import BinanceWebSocketClient


class _RecordingEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, event: object) -> None:
        self.published.append((topic, event))

    async def subscribe(
        self, topic: str, handler: Callable[[object], Awaitable[None]]
    ) -> None:
        return None


def _kline_frame(*, symbol: str, open_time_ms: int, is_closed: bool = False) -> str:
    return json.dumps(
        {
            "e": "kline",
            "E": open_time_ms,
            "s": symbol,
            "k": {
                "t": open_time_ms,
                "T": open_time_ms + 59_999,
                "s": symbol,
                "i": "1m",
                "o": "100.00",
                "c": "100.50",
                "h": "101.00",
                "l": "99.00",
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


# --- acceptance criterion 1: stopping the WS client still triggers FeedStale --


def test_stopping_the_ws_client_triggers_a_symbol_feed_stale_event_from_the_watchdog() -> None:
    """TASKS.md T-P1-10 acceptance criterion, verbatim: "Stopping the WS
    client while the watchdog runs triggers a `FeedStale` event within 65
    seconds."

    Uses a sped-up threshold/check-interval to keep the test fast; the
    default configuration's own arithmetic guarantee of the literal
    "within 65 seconds" bound is verified separately, below, without
    waiting for it.
    """

    async def handler(ws: websockets.WebSocketServerProtocol) -> None:
        await ws.send(_kline_frame(symbol="BTCUSDT", open_time_ms=1_700_000_000_000))
        await ws.wait_closed()

    async def run() -> None:
        bus = _RecordingEventBus()
        watchdog = SymbolStalenessWatchdog(
            event_bus=bus, threshold_seconds=0.1, check_interval_seconds=0.02
        )

        async def on_message(raw: str) -> None:
            payload = json.loads(raw)
            watchdog.record_received(payload["s"])

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            url = _free_port_url(server)
            client = BinanceWebSocketClient(url=url, event_bus=bus, on_message=on_message)
            client_task = asyncio.ensure_future(client.run())
            watchdog_task = asyncio.ensure_future(watchdog.run())

            await asyncio.sleep(0.05)  # let the one message be received/recorded
            client.stop()  # "stopping the WS client" — the watchdog must not care
            client_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client_task

            await asyncio.sleep(0.3)  # well past the sped-up threshold
            watchdog.stop()
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task

        stale_events = [e for topic, e in bus.published if topic == "symbol_feed_stale"]
        assert len(stale_events) >= 1
        assert isinstance(stale_events[0], SymbolFeedStale)
        assert stale_events[0].symbol == "BTCUSDT"
        assert stale_events[0].idle_seconds > 0.1

    asyncio.run(run())


def test_default_configuration_guarantees_detection_within_65_seconds() -> None:
    """The default constructor values (60s threshold, 1s check interval)
    mean the very first check tick that can possibly detect staleness
    happens no later than `threshold_seconds + check_interval_seconds` =
    61 seconds after a symbol goes quiet — comfortably inside the
    acceptance criterion's stated 65-second bound. Verified by reading
    the defaults back, not by waiting for them."""
    watchdog = SymbolStalenessWatchdog(event_bus=_RecordingEventBus())
    assert watchdog.threshold_seconds == 60.0
    assert watchdog.check_interval_seconds == 1.0
    worst_case_detection_seconds = watchdog.threshold_seconds + watchdog.check_interval_seconds
    assert worst_case_detection_seconds < 65.0


# --- acceptance criterion 2: no trigger for an actively-receiving symbol ---


def test_the_watchdog_does_not_trigger_for_a_symbol_actively_receiving_data() -> None:
    """TASKS.md T-P1-10 acceptance criterion, verbatim: "The watchdog does
    not trigger for a symbol that is actively receiving data."""

    async def run() -> None:
        bus = _RecordingEventBus()
        watchdog = SymbolStalenessWatchdog(
            event_bus=bus, threshold_seconds=0.1, check_interval_seconds=0.02
        )
        watchdog_task = asyncio.ensure_future(watchdog.run())

        async def keep_feeding() -> None:
            for _ in range(20):
                watchdog.record_received("ETHUSDT")
                await asyncio.sleep(0.01)  # faster than the 0.1s threshold

        await keep_feeding()

        watchdog.stop()
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task

        stale_events = [e for topic, e in bus.published if topic == "symbol_feed_stale"]
        assert stale_events == []

    asyncio.run(run())


# --- acceptance criterion 3: independent of any connection-state flag -----


def test_the_watchdog_fires_even_when_a_connection_object_reports_connected_true() -> None:
    """TASKS.md T-P1-10 acceptance criterion, verbatim: "The watchdog's
    staleness check is independent of the WS connection state variable
    (tests confirm it fires even when the socket object reports
    `connected = True`)."""

    class _FakeConnection:
        def __init__(self) -> None:
            self.connected = True  # never read by the watchdog, deliberately

    async def run() -> None:
        bus = _RecordingEventBus()
        fake_connection = _FakeConnection()
        watchdog = SymbolStalenessWatchdog(
            event_bus=bus, threshold_seconds=0.1, check_interval_seconds=0.02
        )
        watchdog.record_received("BTCUSDT")

        watchdog_task = asyncio.ensure_future(watchdog.run())
        await asyncio.sleep(0.3)  # well past the threshold; connection stays "connected"
        watchdog.stop()
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task

        stale_events = [e for topic, e in bus.published if topic == "symbol_feed_stale"]
        assert len(stale_events) >= 1
        assert fake_connection.connected is True  # unchanged: the watchdog never touched it

    asyncio.run(run())


def test_the_watchdog_module_never_references_any_connection_state_concept() -> None:
    """A structural, not just behavioral, proof of acceptance criterion 3:
    the module's own source contains no reference to a connection/socket
    state concept at all — following this repo's established AST-based
    structural-check convention (see tests/domain/test_ports.py)."""
    path = Path("infrastructure/venues/binance/staleness_watchdog.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    forbidden_names = {"connected", "connection", "socket", "ws", "websocket"}
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr.lower())
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg.lower())

    assert forbidden_names.isdisjoint(identifiers)


# --- symbols are tracked dynamically, no pre-registration needed ----------


def test_a_symbols_first_record_received_call_seeds_its_clock_without_immediate_staleness() -> (
    None
):
    async def run() -> None:
        bus = _RecordingEventBus()
        watchdog = SymbolStalenessWatchdog(
            event_bus=bus, threshold_seconds=1.0, check_interval_seconds=0.02
        )
        watchdog.record_received("BTCUSDT")  # first-ever sighting of this symbol

        watchdog_task = asyncio.ensure_future(watchdog.run())
        await asyncio.sleep(0.1)  # well under the 1.0s threshold
        watchdog.stop()
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task

        assert bus.published == []

    asyncio.run(run())
