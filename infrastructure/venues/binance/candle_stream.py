"""BinanceCandleStream — normalize, stamp, and publish realtime candles
(TASKS.md T-P1-08).

"Subscribe to Binance's `<symbol>@kline_<interval>` stream. Normalize
each WS payload to a `Candle` domain object. Stamp both `exchange_ts`
(from the venue payload) and `local_recv_ts` (local monotonic clock).
Distinguish between `is_closed = true` (full bar) and partial candles
— only publish `CandleClosed` events for closed bars. Partial candles
are buffered for the latest-tick Redis cache only."

Design decisions, and why:

- **This is an `on_message` consumer, not a new connection.** T-P1-07's
  `BinanceWebSocketClient` already owns the persistent connection,
  heartbeat, staleness detection, and reconnect; this task's own scope
  ("normalize, stamp, publish") is a pure message-to-event transform.
  `BinanceCandleStream.on_message` has exactly the signature
  `Callable[[str], Awaitable[None]]` T-P1-07's client expects for its
  `on_message` parameter — the two compose directly, with no new
  connection-handling code duplicated here.
- **Only the single-stream raw kline event shape is parsed**: `{"e":
  "kline", "E": <ms>, "s": "<SYMBOL>", "k": {"t": ..., "o": ..., ...,
  "x": <bool>}}`. This is what Binance sends on the single-stream
  endpoint (`/ws/<symbol>@kline_<interval>`) that T-P1-08's own
  description names, literally, as the stream to subscribe to. Binance's
  *combined*-stream endpoint (`/stream?streams=...`) wraps this same
  payload in an extra `{"stream": ..., "data": {...}}` envelope — a
  different subscription mode this task does not ask for, so it isn't
  handled.
- **`exchange_ts` comes from the payload's top-level `E` (event time),
  not `k.t`/`k.T` (bar open/close time).** ARCHITECTURE.md §11.3 states
  the entire reason for recording both timestamps: "a free, continuous
  measurement of latency to the venue." `k.t`/`k.T` are bar-boundary
  timestamps that stay constant across every partial update of the same
  candle — subtracting a local receive time from a constant would not
  measure anything. `E` is Binance's own timestamp for *this specific
  message*, which is what actually varies message-to-message and makes
  the exchange_ts/local_recv_ts difference a real, live latency signal.
- **`local_recv_ts` is an injectable wall-clock `now: Callable[[],
  datetime]`, not literally `time.monotonic()`**, despite the task
  description's parenthetical "(local monotonic clock)". `exchange_ts`
  is a real Binance server timestamp (epoch milliseconds); subtracting a
  `time.monotonic()` reading from it would be meaningless; monotonic
  clocks have an arbitrary, process-local epoch. Read literally,
  "monotonic" here describes the *value's behavior* — it only moves
  forward, captured immediately upon receipt, never backdated — not a
  mandate to use a clock whose epoch is incompatible with the field
  it's meant to be compared against. This mirrors T-P1-07's own
  established `now: Callable[[], datetime] = lambda: datetime.now(UTC)`
  convention (see `BinanceWebSocketClient`), reused here verbatim for
  the same reason: deterministic, injectable event timestamps.
- **No Redis integration.** The task description says partial candles
  are "buffered for the latest-tick Redis cache only," but no Redis
  client dependency or cache adapter exists anywhere in this repo yet
  (docker-compose.yml runs a Redis *container* for local dev per
  T-P0-10, but nothing in `infrastructure/` talks to it), and none of
  T-P1-08's own four acceptance criteria mention Redis or any cache at
  all — they test exactly one thing: that closed candles emit
  `CandleClosed` and partial candles don't. Adding a new `redis` runtime
  dependency and a cache adapter now would be scope far beyond what's
  asked or tested. Instead, partial candles are held as
  `self.latest_partial_candle` (satisfying "buffered," literally, with
  the simplest possible in-memory store) and forwarded to an optional
  injectable `on_partial_candle` callback — the same extension-point
  pattern T-P1-07 used for `on_message` — for whichever later task
  actually introduces a Redis-backed latest-tick cache to wire into.
- **No database persistence.** `domain.candle.Candle`'s own docstring
  (T-P0-07) already excludes `trade_count`/`source`/`inserted_at` as
  "ingestion/persistence metadata... left for whichever later task
  actually constructs and persists rows (T-P1-04/T-P1-08)." T-P1-04
  already owns REST-sourced Postgres persistence via that exact table.
  T-P1-08's four acceptance criteria only test the `CandleClosed` event
  and its two timestamps — nothing about a database row — so no write
  path to the `candle` hypertable is added here.
- **`CandleClosed` is defined here, not in `domain/`.** Identical
  reasoning to T-P1-07's `FeedStale`: `domain/` still holds no
  event/notification vocabulary (only value/entity types), and
  `EventBus.publish(topic: str, event: object)` accepts any object by
  design. A later, unrelated task (T-P2-03, "Historical Feed and Event
  Ordering") also emits a same-named `CandleClosed` for the *backtest*
  engine's own bar-merging pipeline — a different producer, a different
  subsystem, potentially a different shape. Defining this task's
  `CandleClosed` in `domain/` now would risk becoming the wrong shared
  contract for that later, unrelated task; keeping it scoped to
  `infrastructure/venues/binance/` avoids that.
- **Malformed or non-kline frames are silently ignored, never raised.**
  `BinanceWebSocketClient` has no per-message error boundary — only a
  connection-level `try/except` in `run()` that treats any exception as
  "reconnect." Letting a single unparseable frame raise out of
  `on_message` would look exactly like a connection failure and trigger
  an unnecessary, unrelated reconnect. Parsing failures (bad JSON,
  missing/malformed fields, a non-kline event type) therefore all
  resolve to "ignore this message," matching T-P1-07's own precedent of
  silently `continue`-ing past a frame it can't interpret.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from uuid import UUID

from domain.candle import Candle
from domain.ports.event_bus import EventBus

_CANDLE_CLOSED_TOPIC = "candle_closed"


@dataclass(frozen=True)
class CandleClosed:
    """Published to the `EventBus` when a full (non-partial) candle bar
    closes. `exchange_ts` and `local_recv_ts` are independent fields —
    never conflated — so their difference can be read as venue latency."""

    candle: Candle
    exchange_ts: datetime
    local_recv_ts: datetime


def _parse_kline_message(raw: str, *, instrument_id: UUID) -> tuple[Candle, datetime] | None:
    """Parse one raw Binance `<symbol>@kline_<interval>` WS text frame
    into a `(Candle, exchange_ts)` pair, or `None` if the frame isn't a
    well-formed kline event this stream can interpret."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("e") != "kline":
        return None
    kline = payload.get("k")
    if not isinstance(kline, dict):
        return None

    try:
        candle = Candle(
            instrument_id=instrument_id,
            interval=str(kline["i"]),
            open_time=datetime.fromtimestamp(int(kline["t"]) / 1000, tz=UTC),
            open=Decimal(str(kline["o"])),
            high=Decimal(str(kline["h"])),
            low=Decimal(str(kline["l"])),
            close=Decimal(str(kline["c"])),
            volume=Decimal(str(kline["v"])),
            is_closed=bool(kline["x"]),
        )
        exchange_ts = datetime.fromtimestamp(int(payload["E"]) / 1000, tz=UTC)
    except (KeyError, TypeError, ValueError, DecimalException):
        return None

    return candle, exchange_ts


