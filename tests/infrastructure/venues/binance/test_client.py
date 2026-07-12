"""Tests for infrastructure/venues/binance/client.py (BinanceRestClient).

TASKS.md T-P1-01's acceptance criteria, verbatim:
  - "Unit tests replay recorded HTTP fixtures (VCR / `httpx` mock
    transport) covering: success, 429 rate-limit, 418 IP ban, 5xx retry,
    `-1021` timestamp error." -> covered below via `httpx.MockTransport`,
    the acceptance criterion's own named alternative to VCR.
  - "A 429 response triggers backoff; the client does not immediately
    retry." -> `test_429_...`.
  - "A 418 response raises `VenueIPBanError` (not retried
    automatically)." -> `test_418_...`.
  - "All typed response fields use Decimal for price and quantity
    fields, never float." -> see test_models.py; also spot-checked here
    for the actual client return values.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import httpx
import pytest

from infrastructure.venues.binance.client import BinanceRestClient
from infrastructure.venues.binance.errors import (
    VenueIPBanError,
    VenueRateLimitError,
    VenueRequestError,
    VenueServerError,
    VenueTimestampError,
)

_EXCHANGE_INFO_BODY = {
    "timezone": "UTC",
    "serverTime": 1735689600000,
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "baseAssetPrecision": 8,
            "quoteAssetPrecision": 8,
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.01000000",
                    "maxPrice": "1000000.00000000",
                    "tickSize": "0.01000000",
                },
            ],
        }
    ],
}

_API_KEY_HEADER = "x-mbx-apikey"

_ACCOUNT_BODY = {
    "makerCommission": 10,
    "takerCommission": 10,
    "canTrade": True,
    "canWithdraw": False,
    "canDeposit": True,
    "updateTime": 1735689600000,
    "accountType": "SPOT",
    "balances": [{"asset": "BTC", "free": "1.50000000", "locked": "0.00000000"}],
}


class _FakeClock:
    """A settable, injectable `now`. Advances only when told to."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str = "test-api-key",
    api_secret: str = "test-api-secret",
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    **kwargs: object,
) -> BinanceRestClient:
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.binance.com"
    )
    clock = now or (lambda: datetime(2026, 1, 1, tzinfo=UTC))
    return BinanceRestClient(
        http_client=http_client,
        api_key=api_key,
        api_secret=api_secret,
        now=clock,
        sleep=sleep or (lambda _seconds: None),
        **kwargs,  # type: ignore[arg-type]
    )


# --- success ----------------------------------------------------------------


def test_get_exchange_info_success_returns_typed_model_not_a_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/exchangeInfo"
        return httpx.Response(200, json=_EXCHANGE_INFO_BODY)

    client = _client(handler)
    result = client.get_exchange_info()

    assert not isinstance(result, dict)
    assert result.symbols[0].symbol == "BTCUSDT"
    tick_size = result.symbols[0].filters[0].tick_size
    assert tick_size == Decimal("0.01000000")
    assert isinstance(tick_size, Decimal)


def test_get_exchange_info_is_unsigned_and_sends_no_api_key_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert _API_KEY_HEADER not in request.headers
        assert "signature" not in request.url.params
        return httpx.Response(200, json=_EXCHANGE_INFO_BODY)

    client = _client(handler)
    client.get_exchange_info()


def test_get_account_success_returns_typed_model_with_decimal_balances() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ACCOUNT_BODY)

    client = _client(handler)
    result = client.get_account()

    assert not isinstance(result, dict)
    assert result.balances[0].free == Decimal("1.50000000")
    assert isinstance(result.balances[0].free, Decimal)


# --- HMAC-SHA256 signing ------------------------------------------------


def test_get_account_signs_the_exact_query_string_it_sends() -> None:
    api_secret = "s3cr3t-signing-key"
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        assert request.headers[_API_KEY_HEADER] == "test-api-key"
        return httpx.Response(200, json=_ACCOUNT_BODY)

    client = _client(handler, api_secret=api_secret)
    client.get_account()

    signature = captured.pop("signature")
    assert "timestamp" in captured
    assert "recvWindow" in captured

    expected_query_string = urlencode(captured)
    expected_signature = hmac.new(
        api_secret.encode("utf-8"), expected_query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert signature == expected_signature


def test_get_account_uses_the_configured_recv_window() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=_ACCOUNT_BODY)

    client = _client(handler, recv_window_ms=9000)
    client.get_account()

    assert captured["recvWindow"] == "9000"


