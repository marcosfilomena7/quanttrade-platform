"""BinanceRestClient — signed, rate-limit-aware REST client for Binance Spot.

TASKS.md T-P1-01: "Implement a `BinanceRestClient` ... using `httpx`.
Enforce Binance's weight-based rate limit client-side ... Sign requests
with HMAC-SHA256. Return typed response models (Pydantic v2), never raw
dicts."

Design decisions, and why:

- **The `httpx.Client` is injected, not constructed internally** —
  mirrors `infrastructure/secrets/vault.py`'s `VaultSecretsClient`
  (T-P0-09): the caller owns the transport (base URL, timeouts), and
  tests substitute an `httpx.MockTransport` for a real network call.
  This is also literally one of the two techniques the acceptance
  criteria names ("VCR / `httpx` mock transport").
- **`api_key`/`api_secret` are plain strings, not a `SecretsClient`.**
  ARCHITECTURE.md §3.6 / T-P0-09 requires that *config loading* call
  `SecretsClient`, never read a credential from `os.environ` directly.
  This class is not itself "config loading" — whatever assembles it at
  startup is expected to call `SecretsClient.get(...)` and pass the
  resulting plain values in, the same way `VaultSecretsClient` takes a
  plain `secret_path` rather than owning secret retrieval itself.
  Requiring a `SecretsClient` here would force every test to also
  construct one for a dependency this class does not otherwise need.
- **`now` is an injectable `Callable[[], datetime]`, not the domain
  `Clock` port.** `Clock` (T-P0-07) exists, but its production adapter,
  `RealClock`, is not implemented until T-P2-01 — a later task.
  ARCHITECTURE.md §4.7's "no wall clock" rule scopes determinism to
  strategies/domain objects whose decisions must replay identically in
  a backtest; this client is a live-only infrastructure adapter that
  *needs* the real wall clock to satisfy Binance's own `recvWindow`
  timestamp validation, and is never exercised during a backtest at all
  (backtests run against a simulated venue, not a real REST client). An
  injectable callable gives full test determinism without depending on
  infrastructure (`RealClock`) that does not exist yet.
- **`sleep` is likewise an injectable `Callable[[float], None]`**,
  defaulting to `time.sleep`, purely so retry-backoff tests don't spend
  real wall-clock time waiting.
- **The exact string this client signs is the exact string it sends.**
  The query string is built once with `urllib.parse.urlencode` and
  handed to `httpx` as a literal URL, rather than as a `params=` dict
  for `httpx` to re-serialize — avoiding any risk that `httpx`'s own
  encoding differs byte-for-byte from what was signed, which would
  produce a signature Binance rejects.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from infrastructure.venues.binance.errors import (
    VenueIPBanError,
    VenueRateLimitError,
    VenueRequestError,
    VenueServerError,
    VenueTimestampError,
)
from infrastructure.venues.binance.models import AccountResponse, ExchangeInfoResponse
from infrastructure.venues.binance.rate_limiter import RateLimitTracker

_TIMESTAMP_ERROR_CODE = -1021
_USED_WEIGHT_HEADER = "X-MBX-USED-WEIGHT-1M"
_API_KEY_HEADER = "X-MBX-APIKEY"
_DEFAULT_RETRY_AFTER_SECONDS = 60.0


class BinanceRestClient:
    """Signed, rate-limit-aware REST client for the Binance Spot API."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        api_key: str,
        api_secret: str,
        recv_window_ms: int = 5000,
        weight_limit: int = 1200,
        backoff_ratio: float = 0.9,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.5,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._recv_window_ms = recv_window_ms
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._now = now
        self._sleep = sleep
        self._rate_limiter = RateLimitTracker(
            weight_limit=weight_limit, backoff_ratio=backoff_ratio
        )
        self._blocked_until: datetime | None = None

    # --- Public API -------------------------------------------------------

    def get_exchange_info(self) -> ExchangeInfoResponse:
        """`GET /api/v3/exchangeInfo` — public, unsigned."""
        response = self._request("GET", "/api/v3/exchangeInfo", signed=False)
        return ExchangeInfoResponse.model_validate(response.json())

    def get_account(self) -> AccountResponse:
        """`GET /api/v3/account` — signed; requires a valid API key/secret."""
        response = self._request("GET", "/api/v3/account", signed=True)
        return AccountResponse.model_validate(response.json())

    # --- Request plumbing ---------------------------------------------------

    def _request(self, method: str, path: str, *, signed: bool) -> httpx.Response:
        now = self._now()
        self._raise_if_in_backoff(now)
        url = self._build_url(path, now, signed=signed)
        headers = {_API_KEY_HEADER: self._api_key} if signed else {}

        attempt = 0
        while True:
            attempt += 1
            response = self._http.request(method, url, headers=headers)
            self._rate_limiter.observe(response.headers.get(_USED_WEIGHT_HEADER), self._now())

            if response.status_code == 200:
                return response

            if response.status_code == 429:
                self._enter_backoff(response, now=self._now())
                raise VenueRateLimitError(f"{method} {path} was rate-limited (429); backing off")

            if response.status_code == 418:
                raise VenueIPBanError(f"{method} {path} returned 418 — IP banned by the venue")

            if self._error_code(response) == _TIMESTAMP_ERROR_CODE:
                raise VenueTimestampError(
                    f"{method} {path} rejected on timestamp/recvWindow "
                    f"(code {_TIMESTAMP_ERROR_CODE}): {self._error_message(response)}"
                )

            if 500 <= response.status_code < 600:
                if attempt >= self._max_attempts:
                    raise VenueServerError(
                        f"{method} {path} failed with status {response.status_code} "
                        f"after {attempt} attempt(s)"
                    )
                self._sleep(self._retry_backoff_seconds * attempt)
                continue

            raise VenueRequestError(
                f"{method} {path} returned unexpected status {response.status_code}: "
                f"{self._error_message(response)}"
            )

    def _build_url(self, path: str, now: datetime, *, signed: bool) -> str:
        if not signed:
            return path

        query = {"timestamp": int(now.timestamp() * 1000), "recvWindow": self._recv_window_ms}
        query_string = urlencode(query)
        signature = self._sign(query_string)
        return f"{path}?{query_string}&signature={signature}"

    def _sign(self, query_string: str) -> str:
        return hmac.new(self._api_secret, query_string.encode("utf-8"), hashlib.sha256).hexdigest()

    def _raise_if_in_backoff(self, now: datetime) -> None:
        if self._blocked_until is not None:
            if now < self._blocked_until:
                raise VenueRateLimitError(
                    f"in local rate-limit backoff until {self._blocked_until.isoformat()}"
                )
            self._blocked_until = None

        if self._rate_limiter.should_back_off(now):
            raise VenueRateLimitError(
                f"tracked used weight ({self._rate_limiter.used_weight}/"
                f"{self._rate_limiter.weight_limit}) is approaching the configured limit; "
                "refusing to send another request until the window resets"
            )

    def _enter_backoff(self, response: httpx.Response, *, now: datetime) -> None:
        self._blocked_until = now + timedelta(seconds=self._retry_after_seconds(response))

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        header = response.headers.get("Retry-After")
        if header is None:
            return _DEFAULT_RETRY_AFTER_SECONDS
        try:
            return float(header)
        except ValueError:
            return _DEFAULT_RETRY_AFTER_SECONDS

    @staticmethod
    def _error_code(response: httpx.Response) -> int | None:
        body = BinanceRestClient._safe_json(response)
        if isinstance(body, dict):
            code = body.get("code")
            if isinstance(code, int):
                return code
        return None

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        body = BinanceRestClient._safe_json(response)
        if isinstance(body, dict) and isinstance(body.get("msg"), str):
            return str(body["msg"])
        return response.text

    @staticmethod
    def _safe_json(response: httpx.Response) -> object | None:
        try:
            body: object = response.json()
        except ValueError:
            return None
        return body