class BinanceCandleStream:
    """Normalizes raw Binance kline WS frames into `Candle` objects,
    stamps them with `exchange_ts`/`local_recv_ts`, and publishes a
    `CandleClosed` event for every closed bar. Partial (still-forming)
    bars are held in `latest_partial_candle` and, if configured, handed
    to `on_partial_candle` instead of being published as events.

    Intended for direct use as a `BinanceWebSocketClient`'s `on_message`
    callback (T-P1-07): `BinanceWebSocketClient(..., on_message=stream.on_message)`.
    """

    def __init__(
        self,
        *,
        instrument_id: UUID,
        event_bus: EventBus,
        on_partial_candle: Callable[[Candle], Awaitable[None]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._instrument_id = instrument_id
        self._event_bus = event_bus
        self._on_partial_candle = on_partial_candle
        self._now = now
        self.latest_partial_candle: Candle | None = None

    async def on_message(self, raw: str) -> None:
        parsed = _parse_kline_message(raw, instrument_id=self._instrument_id)
        if parsed is None:
            return
        candle, exchange_ts = parsed

        if not candle.is_closed:
            self.latest_partial_candle = candle
            if self._on_partial_candle is not None:
                await self._on_partial_candle(candle)
            return

        await self._event_bus.publish(
            _CANDLE_CLOSED_TOPIC,
            CandleClosed(candle=candle, exchange_ts=exchange_ts, local_recv_ts=self._now()),
        )
