"""GapFillingCandleStream — WebSocket reconnect with REST gap-fill
(TASKS.md T-P1-09).

"On every reconnect, unconditionally trigger a REST backfill of the
interval `[last_known_bar_ts, now]` before resuming WS consumption. This
prevents silent data gaps from reconnects. The sequence: detect
disconnect → backoff → reconnect → REST gap-fill → validate → resume
publishing. Gap-fill must complete before resuming event publication."

Design decisions, and why:

- **Wraps T-P1-08's `BinanceCandleStream` unmodified, by implementing
  `EventBus` itself.** `GapFillingCandleStream` constructs its own inner
  `BinanceCandleStream` and passes `self` as that inner stream's
  `event_bus` — so every `CandleClosed` publish the inner stream makes
  is intercepted by `GapFillingCandleStream.publish` first. This is what
  lets publication be gated ("remains halted") without changing a single
  line of T-P1-07 or T-P1-08: both are reused exactly as they already
  are, composed rather than modified.
- **Reconnect is detected via `BinanceWebSocketClient.connection_attempts`
  (T-P1-07), not a new hook on that client.** T-P1-07's `run()` increments
  this counter once per `_run_connection()` call — including the very
  first one — and that file is not touched here. A change in this
  counter between two `on_message` invocations means a new TCP
  connection generation has started since the last message was
  processed, i.e. "we just reconnected" (possibly through several failed
  attempts in between, if the counter jumped by more than one — the
  gap-fill range still correctly covers the whole elapsed gap either
  way, since it's anchored to `last_known_bar_ts`, not to a specific
  attempt count). The very first connection (0 → 1) is deliberately
  *not* treated as a reconnect: T-P1-09's own wording is "on every
  reconnect" — nothing could have gone missing before any connection has
  ever been established.
- **Two-phase construction (`bind()`), not a constructor cycle.** This
  class needs to read the client's `connection_attempts`; the client
  needs `self.on_message` as its `on_message` callback. Neither object
  can be fully constructed before the other exists, so `bind(client)` is
  called once, after both are constructed, before `client.run()` is
  awaited. This is the honest shape of the dependency, not an invented
  abstraction — the alternative (this class constructing and owning a
  `BinanceWebSocketClient` internally) would mean re-exposing every one
  of that client's own constructor parameters (heartbeat interval,
  staleness threshold, backoff, sequence field, injectable clocks, ...)
  through this class too, which is more coupling, not less.
- **`gap_fill` is an injected async callable, not a concrete REST/DB
  composition inside this class.** This class's own literal scope is
  the *sequencing* T-P1-09 describes ("detect disconnect → backoff →
  reconnect → REST gap-fill → validate → resume publishing") — not a
  reimplementation of *how* gap-fill or validation work, which are
  already T-P1-04's `backfill_candles` and T-P1-05's
  `run_validation_suite`, unmodified. `make_gap_fill` (below) is the
  composition root that wires those two together into the single
  callable this class calls.
- **`make_gap_fill` offloads the blocking DB/HTTP calls via
  `asyncio.to_thread`.** `backfill_candles` and `run_validation_suite`
  are synchronous, blocking SQLAlchemy/httpx calls (the established
  convention across every job in `infrastructure/jobs/` and
  `infrastructure/validation/`). Calling them directly from inside
  `on_message` — itself running on the same asyncio event loop as
  `BinanceWebSocketClient`'s heartbeat and staleness-watchdog tasks —
  would block that loop for the entire gap-fill duration. Running them
  on a worker thread keeps the event loop free.
- **`backfill_candles`/`run_validation_suite` are imported inside
  `make_gap_fill`, not at module level.** `infrastructure.jobs
  .ohlcv_backfill_job` itself imports `infrastructure.venues.binance
  .client`, which — the first time anything imports it — must first
  fully execute this package's own `__init__.py`. If this module
  imported `infrastructure.jobs.ohlcv_backfill_job` at module level,
  that would run *while* this package's `__init__.py` is still
  mid-execution (importing this very module), re-entering
  `ohlcv_backfill_job` before it has finished defining
  `backfill_candles` — an `ImportError` on a partially-initialized
  module. Deferring the import to `make_gap_fill`'s call time (by which
  point both packages have long finished their own top-level imports)
  avoids the cycle without reordering or modifying either existing
  module.
- **A missing `last_known_bar_ts` skips gap-fill entirely.** If no
  candle has ever been published yet (a reconnect before the very first
  `CandleClosed`), there is no known prior state for anything to have
  gone missing relative to — T-P1-09's own acceptance criteria describe
  "the disconnected interval" and "the interval `[last_known_bar_ts,
  now]`", both of which presuppose a `last_known_bar_ts` exists. This is
  a narrow edge case none of the three acceptance criteria exercise;
  publication simply resumes immediately in this case rather than
  gap-filling an undefined range.
- **Any gap-fill failure — REST 5xx via `VenueServerError`, or anything
  else — keeps publication halted and is logged at `error`, not
  `warning`, level.** TASKS.md's own acceptance criterion names a REST
  5xx specifically, but the code catches broadly (matching T-P1-07's
  own "any failure here means reconnect" precedent in `run()`,
  generalized here to "any failure here means stay halted"): the
  contract this class must uphold — never resume publishing candles
  that might have a silent gap behind them — cannot depend on exactly
  which exception type a given failure happens to raise. `error` (not
  `warning`) reflects that this is an ongoing suppression of live market
  data, not a transient, already-recovered condition.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
import structlog

from domain.candle import Candle
from domain.ports.event_bus import EventBus
from infrastructure.venues.binance.candle_stream import BinanceCandleStream, CandleClosed
from infrastructure.venues.binance.client import BinanceRestClient
from infrastructure.venues.binance.websocket_client import BinanceWebSocketClient

_logger = structlog.get_logger()


def make_gap_fill(
    *,
    rest_client: BinanceRestClient,
    conn: sa.Connection,
    venue_id: UUID,
    instrument_id: UUID,
    symbol: str,
    interval: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Callable[[datetime, datetime], Awaitable[None]]:
    """Builds a `gap_fill` callable for `GapFillingCandleStream`: T-P1-04's
    REST backfill followed by T-P1-05's sequence re-validation over the
    same range — the literal "REST gap-fill → validate" sequence T-P1-09
    names. Both calls run on a worker thread via `asyncio.to_thread` so
    the caller's event loop is never blocked while a gap-fill is in
    flight. Propagates whatever `backfill_candles` or `run_validation_suite`
    raise (e.g. `VenueServerError` for a persistent REST 5xx).
    """
    from infrastructure.jobs.ohlcv_backfill_job import backfill_candles
    from infrastructure.validation.candle_validation import run_validation_suite

    def _run_gap_fill_and_validate(range_start: datetime, range_end: datetime) -> None:
        backfill_candles(
            rest_client=rest_client,
            conn=conn,
            venue_id=venue_id,
            instrument_id=instrument_id,
            symbol=symbol,
            interval=interval,
            range_start=range_start,
            range_end=range_end,
            now=now,
        )
        run_validation_suite(
            conn,
            instrument_id=instrument_id,
            interval=interval,
            window_start=range_start,
            window_end=range_end,
            now=now,
        )

    async def gap_fill(range_start: datetime, range_end: datetime) -> None:
        await asyncio.to_thread(_run_gap_fill_and_validate, range_start, range_end)

    return gap_fill


class GapFillingCandleStream:
    """Reconnect-aware wrapper around `BinanceCandleStream` (T-P1-08):
    on every WS reconnect, runs `gap_fill([last_known_bar_ts, now])`
    before allowing any further `CandleClosed` events to reach the real
    `EventBus`. Must be `bind()`-ed to the `BinanceWebSocketClient`
    (T-P1-07) instance whose `on_message` it serves, before that
    client's `run()` is awaited.
    """

    def __init__(
        self,
        *,
        instrument_id: UUID,
        event_bus: EventBus,
        gap_fill: Callable[[datetime, datetime], Awaitable[None]],
        on_partial_candle: Callable[[Candle], Awaitable[None]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._downstream_bus = event_bus
        self._gap_fill = gap_fill
        self._now = now
        self._inner = BinanceCandleStream(
            instrument_id=instrument_id,
            event_bus=self,
            on_partial_candle=on_partial_candle,
            now=now,
        )
        self._client: BinanceWebSocketClient | None = None
        self._last_seen_connection_attempts = 0
        self._halted = False
        self.last_known_bar_ts: datetime | None = None
        self.gap_fill_failures = 0

    def bind(self, client: BinanceWebSocketClient) -> None:
        """Must be called once, after both objects are constructed, and
        before `client.run()` is awaited."""
        self._client = client

    async def publish(self, topic: str, event: object) -> None:
        """Implements `EventBus.publish`. Called by the inner
        `BinanceCandleStream` for every closed candle; drops it instead
        of forwarding it while `self._halted` is `True`."""
        if self._halted:
            return
        if isinstance(event, CandleClosed):
            self.last_known_bar_ts = event.candle.open_time
        await self._downstream_bus.publish(topic, event)

    async def subscribe(
        self, topic: str, handler: Callable[[object], Awaitable[None]]
    ) -> None:
        """Implements `EventBus.subscribe` by delegating to the real bus."""
        await self._downstream_bus.subscribe(topic, handler)

    async def on_message(self, raw: str) -> None:
        if self._client is None:
            raise RuntimeError("GapFillingCandleStream.bind(client) was never called")

        current_attempts = self._client.connection_attempts
        if current_attempts != self._last_seen_connection_attempts:
            is_reconnect = self._last_seen_connection_attempts != 0
            self._last_seen_connection_attempts = current_attempts
            if is_reconnect:
                await self._handle_reconnect()

        await self._inner.on_message(raw)

    async def _handle_reconnect(self) -> None:
        self._halted = True

        if self.last_known_bar_ts is None:
            self._halted = False
            return

        try:
            await self._gap_fill(self.last_known_bar_ts, self._now())
        except Exception as exc:  # noqa: BLE001 — any gap-fill failure keeps publication halted
            self.gap_fill_failures += 1
            _logger.error(
                "gap_fill_failed_publication_remains_halted",
                error=str(exc),
                last_known_bar_ts=self.last_known_bar_ts.isoformat(),
            )
            return

        self._halted = False
