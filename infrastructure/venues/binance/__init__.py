"""Binance Spot REST + WebSocket clients (TASKS.md T-P1-01, T-P1-07).

`BinanceRestClient` is a plain, rate-limit-aware, HMAC-signing REST client
— not yet a `VenuePort` adapter. The full `BinanceVenueAdapter`
implementing `VenuePort` (submit, cancel, get_positions, ...) is a later,
separate task (T-P6-01) that will build on top of this client. For now,
this exists to serve the data-engine jobs that need it next: the
reference-data importer (T-P1-02, `GET /api/v3/exchangeInfo`) and the
OHLCV backfill job (T-P1-04).

`BinanceWebSocketClient` is the persistent-connection counterpart
(T-P1-07): heartbeat, sequence tracking, staleness detection, and
backoff+jitter reconnect. It has no message-normalization logic of its
own — that is `BinanceCandleStream`'s job (T-P1-08), built on top of
this client's `on_message` callback.

`BinanceCandleStream` (T-P1-08) normalizes raw kline WS frames into
`Candle` objects, stamps `exchange_ts`/`local_recv_ts`, and publishes a
`CandleClosed` event per closed bar. It does not itself own a WS
connection, REST gap-fill on reconnect (T-P1-09), or a Redis-backed
latest-tick cache — none of those are in its own task's scope.

`GapFillingCandleStream` (T-P1-09) wraps `BinanceCandleStream` so that
every WS reconnect triggers a REST gap-fill (T-P1-04's `backfill_candles`)
plus a sequence re-validation (T-P1-05's `run_validation_suite`) — via
the `make_gap_fill` factory — before candle publication resumes.

`SymbolStalenessWatchdog` (T-P1-10) is a connection-agnostic, per-symbol
staleness watchdog: fed via `record_received(symbol)`, it runs its own
async task and emits `SymbolFeedStale` (distinct from T-P1-07's
connection-level `FeedStale`) plus a P1-tagged log line and the
`data_staleness_seconds` metric (T-P0-08) whenever a symbol goes quiet
for longer than its configured threshold.
"""

from __future__ import annotations

from infrastructure.venues.binance.candle_stream import BinanceCandleStream, CandleClosed
from infrastructure.venues.binance.client import BinanceRestClient
from infrastructure.venues.binance.errors import (
    BinanceAPIError,
    VenueIPBanError,
    VenueRateLimitError,
    VenueRequestError,
    VenueServerError,
    VenueTimestampError,
)
from infrastructure.venues.binance.gap_fill_stream import GapFillingCandleStream, make_gap_fill
from infrastructure.venues.binance.models import (
    AccountBalance,
    AccountResponse,
    ExchangeInfoResponse,
    ExchangeSymbol,
    SymbolFilter,
)
from infrastructure.venues.binance.rate_limiter import RateLimitTracker
from infrastructure.venues.binance.staleness_watchdog import (
    SymbolFeedStale,
    SymbolStalenessWatchdog,
)
from infrastructure.venues.binance.websocket_client import (
    BinanceWebSocketClient,
    FeedStale,
    backoff_delay,
)

__all__ = [
    "BinanceRestClient",
    "BinanceAPIError",
    "VenueIPBanError",
    "VenueRateLimitError",
    "VenueRequestError",
    "VenueServerError",
    "VenueTimestampError",
    "AccountBalance",
    "AccountResponse",
    "ExchangeInfoResponse",
    "ExchangeSymbol",
    "SymbolFilter",
    "RateLimitTracker",
    "BinanceWebSocketClient",
    "FeedStale",
    "backoff_delay",
    "BinanceCandleStream",
    "CandleClosed",
    "GapFillingCandleStream",
    "make_gap_fill",
    "SymbolFeedStale",
    "SymbolStalenessWatchdog",
]
