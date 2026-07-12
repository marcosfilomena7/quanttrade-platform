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
own — that is T-P1-08's job, built on top of this client's `on_message`
callback.
"""

from __future__ import annotations

from infrastructure.venues.binance.client import BinanceRestClient
from infrastructure.venues.binance.errors import (
    BinanceAPIError,
    VenueIPBanError,
    VenueRateLimitError,
    VenueRequestError,
    VenueServerError,
    VenueTimestampError,
)
from infrastructure.venues.binance.models import (
    AccountBalance,
    AccountResponse,
    ExchangeInfoResponse,
    ExchangeSymbol,
    SymbolFilter,
)
from infrastructure.venues.binance.rate_limiter import RateLimitTracker
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
]
