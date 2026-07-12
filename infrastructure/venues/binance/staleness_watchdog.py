"""SymbolStalenessWatchdog — per-symbol data staleness watchdog
(TASKS.md T-P1-10).

"Implement a per-symbol watchdog independent of connection state. Every
second, check `now - last_received_ts[symbol]` against a configurable
threshold (default: 60 seconds). If exceeded, emit a `FeedStale` event,
increment `data_staleness_seconds{symbol}` Prometheus metric, and
trigger a P1 alert. The watchdog runs in a separate async task, not the
main receive loop — an open socket carrying no data must not suppress
it."

Design decisions, and why:

- **A new `SymbolFeedStale` event, not T-P1-07's `FeedStale`.** T-P1-07's
  `FeedStale` (`idle_seconds`, `detected_at`) is connection-level and
  carries no `symbol` — it can't; a single WS connection can carry
  several symbols. T-P1-10 is explicitly *per-symbol*
  ("last_received_ts[symbol]"), so its event needs a `symbol` field
  T-P1-07's type doesn't have. This is exactly the situation T-P1-07's
  own docstring anticipated: "Both modules emit the same kind of
  `FeedStale` event... without either depending on or duplicating the
  other" — same purpose, deliberately separate types, so neither module
  needs to change to accommodate the other.
- **Fully connection-agnostic: no WS client, socket, or `connected`
  flag appears anywhere in this module.** The watchdog's only input is
  `record_received(symbol)`; its own `run()` loop measures elapsed time
  since the last such call per symbol and nothing else. This isn't just
  tested — it's structurally guaranteed by this module never importing
  or referencing `BinanceWebSocketClient` or any connection-state
  concept at all, which is what makes the third acceptance criterion
  ("independent of the WS connection state variable... fires even when
  the socket object reports `connected = True`") true by construction,
  not by a runtime check that happens to ignore the flag.
- **No new Prometheus metric.** T-P0-08 already defines
  `data_staleness_seconds` as a `Histogram` labeled by `symbol`, with
  its own acceptance criterion stating it "can be updated per symbol
  without a metric per symbol being pre-declared" — exactly what this
  watchdog needs. It is imported and `.observe()`-d here, unmodified.
- **"Trigger a P1 alert" is a structured `error`-level log line carrying
  `severity="P1"`, not a PagerDuty page.** No Alertmanager/PagerDuty
  integration exists anywhere in this codebase yet — that pipeline is
  T-P5-07's job, a separate, much later task ("Alertmanager rules for
  all P1 alerts... → Alertmanager → PagerDuty"), which ARCHITECTURE.md
  itself names "data feed down > 60s" as one of the exact conditions it
  will watch for — i.e., this watchdog emitting the metric and a
  clearly P1-tagged log line *is* the producer-side half of that future
  pipeline, not a reimplementation of it. This mirrors the identical
  "alert == structured log" precedent already established by T-P1-04's
  and T-P1-06's own gap-detection "alerts."
- **Symbols are tracked dynamically, first-seen at `record_received`
  time — no pre-registration.** Matches T-P0-08's own design intent for
  `data_staleness_seconds` (no per-symbol pre-declaration needed). A
  symbol's very first `record_received` call seeds its clock at "now,"
  so it can never be flagged stale before this watchdog has ever heard
  of it.
- **No debounce.** Every check tick (default: every second) that finds a
  tracked symbol still beyond the threshold re-emits the event, metric
  observation, and P1 log line. Neither T-P1-10's description nor its
  three acceptance criteria mention suppressing repeated firings for a
  persistently stale symbol; inventing a dedup policy would be assuming
  behavior nobody asked for.
- **`threshold_seconds`/`check_interval_seconds` are public, read-only
  properties.** The first acceptance criterion ("triggers... within 65
  seconds") is, for the *default* configuration (60s threshold + a
  first check at most one 1s tick later = 61s worst case), a fact about
  the constructor's own default values — provable by reading them back,
  not by a test that actually waits 61 real seconds. The mechanism
  itself (that a stale symbol really does get flagged) is separately
  verified with a sped-up threshold, matching this repo's established
  T-P1-07 testing convention.
- **Placed alongside the rest of the realtime pipeline in
  `infrastructure/venues/binance/`**, not `infrastructure/observability/`,
  even though nothing in this module is Binance-specific. T-P1-10's own
  dependency (T-P1-08) and ARCHITECTURE.md's M2 module (which lists
  "detect stale feeds" as one of the Market Data Gateway's own
  responsibilities) both place this squarely alongside T-P1-07/08/09's
  realtime candle pipeline — the only realtime feed this platform has
  today.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from domain.ports.event_bus import EventBus
from infrastructure.observability.metrics import data_staleness_seconds

_logger = structlog.get_logger()

_SYMBOL_FEED_STALE_TOPIC = "symbol_feed_stale"


@dataclass(frozen=True)
class SymbolFeedStale:
    """Published to the `EventBus` when a specific symbol has received no
    data for longer than the watchdog's configured threshold."""

    symbol: str
    idle_seconds: float
    detected_at: datetime


class SymbolStalenessWatchdog:
    """Tracks `now - last_received_ts[symbol]` for every symbol it has
    ever seen via `record_received`, independent of any WS connection's
    own state. Runs as its own async task (`run()`), checking every
    `check_interval_seconds` (default: 1 second, per TASKS.md T-P1-10).
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        threshold_seconds: float = 60.0,
        check_interval_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._event_bus = event_bus
        self._threshold_seconds = threshold_seconds
        self._check_interval_seconds = check_interval_seconds
        self._monotonic = monotonic
        self._now = now
        self._sleep = sleep

        self._last_received_at: dict[str, float] = {}
        self._stopped = False

    @property
    def threshold_seconds(self) -> float:
        return self._threshold_seconds

    @property
    def check_interval_seconds(self) -> float:
        return self._check_interval_seconds

    def record_received(self, symbol: str) -> None:
        """Call this whenever any data (partial or closed candle, trade,
        or other market-data message) arrives for `symbol`."""
        self._last_received_at[symbol] = self._monotonic()

    def stop(self) -> None:
        """Signal `run()` to exit after its current check tick."""
        self._stopped = True

    async def run(self) -> None:
        while not self._stopped:
            await self._sleep(self._check_interval_seconds)
            if self._stopped:
                return
            await self._check_all_symbols()

    async def _check_all_symbols(self) -> None:
        now_monotonic = self._monotonic()
        for symbol, last_received_at in list(self._last_received_at.items()):
            idle_seconds = now_monotonic - last_received_at
            if idle_seconds > self._threshold_seconds:
                await self._raise_stale(symbol, idle_seconds)

    async def _raise_stale(self, symbol: str, idle_seconds: float) -> None:
        data_staleness_seconds.labels(symbol=symbol).observe(idle_seconds)
        _logger.error(
            "symbol_feed_stale_p1_alert",
            severity="P1",
            symbol=symbol,
            idle_seconds=idle_seconds,
            threshold_seconds=self._threshold_seconds,
        )
        await self._event_bus.publish(
            _SYMBOL_FEED_STALE_TOPIC,
            SymbolFeedStale(symbol=symbol, idle_seconds=idle_seconds, detected_at=self._now()),
        )
