"""BinanceWebSocketClient — persistent WS connection with heartbeat,
sequence tracking, staleness detection, and reconnect (TASKS.md T-P1-07).

"Implement a `BinanceWebSocketClient` that maintains a persistent WS
connection to the Binance stream endpoint, sends `ping` frames on the
Binance-required 30-second heartbeat, tracks sequence numbers where
available, detects silent connection death (open socket, no messages
for > threshold seconds), and emits a `FeedStale` event. Uses
exponential backoff with jitter on reconnect."

Design decisions, and why:

- **No message normalization or candle handling.** ARCHITECTURE.md's M6
  module lists WS-feed responsibilities (subscribe, normalize, stamp,
  publish candles) far beyond this task's own description, but T-P1-08
  ("Realtime Candle Stream") is the dedicated, later task for
  normalizing/stamping/publishing candle events. This client only knows
  about connection lifecycle; every raw text frame it receives is handed
  to an injected `on_message` callback, which T-P1-08 will implement.
- **This is *not* T-P1-10's per-symbol staleness watchdog.** A later,
  separate task ("Per-Symbol Data Staleness Watchdog", dependencies
  T-P1-08 + T-P0-08) implements a per-*symbol*, connection-state-
  independent watchdog with a configurable default threshold, a
  Prometheus metric, and a P1 alert. T-P1-07's own acceptance criterion
  is narrower and connection-level: "no messages for > threshold
  seconds" on *this one connection* — there is no per-symbol
  bookkeeping here, no metric, and no alert; this task only needs to
  detect the connection has gone quiet and reconnect. Both modules emit
  the same kind of `FeedStale` event (see below) without either
  depending on or duplicating the other.
- **`FeedStale` is defined here, not in `domain/`.** No domain event
  vocabulary exists yet (`domain/` currently holds only value/entity
  types — `Candle`, `Fill`, `Instrument`, `Money`, `Order`, `Position` —
  no notification/event types). Inventing a `domain/events.py` module
  now would be guessing at a design a later task (T-P1-08, which
  introduces `CandleClosed`) actually owns. `EventBus.publish(topic:
  str, event: object)` (T-P0-07) takes a plain `object` by design
  ("transport-agnostic"), so an infrastructure-defined dataclass is a
  perfectly valid payload — this is that payload, minimal and scoped to
  what this task needs.
- **`websockets` (PyPI) is the WS library**, added as a new runtime
  dependency. Neither TASKS.md nor ARCHITECTURE.md names a specific
  library; `websockets` is the standard, asyncio-native choice
  (consistent with ADR-001's asyncio decision) and — crucially for
  testing — ships its own real, in-process server (`websockets.serve`),
  so the acceptance criteria's "mock WS server" can be a genuine local
  server exercising the real client code path, not a hand-rolled fake.
- **Two separate clocks.** `monotonic` (`time.monotonic`, default)
  measures elapsed durations (staleness, backoff) — a wall clock is the
  wrong tool for measuring an interval, since NTP adjustments can make
  it jump backward or forward. `now` (`datetime.now(UTC)`, default)
  stamps `FeedStale.detected_at` for logging/alerting, where a real
  calendar timestamp is what's useful. Both are injectable for
  deterministic tests, mirroring T-P1-01/04's established `now`
  convention.
- **Sequence tracking is field-name-configurable, not stream-specific.**
  Binance kline/trade payloads carry no sequence number at all; only
  some streams (e.g. depth-diff) do. Rather than hardcoding a
  stream-specific shape, `sequence_field` names the JSON key (if any)
  holding a monotonically increasing integer; a detected gap is logged
  as a structured warning. No acceptance criterion pins down further
  behavior (no dedicated table, no metric) — "where available" is
  honored literally, without inventing an unrequested response to a
  detected gap.
- **Heartbeat pings do not reset the staleness clock.** `websockets`
  handles ping/pong at the protocol level, transparently; they never
  surface through the message-receive loop. Only genuine application
  data resets `last_message_at` — exactly the "open socket, no messages"
  failure mode this task describes, which an outbound ping (or an
  automatic pong reply) must not be able to mask.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
import websockets

from domain.ports.event_bus import EventBus

_logger = structlog.get_logger()

_FEED_STALE_TOPIC = "feed_stale"


@dataclass(frozen=True)
class FeedStale:
    """Published to the `EventBus` when this connection has received no
    application message for longer than its configured threshold."""

    idle_seconds: float
    detected_at: datetime


def backoff_delay(
    attempt: int,
    *,
    initial_seconds: float = 1.0,
    max_seconds: float = 30.0,
    jitter: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with "equal jitter": half the exponential
    value is guaranteed, the other half is randomized on top. This
    keeps every delay strictly positive, keeps the *minimum* possible
    delay strictly increasing with `attempt` (until the cap is reached),
    and makes two calls for the same `attempt` genuinely differ —
    exactly TASKS.md's three requirements ("non-zero, increasing, and
    jittered (not identical across reconnects)").
    """
    capped: float = min(initial_seconds * (2.0**attempt), max_seconds)
    half: float = capped / 2
    return half + half * jitter()


