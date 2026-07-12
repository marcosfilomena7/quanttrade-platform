"""Tests for infrastructure/venues/binance/rate_limiter.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from infrastructure.venues.binance.rate_limiter import RateLimitTracker

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_starts_with_zero_used_weight_and_no_backoff() -> None:
    tracker = RateLimitTracker(weight_limit=1200, backoff_ratio=0.9)
    assert tracker.used_weight == 0
    assert tracker.should_back_off(_T0) is False


def test_observe_records_the_reported_used_weight() -> None:
    tracker = RateLimitTracker(weight_limit=1200)
    tracker.observe("500", _T0)
    assert tracker.used_weight == 500


def test_observe_ignores_a_missing_header() -> None:
    tracker = RateLimitTracker(weight_limit=1200)
    tracker.observe(None, _T0)
    assert tracker.used_weight == 0


def test_observe_ignores_a_non_integer_header() -> None:
    tracker = RateLimitTracker(weight_limit=1200)
    tracker.observe("not-a-number", _T0)
    assert tracker.used_weight == 0


def test_should_back_off_once_threshold_is_reached() -> None:
    tracker = RateLimitTracker(weight_limit=1000, backoff_ratio=0.9)
    tracker.observe("899", _T0)
    assert tracker.should_back_off(_T0) is False
    tracker.observe("900", _T0)
    assert tracker.should_back_off(_T0) is True


def test_used_weight_resets_after_the_one_minute_window_elapses() -> None:
    tracker = RateLimitTracker(weight_limit=1000, backoff_ratio=0.9)
    tracker.observe("950", _T0)
    assert tracker.should_back_off(_T0) is True

    later = _T0 + timedelta(seconds=61)
    assert tracker.should_back_off(later) is False
    assert tracker.used_weight == 0


def test_used_weight_does_not_reset_before_the_window_elapses() -> None:
    tracker = RateLimitTracker(weight_limit=1000, backoff_ratio=0.9)
    tracker.observe("950", _T0)

    almost_a_minute_later = _T0 + timedelta(seconds=59)
    assert tracker.should_back_off(almost_a_minute_later) is True
    assert tracker.used_weight == 950


def test_a_fresh_observation_restarts_the_window() -> None:
    tracker = RateLimitTracker(weight_limit=1000, backoff_ratio=0.9)
    tracker.observe("950", _T0)

    later = _T0 + timedelta(seconds=61)
    tracker.observe("100", later)

    assert tracker.used_weight == 100
    still_within_new_window = later + timedelta(seconds=30)
    assert tracker.should_back_off(still_within_new_window) is False


@pytest.mark.parametrize("weight_limit", [0, -1])
def test_rejects_a_non_positive_weight_limit(weight_limit: int) -> None:
    with pytest.raises(ValueError, match="weight_limit"):
        RateLimitTracker(weight_limit=weight_limit)


@pytest.mark.parametrize("backoff_ratio", [0.0, -0.1, 1.1])
def test_rejects_a_backoff_ratio_outside_zero_to_one(backoff_ratio: float) -> None:
    with pytest.raises(ValueError, match="backoff_ratio"):
        RateLimitTracker(weight_limit=1200, backoff_ratio=backoff_ratio)
