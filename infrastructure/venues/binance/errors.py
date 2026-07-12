"""Exception hierarchy for `infrastructure/venues/binance/`.

ARCHITECTURE.md §8.5's failure-recovery table is explicit that a few
Binance failure modes must never be conflated with an ordinary,
retryable error: a 429 means "halt submissions; drain budget; resume on
reset", and a 418 means "halt all trading. Page. Manual intervention" —
neither is "retry and hope." Distinct exception *types* (rather than a
single error with a message a caller would have to string-match) are
what let calling code `except VenueIPBanError` and know, at the type
level, that this is not something to retry.

A full Retryable/Terminal/Unknown classification across every documented
Binance error code is `BinanceErrorClassifier`'s job (T-P6-03, a later
task, not implemented here). T-P1-01 only needs the specific, distinct
types its own acceptance criteria name.
"""

from __future__ import annotations


class BinanceAPIError(Exception):
    """Base class for every error `BinanceRestClient` raises from a venue response."""


class VenueRateLimitError(BinanceAPIError):
    """HTTP 429, or this client's own proactive weight-budget backoff.

    Raised instead of retrying automatically: a 429 means the venue is
    telling the client to stop, not to try again immediately.
    """


class VenueIPBanError(BinanceAPIError):
    """HTTP 418 — the venue has banned this IP. Never retried automatically."""


class VenueServerError(BinanceAPIError):
    """A 5xx response that persisted after this client's bounded retries."""


class VenueTimestampError(BinanceAPIError):
    """Binance error code -1021: the request's timestamp fell outside the
    venue's `recvWindow`, signaling local/venue clock skew.

    Recovering from this (resync the clock, retry once) is
    `BinanceErrorClassifier`'s job (T-P6-03, not yet implemented) — this
    client only surfaces it as a distinct, identifiable error.
    """


class VenueRequestError(BinanceAPIError):
    """Any other non-success response this client has no more specific
    classification for."""