def _extract_sequence(payload: object, sequence_field: str) -> int | None:
    if isinstance(payload, dict):
        value = payload.get(sequence_field)
        if isinstance(value, int):
            return value
    return None


class BinanceWebSocketClient:
    """A persistent, auto-reconnecting WebSocket client for a single
    Binance stream URL."""

    def __init__(
        self,
        *,
        url: str,
        event_bus: EventBus,
        on_message: Callable[[str], Awaitable[None]],
        heartbeat_interval_seconds: float = 30.0,
        stale_threshold_seconds: float = 60.0,
        stale_check_interval_seconds: float = 1.0,
        sequence_field: str | None = None,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._url = url
        self._event_bus = event_bus
        self._on_message = on_message
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._stale_threshold_seconds = stale_threshold_seconds
        self._stale_check_interval_seconds = stale_check_interval_seconds
        self._sequence_field = sequence_field
        self._initial_backoff_seconds = initial_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._monotonic = monotonic
        self._now = now
        self._sleep = sleep
        self._jitter = jitter

        self._stopped = False
        self._last_message_at: float = 0.0
        self._last_sequence: int | None = None
        self.connection_attempts = 0

    async def run(self) -> None:
        """Connect and stay connected until `stop()` is called, backing
        off and reconnecting automatically after any disconnect."""
        attempt = 0
        while not self._stopped:
            self.connection_attempts += 1
            try:
                await self._run_connection()
            except Exception as exc:  # noqa: BLE001 — any failure here means "reconnect"
                _logger.warning("websocket_connection_error", error=str(exc))

            if self._stopped:
                return

            delay = backoff_delay(
                attempt,
                initial_seconds=self._initial_backoff_seconds,
                max_seconds=self._max_backoff_seconds,
                jitter=self._jitter,
            )
            await self._sleep(delay)
            attempt += 1

    def stop(self) -> None:
        """Signal the run loop to exit after the current connection ends."""
        self._stopped = True

    async def _run_connection(self) -> None:
        async with websockets.connect(self._url) as ws:
            self._last_message_at = self._monotonic()
            heartbeat_task = asyncio.ensure_future(self._heartbeat_loop(ws))
            watchdog_task = asyncio.ensure_future(self._stale_watchdog(ws))
            try:
                async for raw_message in ws:
                    self._last_message_at = self._monotonic()
                    if not isinstance(raw_message, str):
                        # Binance streams are JSON text frames; a binary
                        # frame is unexpected and has nothing this client
                        # knows how to interpret.
                        continue
                    self._track_sequence(raw_message)
                    await self._on_message(raw_message)
            finally:
                heartbeat_task.cancel()
                watchdog_task.cancel()

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await self._sleep(self._heartbeat_interval_seconds)
            await ws.ping()

    async def _stale_watchdog(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await self._sleep(self._stale_check_interval_seconds)
            idle_seconds = self._monotonic() - self._last_message_at
            if idle_seconds > self._stale_threshold_seconds:
                await self._event_bus.publish(
                    _FEED_STALE_TOPIC,
                    FeedStale(idle_seconds=idle_seconds, detected_at=self._now()),
                )
                await ws.close()
                return

    def _track_sequence(self, raw_message: str) -> None:
        if self._sequence_field is None:
            return
        try:
            payload = json.loads(raw_message)
        except ValueError:
            return
        sequence = _extract_sequence(payload, self._sequence_field)
        if sequence is None:
            return
        if self._last_sequence is not None and sequence != self._last_sequence + 1:
            _logger.warning(
                "sequence_gap_detected",
                previous_sequence=self._last_sequence,
                received_sequence=sequence,
            )
        self._last_sequence = sequence