# --- 429 rate limit: backoff, no immediate retry ------------------------


def test_429_raises_rate_limit_error_and_does_not_retry_within_the_call() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"msg": "too many requests"})

    client = _client(handler)
    with pytest.raises(VenueRateLimitError):
        client.get_exchange_info()

    assert call_count == 1


def test_429_backoff_blocks_a_subsequent_call_without_a_new_http_request() -> None:
    call_count = 0
    clock = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"msg": "too many requests"})

    client = _client(handler, now=clock)

    with pytest.raises(VenueRateLimitError):
        client.get_exchange_info()
    assert call_count == 1

    clock.advance(1)  # still well within the 30s Retry-After window
    with pytest.raises(VenueRateLimitError, match="backoff"):
        client.get_exchange_info()
    assert call_count == 1  # no new HTTP request was made


def test_429_backoff_expires_after_the_retry_after_window() -> None:
    call_count = 0
    clock = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "30"}, json={"msg": "slow down"})
        return httpx.Response(200, json=_EXCHANGE_INFO_BODY)

    client = _client(handler, now=clock)

    with pytest.raises(VenueRateLimitError):
        client.get_exchange_info()
    assert call_count == 1

    clock.advance(31)  # past the 30s Retry-After window
    result = client.get_exchange_info()
    assert call_count == 2
    assert result.symbols[0].symbol == "BTCUSDT"


def test_tracked_used_weight_triggers_proactive_backoff_before_any_429() -> None:
    """"Back off when approaching the limit" — a purely client-side
    decision based on a previously observed X-MBX-USED-WEIGHT-1M header,
    with no 429 ever returned by the venue."""
    call_count = 0
    clock = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200, headers={"X-MBX-USED-WEIGHT-1M": "1150"}, json=_EXCHANGE_INFO_BODY
        )

    client = _client(handler, now=clock, weight_limit=1200, backoff_ratio=0.9)
    client.get_exchange_info()
    assert call_count == 1

    with pytest.raises(VenueRateLimitError, match="used weight"):
        client.get_exchange_info()
    assert call_count == 1  # refused locally, no second HTTP request

    clock.advance(61)  # the tracked weight goes stale after the 1-minute window
    client.get_exchange_info()
    assert call_count == 2


# --- 418 IP ban: never retried ----------------------------------------------


def test_418_raises_venue_ip_ban_error_and_is_never_retried() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(418, json={"msg": "IP banned until 1735689600000"})

    client = _client(handler)
    with pytest.raises(VenueIPBanError):
        client.get_exchange_info()

    assert call_count == 1


# --- 5xx: retried up to a bound, then raises --------------------------------


def test_5xx_is_retried_and_eventually_succeeds() -> None:
    call_count = 0
    sleep_calls: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503, json={"msg": "service unavailable"})
        return httpx.Response(200, json=_EXCHANGE_INFO_BODY)

    client = _client(handler, sleep=sleep_calls.append, max_attempts=3)
    result = client.get_exchange_info()

    assert call_count == 3
    assert result.symbols[0].symbol == "BTCUSDT"
    assert len(sleep_calls) == 2  # slept between attempts 1->2 and 2->3, not after success


def test_5xx_raises_venue_server_error_after_exhausting_retries() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"msg": "internal error"})

    client = _client(handler, sleep=lambda _seconds: None, max_attempts=3)
    with pytest.raises(VenueServerError):
        client.get_exchange_info()

    assert call_count == 3


# --- -1021 timestamp error ---------------------------------------------


def test_minus_1021_raises_venue_timestamp_error() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            400,
            json={"code": -1021, "msg": "Timestamp for this request is outside of the recvWindow."},
        )

    client = _client(handler)
    with pytest.raises(VenueTimestampError, match="recvWindow"):
        client.get_account()

    assert call_count == 1  # not retried automatically (T-P6-03 owns resync-and-retry)


# --- anything else: a clear, distinct fallback error ------------------------


def test_unclassified_error_status_raises_venue_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    client = _client(handler)
    with pytest.raises(VenueRequestError, match="Invalid symbol"):
        client.get_exchange_info()
