"""Tests for infrastructure/clock.py (TASKS.md T-P2-01)."""

from __future__ import annotations

import warnings
from datetime import UTC, datetime, timedelta

import pytest

from domain.ports import Clock
from infrastructure.clock import (
    ClockError,
    ClockNotInitialized,
    ClockRegressionError,
    RealClock,
    SimulatedClock,
)


def test_simulated_clock_now_returns_exactly_the_value_last_set_by_advance_to() -> None:
    """TASKS.md T-P2-01 acceptance criterion, verbatim: "SimulatedClock
    .now() returns exactly the value last set by advance_to().\""""
    clock = SimulatedClock()
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock.advance_to(ts)
    assert clock.now() == ts

    later = ts + timedelta(minutes=1)
    clock.advance_to(later)
    assert clock.now() == later


def test_calling_now_before_any_advance_to_raises_clock_not_initialized() -> None:
    """TASKS.md T-P2-01 acceptance criterion, verbatim: "Calling now()
    before any advance_to() raises ClockNotInitialized.\""""
    clock = SimulatedClock()
    with pytest.raises(ClockNotInitialized):
        clock.now()


def test_clock_not_initialized_is_a_clock_error() -> None:
    assert issubclass(ClockNotInitialized, ClockError)


def test_advance_to_with_an_earlier_time_raises_clock_regression_error() -> None:
    """TASKS.md T-P2-01 acceptance criterion, verbatim: "advance_to()
    with a time earlier than current raises ClockRegressionError.\""""
    clock = SimulatedClock()
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock.advance_to(ts)

    with pytest.raises(ClockRegressionError):
        clock.advance_to(ts - timedelta(seconds=1))

    # A rejected advance_to() must not have mutated the clock's state.
    assert clock.now() == ts


def test_clock_regression_error_is_a_clock_error() -> None:
    assert issubclass(ClockRegressionError, ClockError)


def test_advance_to_with_the_same_time_is_allowed_not_a_regression() -> None:
    """Several events can legitimately share one timestamp (T-P2-03's
    own multi-symbol/multi-timeframe event merge) — advancing to the
    *same* instant again must not raise."""
    clock = SimulatedClock()
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock.advance_to(ts)
    clock.advance_to(ts)
    assert clock.now() == ts


def test_advance_to_strictly_forward_repeatedly_is_allowed() -> None:
    clock = SimulatedClock()
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        clock.advance_to(ts + timedelta(minutes=i))
    assert clock.now() == ts + timedelta(minutes=4)


def test_real_clock_now_returns_timezone_aware_utc_within_one_second_of_datetime_utcnow() -> (
    None
):
    """TASKS.md T-P2-01 acceptance criterion, verbatim: "RealClock.now()
    returns a timezone-aware UTC datetime within 1 second of
    datetime.utcnow().\""""
    clock = RealClock()
    result = clock.now()

    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        reference = datetime.utcnow()  # noqa: DTZ003 — the AC names this exact call
    delta = abs((result.replace(tzinfo=None) - reference).total_seconds())
    assert delta < 1.0


def test_both_clocks_implement_the_clock_protocol_and_are_interchangeable() -> None:
    """TASKS.md T-P2-01 acceptance criterion, verbatim: "Both implement
    the Clock protocol and are interchangeable.\""""
    assert isinstance(RealClock(), Clock)

    simulated = SimulatedClock()
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    simulated.advance_to(fixed)
    assert isinstance(simulated, Clock)

    def read_time(clock: Clock) -> datetime:
        return clock.now()

    assert isinstance(read_time(RealClock()), datetime)
    assert read_time(simulated) == fixed
