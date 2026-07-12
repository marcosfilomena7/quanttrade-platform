"""Unit tests for infrastructure/jobs/gap_detection.py's pure
helpers — no database required.

The end-to-end scan/log/auto-backfill behavior against a real Postgres
is covered separately by test_gap_detection_integration.py (TASKS.md
T-P1-06's acceptance criteria are all database-state assertions).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from infrastructure.jobs.gap_detection import (
    MaintenanceWindow,
    expected_open_times,
    gaps_from_expected_and_actual,
)

_BASE = datetime(2026, 1, 1, tzinfo=UTC)
_STEP = timedelta(minutes=1)


def _minutes(*minutes: int) -> list[datetime]:
    return [_BASE + timedelta(minutes=m) for m in minutes]


# --- expected_open_times -------------------------------------------------


def test_expected_open_times_covers_every_minute_inclusive() -> None:
    result = expected_open_times(_BASE, _BASE + timedelta(minutes=4), _STEP)
    assert result == _minutes(0, 1, 2, 3, 4)


def test_expected_open_times_for_a_single_instant_window() -> None:
    assert expected_open_times(_BASE, _BASE, _STEP) == [_BASE]


def test_expected_open_times_empty_when_end_before_start() -> None:
    assert expected_open_times(_BASE, _BASE - _STEP, _STEP) == []


# --- gaps_from_expected_and_actual: the literal "5 missing rows" case --


def test_five_scattered_missing_rows_are_all_identified() -> None:
    """TASKS.md T-P1-06 acceptance criterion, verbatim: "Manually deleting
    5 rows from the candle table and running the detector identifies all
    5 missing intervals.\""""
    expected = expected_open_times(_BASE, _BASE + timedelta(minutes=19), _STEP)
    missing_minutes = {2, 5, 6, 13, 19}
    actual = {t for t in expected if (t - _BASE).total_seconds() // 60 not in missing_minutes}

    result = gaps_from_expected_and_actual(expected, actual, step=_STEP)

    assert result.missing_count == 5


def test_five_contiguous_missing_rows_are_one_gap_with_five_entries() -> None:
    expected = expected_open_times(_BASE, _BASE + timedelta(minutes=9), _STEP)
    actual = set(expected) - set(_minutes(3, 4, 5, 6, 7))

    result = gaps_from_expected_and_actual(expected, actual, step=_STEP)

    assert result.missing_count == 5
    assert len(result.gaps) == 1
    assert result.gaps[0].missing_open_times == _minutes(3, 4, 5, 6, 7)


def test_no_missing_rows_produces_zero_gaps() -> None:
    """TASKS.md T-P1-06 acceptance criterion, verbatim (second half):
    "...the subsequent detector scan finds zero gaps.\""""
    expected = expected_open_times(_BASE, _BASE + timedelta(minutes=9), _STEP)
    result = gaps_from_expected_and_actual(expected, set(expected), step=_STEP)
    assert result.gaps == []
    assert result.missing_count == 0


def test_two_separate_gaps_are_reported_as_two_runs() -> None:
    expected = expected_open_times(_BASE, _BASE + timedelta(minutes=19), _STEP)
    actual = set(expected) - {_minutes(2)[0], _minutes(15)[0], _minutes(16)[0]}

    result = gaps_from_expected_and_actual(expected, actual, step=_STEP)

    assert len(result.gaps) == 2
    lengths = sorted(len(g.missing_open_times) for g in result.gaps)
    assert lengths == [1, 2]


# --- classification: maintenance vs. unexplained -----------------------


def test_a_gap_overlapping_a_maintenance_window_is_classified_as_maintenance() -> None:
    """TASKS.md T-P1-06 acceptance criterion, verbatim: "Gaps spanning a
    known exchange maintenance window are classified separately (not a
    data error).\""""
    expected = expected_open_times(_BASE, _BASE + timedelta(minutes=9), _STEP)
    actual = set(expected) - set(_minutes(3, 4, 5))
    maintenance = [
        MaintenanceWindow(
            start=_minutes(2)[0], end=_minutes(6)[0], reason="scheduled exchange maintenance"
        )
    ]

    result = gaps_from_expected_and_actual(
        expected, actual, step=_STEP, maintenance_windows=maintenance
    )

    assert len(result.gaps) == 1
    assert result.gaps[0].classification == "maintenance"


def test_a_gap_not_overlapping_any_maintenance_window_is_unexplained() -> None:
    expected = expected_open_times(_BASE, _BASE + timedelta(minutes=9), _STEP)
    actual = set(expected) - set(_minutes(3, 4, 5))
    maintenance = [
        MaintenanceWindow(start=_minutes(50)[0], end=_minutes(60)[0], reason="unrelated window")
    ]

    result = gaps_from_expected_and_actual(
        expected, actual, step=_STEP, maintenance_windows=maintenance
    )

    assert result.gaps[0].classification == "unexplained"


def test_partial_overlap_with_a_maintenance_window_still_classifies_as_maintenance() -> None:
    """A gap only partially inside the announced window (e.g. data
    resumed a minute late) is still maintenance, not a data error."""
    expected = expected_open_times(_BASE, _BASE + timedelta(minutes=9), _STEP)
    actual = set(expected) - set(_minutes(3, 4, 5))
    maintenance = [
        MaintenanceWindow(start=_minutes(4)[0], end=_minutes(20)[0], reason="late start")
    ]

    result = gaps_from_expected_and_actual(
        expected, actual, step=_STEP, maintenance_windows=maintenance
    )

    assert result.gaps[0].classification == "maintenance"


def test_with_no_maintenance_windows_supplied_everything_is_unexplained() -> None:
    expected = expected_open_times(_BASE, _BASE + timedelta(minutes=9), _STEP)
    actual = set(expected) - set(_minutes(3, 4, 5))

    result = gaps_from_expected_and_actual(expected, actual, step=_STEP)

    assert result.gaps[0].classification == "unexplained"
