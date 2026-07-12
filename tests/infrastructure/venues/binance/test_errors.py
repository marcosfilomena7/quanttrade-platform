"""Tests for infrastructure/venues/binance/errors.py — the exception hierarchy."""

from __future__ import annotations

import pytest

from infrastructure.venues.binance.errors import (
    BinanceAPIError,
    VenueIPBanError,
    VenueRateLimitError,
    VenueRequestError,
    VenueServerError,
    VenueTimestampError,
)


@pytest.mark.parametrize(
    "exc_type",
    [
        VenueIPBanError,
        VenueRateLimitError,
        VenueRequestError,
        VenueServerError,
        VenueTimestampError,
    ],
)
def test_every_specific_error_is_a_binance_api_error(exc_type: type[Exception]) -> None:
    assert issubclass(exc_type, BinanceAPIError)


def test_venue_ip_ban_error_is_distinguishable_from_rate_limit_error() -> None:
    """A caller must be able to tell an IP ban (never retried) apart from a
    rate limit (retryable after backoff) purely by exception type."""
    assert not issubclass(VenueIPBanError, VenueRateLimitError)
    assert not issubclass(VenueRateLimitError, VenueIPBanError)


def test_errors_carry_a_human_readable_message() -> None:
    error = VenueTimestampError("timestamp outside recvWindow")
    assert str(error) == "timestamp outside recvWindow"
