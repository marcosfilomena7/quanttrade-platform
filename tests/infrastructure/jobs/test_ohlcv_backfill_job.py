"""Unit tests for infrastructure/jobs/ohlcv_backfill_job.py's pure
helpers — no database required.

The end-to-end resumability/idempotency/gap-detection behavior against a
real Postgres is covered separately by
test_ohlcv_backfill_job_integration.py (TASKS.md T-P1-04's acceptance
criteria are all database-state assertions).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from infrastructure.jobs.ohlcv_backfill_job import find_gaps, interval_to_timedelta

# --- interval_to_timedelta ---------------------------------------------


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        ("1m", timedelta(minutes=1)),
        ("5m", timedelta(minutes=5)),
        ("15m", timedelta(minutes=15)),
        ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
        ("1d", timedelta(days=1)),
        ("1w", timedelta(weeks=1)),
    ],
)
def test_interval_to_timedelta_known_intervals(interval: str, expected: timedelta) -> None:
    assert interval_to_timedelta(interval) == expected


def test_interval_to_timedelta_raises_for_an_unsupported_interval() -> None:
    with pytest.raises(ValueError, match="unsupported candle interval"):
        interval_to_timedelta("7x")


# --- find_gaps -----------------------------------------------------------


def _minutes(*minutes: int) -> list[datetime]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [base + timedelta(minutes=m) for m in minutes]


def test_find_gaps_returns_nothing_for_contiguous_bars() -> None:
    open_times = _minutes(0, 1, 2, 3)
    assert find_gaps(open_times, step=timedelta(minutes=1), previous_open_time=None) == []


def test_find_gaps_detects_a_single_missing_bar() -> None:
    open_times = _minutes(0, 1, 3)  # minute 2 is missing
    gaps = find_gaps(open_times, step=timedelta(minutes=1), previous_open_time=None)
    assert gaps == [(_minutes(1)[0], _minutes(3)[0])]


def test_find_gaps_detects_a_gap_against_a_prior_checkpoint() -> None:
    """A gap that straddles a resume boundary (the last bar from a
    previous call vs. the first bar of this one) must still be caught."""
    previous = _minutes(0)[0]
    open_times = _minutes(5)  # minutes 1-4 missing, resumed straight to 5
    gaps = find_gaps(open_times, step=timedelta(minutes=1), previous_open_time=previous)
    assert gaps == [(previous, _minutes(5)[0])]


def test_find_gaps_ignores_the_boundary_when_no_previous_open_time_given() -> None:
    # First-ever call for this range: nothing to compare minute 10 against.
    open_times = _minutes(10, 11)
    assert find_gaps(open_times, step=timedelta(minutes=1), previous_open_time=None) == []


def test_find_gaps_detects_multiple_gaps_in_one_chunk() -> None:
    open_times = _minutes(0, 2, 5)  # gap 0->2, gap 2->5
    gaps = find_gaps(open_times, step=timedelta(minutes=1), previous_open_time=None)
    assert gaps == [(_minutes(0)[0], _minutes(2)[0]), (_minutes(2)[0], _minutes(5)[0])]


def test_find_gaps_on_empty_input_is_empty() -> None:
    assert find_gaps([], step=timedelta(minutes=1), previous_open_time=None) == []
