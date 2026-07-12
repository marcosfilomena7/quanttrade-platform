"""Tests for infrastructure/venues/binance/candle_stream.py (TASKS.md T-P1-08).

Following this repo's established convention (see
tests/infrastructure/venues/binance/test_websocket_client.py): async code
is exercised from plain `def test_...()` functions via an inner `async
def run(): ...` + `asyncio.run(run())` — no `pytest-asyncio` dependency.

The "1000 simulated WS messages" acceptance criterion is verified against
a *real* local WebSocket server (`websockets.serve`), driving the actual
`BinanceWebSocketClient` (T-P1-07) with `BinanceCandleStream.on_message`
wired in as its `on_message` callback — proving the two compose exactly
as designed, not just that the parser works in isolation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import websockets

from domain.candle import Candle
from infrastructure.venues.binance.candle_stream import BinanceCandleStream, CandleClosed
from infrastructure.venues.binance.websocket_client import BinanceWebSocketClient

_INSTRUMENT_ID = uuid4()


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


def _kline_frame(
    *,
    open_time_ms: int,
    event_time_ms: int,
    is_closed: bool,
    interval: str = "1m",
    symbol: str = "BTCUSDT",
    open_: str = "50000.00",
    high: str = "50020.00",
    low: str = "49990.00",
    close: str = "50010.00",
    volume: str = "10.5",
) -> str:
    """Builds one raw Binance `<symbol>@kline_<interval>` WS text frame."""
    return json.dumps(
        {
            "e": "kline",
            "E": event_time_ms,
            "s": symbol,
            "k": {
                "t": open_time_ms,
                "T": open_time_ms + 59_999,
                "s": symbol,
                "i": interval,
                "f": 100,
                "L": 200,
                "o": open_,
                "c": close,
                "h": high,
                "l": low,
                "v": volume,
                "n": 50,
                "x": is_closed,
                "q": "525000.00",
                "V": "5.0",
                "Q": "250000.00",
                "B": "0",
            },
        }
    )


# --- acceptance criterion: partial candle emits no CandleClosed event ------


def test_a_partial_candle_does_not_emit_a_candle_closed_event() -> None:
    """TASKS.md T-P1-08 acceptance criterion, verbatim: "A partial candle
    (is_closed = false) does not emit a `CandleClosed` event.\""""

    async def run() -> None:
        bus = _RecordingEventBus()
        stream = BinanceCandleStream(instrument_id=_INSTRUMENT_ID, event_bus=bus)

        await stream.on_message(
            _kline_frame(
                open_time_ms=1_700_000_000_000, event_time_ms=1_700_000_005_000, is_closed=False
            )
        )

        assert bus.published == []
        assert stream.latest_partial_candle is not None
        assert stream.latest_partial_candle.is_closed is False

    asyncio.run(run())


# --- acceptance criterion: closed candle emits exactly one event with both timestamps --


def test_a_closed_candle_emits_exactly_one_candle_closed_event_with_both_timestamps_populated() -> (
    None
):
    """TASKS.md T-P1-08 acceptance criterion, verbatim: "A closed candle
    emits exactly one `CandleClosed` event with both timestamps
    populated.\""""

    async def run() -> None:
        bus = _RecordingEventBus()
        fixed_recv = datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC)
        stream = BinanceCandleStream(
            instrument_id=_INSTRUMENT_ID, event_bus=bus, now=lambda: fixed_recv
        )

        await stream.on_message(
            _kline_frame(
                open_time_ms=1_700_000_000_000, event_time_ms=1_700_000_005_000, is_closed=True
            )
        )

        assert len(bus.published) == 1
        topic, event = bus.published[0]
        assert topic == "candle_closed"
        assert isinstance(event, CandleClosed)
        assert event.exchange_ts is not None
        assert event.local_recv_ts is not None
        assert event.local_recv_ts == fixed_recv
        assert isinstance(event.candle, Candle)
        assert event.candle.is_closed is True
        assert event.candle.instrument_id == _INSTRUMENT_ID
        assert event.candle.open == Decimal("50000.00")
        assert event.candle.close == Decimal("50010.00")

    asyncio.run(run())


# --- acceptance criterion: exchange_ts != local_recv_ts, never conflated ---


def test_exchange_ts_and_local_recv_ts_are_independent_never_conflated() -> None:
    """TASKS.md T-P1-08 acceptance criterion, verbatim: "`exchange_ts !=
    local_recv_ts` — they are independent fields, never conflated.\""""

    async def run() -> None:
        bus = _RecordingEventBus()
        fixed_recv = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)  # far later than exchange_ts
        stream = BinanceCandleStream(
            instrument_id=_INSTRUMENT_ID, event_bus=bus, now=lambda: fixed_recv
        )

        await stream.on_message(
            _kline_frame(
                open_time_ms=1_700_000_000_000, event_time_ms=1_700_000_005_000, is_closed=True
            )
        )

        _, event = bus.published[0]
        assert isinstance(event, CandleClosed)
        assert event.exchange_ts != event.local_recv_ts
        assert event.exchange_ts == datetime.fromtimestamp(1_700_000_005_000 / 1000, tz=UTC)
        assert event.local_recv_ts == fixed_recv

    asyncio.run(run())


# --- partial candles are buffered, not published, with an optional sink ---


def test_partial_candles_are_forwarded_to_the_optional_on_partial_candle_callback() -> None:
    async def run() -> None:
        received: list[Candle] = []

        async def on_partial(candle: Candle) -> None:
            received.append(candle)

        bus = _RecordingEventBus()
        stream = BinanceCandleStream(
            instrument_id=_INSTRUMENT_ID, event_bus=bus, on_partial_candle=on_partial
        )

        await stream.on_message(
            _kline_frame(
                open_time_ms=1_700_000_000_000, event_time_ms=1_700_000_005_000, is_closed=False
            )
        )

        assert len(received) == 1
        assert received[0].is_closed is False
        assert bus.published == []

    asyncio.run(run())


# --- malformed / non-kline frames are ignored, never raise -----------------


def test_non_kline_and_malformed_frames_are_silently_ignored() -> None:
    async def run() -> None:
        bus = _RecordingEventBus()
        stream = BinanceCandleStream(instrument_id=_INSTRUMENT_ID, event_bus=bus)

        await stream.on_message("not json at all")
        await stream.on_message(json.dumps({"e": "trade", "E": 1, "s": "BTCUSDT"}))
        await stream.on_message(
            json.dumps({"e": "kline", "E": 1, "s": "BTCUSDT", "k": "not-a-dict"})
        )
        await stream.on_message(
            # missing fields (o, h, l, c, v, x) on the kline object
            json.dumps({"e": "kline", "E": 1, "s": "BTCUSDT", "k": {"t": 1, "i": "1m"}})
        )

        assert bus.published == []
        assert stream.latest_partial_candle is None

    asyncio.run(run())


# --- acceptance criterion: 1000 simulated WS messages, exact counts --------


def _free_port_url(server: websockets.WebSocketServer) -> str:
    port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return f"ws://127.0.0.1:{port}"


def test_1000_simulated_ws_messages_yield_zero_partial_events_and_exact_closed_count() -> None:
    """TASKS.md T-P1-08 acceptance criterion, verbatim: "1000 simulated WS
    messages processed with zero events emitted for partial candles and
    exactly the expected count of `CandleClosed` events."

    Driven through a real local `websockets.serve` server and the actual
    `BinanceWebSocketClient` (T-P1-07), with `BinanceCandleStream.on_message`
    wired in as its `on_message` callback — end-to-end, not just the parser.
    """

    total_messages = 1000
    updates_per_bar = 10  # 9 partial updates + 1 closing update per bar
    expected_closed = total_messages // updates_per_bar

    async def server_handler(ws: websockets.WebSocketServerProtocol) -> None:
        open_time_ms = 1_700_000_000_000
        for i in range(total_messages):
            is_closed = (i % updates_per_bar) == updates_per_bar - 1
            await ws.send(
                _kline_frame(
                    open_time_ms=open_time_ms,
                    event_time_ms=open_time_ms + i,
                    is_closed=is_closed,
                )
            )
            if is_closed:
                open_time_ms += 60_000
        await ws.close()

    async def run() -> None:
        bus = _RecordingEventBus()
        stream = BinanceCandleStream(instrument_id=_INSTRUMENT_ID, event_bus=bus)
        done = asyncio.Event()
        message_count = 0

        async def on_message(raw: str) -> None:
            nonlocal message_count
            await stream.on_message(raw)
            message_count += 1
            if message_count == total_messages:
                client.stop()
                done.set()

        async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
            url = _free_port_url(server)
            client = BinanceWebSocketClient(url=url, event_bus=bus, on_message=on_message)
            task = asyncio.ensure_future(client.run())
            await asyncio.wait_for(done.wait(), timeout=10.0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=2.0)

        candle_closed_events = [e for topic, e in bus.published if topic == "candle_closed"]
        assert len(candle_closed_events) == expected_closed
        assert all(isinstance(e, CandleClosed) for e in candle_closed_events)
        assert all(e.candle.is_closed for e in candle_closed_events)  # type: ignore[union-attr]

    asyncio.run(run())


def test_instrument_id_is_a_uuid_on_the_produced_candle() -> None:
    async def run() -> None:
        bus = _RecordingEventBus()
        instrument_id = uuid4()
        stream = BinanceCandleStream(instrument_id=instrument_id, event_bus=bus)

        await stream.on_message(
            _kline_frame(
                open_time_ms=1_700_000_000_000, event_time_ms=1_700_000_005_000, is_closed=True
            )
        )

        _, event = bus.published[0]
        assert isinstance(event, CandleClosed)
        assert isinstance(event.candle.instrument_id, UUID)
        assert event.candle.instrument_id == instrument_id

    asyncio.run(run())
