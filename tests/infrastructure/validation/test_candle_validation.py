"""Unit tests for infrastructure/validation/candle_validation.py's pure
validation logic — no database required.

The end-to-end persistence/metric-emission/30-day-clean-dataset behavior
against a real Postgres is covered separately by
test_candle_validation_integration.py (TASKS.md T-P1-05's acceptance
criteria are all database-state assertions).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from infrastructure.validation.candle_validation import (
    CandleRecord,
    validate_candle,
    validate_candle_sequence,
    validate_candles,
)

_INSTRUMENT_ID = uuid4()
_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _candle(
    *,
    minute: int = 0,
    open_: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100.5",
    volume: str = "10",
) -> CandleRecord:
    return CandleRecord(
        instrument_id=_INSTRUMENT_ID,
        interval="1m",
        open_time=_BASE_TIME + timedelta(minutes=minute),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


# --- validate_candle: OHLC invariants ---------------------------------


def test_a_well_formed_candle_has_no_violations() -> None:
    assert validate_candle(_candle()) == []


def test_high_less_than_close_is_quarantined() -> None:
    """TASKS.md T-P1-05 acceptance criterion, verbatim: "Injecting a
    candle with `high < close` produces a quarantine record.\""""
    candle = _candle(high="100", close="105")
    violations = validate_candle(candle)
    assert len(violations) == 1
    assert violations[0].check == "ohlc_invariant"
    assert violations[0].severity == "quarantined"


def test_high_less_than_open_is_quarantined() -> None:
    candle = _candle(open_="105", high="100")
    violations = validate_candle(candle)
    assert any(v.check == "ohlc_invariant" for v in violations)


def test_low_greater_than_close_is_quarantined() -> None:
    candle = _candle(low="102", close="100")
    violations = validate_candle(candle)
    assert any(v.check == "ohlc_invariant" for v in violations)


def test_low_greater_than_open_is_quarantined() -> None:
    candle = _candle(open_="100", low="102")
    violations = validate_candle(candle)
    assert any(v.check == "ohlc_invariant" for v in violations)


def test_high_less_than_low_is_quarantined() -> None:
    candle = _candle(high="90", low="95", open_="92", close="93")
    violations = validate_candle(candle)
    assert any(v.check == "ohlc_invariant" for v in violations)


# --- validate_candle: volume ------------------------------------------


def test_negative_volume_is_quarantined() -> None:
    candle = _candle(volume="-1")
    violations = validate_candle(candle)
    assert len(violations) == 1
    assert violations[0].check == "volume_nonneg"
    assert violations[0].severity == "quarantined"


def test_zero_volume_alone_is_not_a_per_candle_violation() -> None:
    """Zero volume is fine in isolation — only a *streak* of zero-volume
    bars is interesting, and that's a sequence-level check."""
    assert validate_candle(_candle(volume="0")) == []


# --- validate_candle: timestamp alignment ------------------------------


def test_a_timestamp_aligned_to_the_interval_boundary_is_not_flagged() -> None:
    candle = CandleRecord(
        instrument_id=_INSTRUMENT_ID,
        interval="1m",
        open_time=_BASE_TIME,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(10),
    )
    assert validate_candle(candle) == []


def test_a_misaligned_timestamp_is_quarantined() -> None:
    candle = CandleRecord(
        instrument_id=_INSTRUMENT_ID,
        interval="1m",
        open_time=_BASE_TIME + timedelta(seconds=30),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(10),
    )
    violations = validate_candle(candle)
    assert any(v.check == "timestamp_alignment" for v in violations)
    assert all(v.severity == "quarantined" for v in violations if v.check == "timestamp_alignment")


def test_violation_carries_instrument_id_and_interval() -> None:
    candle = _candle(high="100", close="105")
    violation = validate_candle(candle)[0]
    assert violation.instrument_id == _INSTRUMENT_ID
    assert violation.interval == "1m"


# --- validate_candle_sequence: monotonicity + missing intervals --------


def test_contiguous_ascending_bars_have_no_sequence_violations() -> None:
    candles = [_candle(minute=m) for m in range(5)]
    assert validate_candle_sequence(candles) == []


def test_a_missing_bar_is_flagged_as_a_missing_interval() -> None:
    """TASKS.md T-P1-05, check (5): "no missing intervals in a trading window.\""""
    candles = [_candle(minute=0), _candle(minute=1), _candle(minute=3)]  # minute 2 missing
    violations = validate_candle_sequence(candles)
    gap_violations = [v for v in violations if v.check == "missing_interval"]
    assert len(gap_violations) == 1
    assert gap_violations[0].severity == "flagged"


def test_a_repeated_or_out_of_order_timestamp_is_flagged_as_non_monotonic() -> None:
    candles = [_candle(minute=0), _candle(minute=1), _candle(minute=1)]
    violations = validate_candle_sequence(candles)
    mono_violations = [v for v in violations if v.check == "timestamp_monotonic"]
    assert len(mono_violations) == 1
    assert mono_violations[0].severity == "flagged"


def test_less_than_two_candles_produces_no_sequence_violations() -> None:
    assert validate_candle_sequence([]) == []
    assert validate_candle_sequence([_candle()]) == []


# --- validate_candle_sequence: zero-volume streaks ----------------------


def test_a_short_zero_volume_run_is_not_flagged() -> None:
    candles = [_candle(minute=m, volume="0") for m in range(3)] + [_candle(minute=3, volume="5")]
    violations = validate_candle_sequence(candles, zero_volume_streak_threshold=5)
    assert not any(v.check == "zero_volume_streak" for v in violations)


def test_a_long_zero_volume_run_is_flagged_once() -> None:
    candles = [_candle(minute=m, volume="0") for m in range(6)] + [_candle(minute=6, volume="5")]
    violations = validate_candle_sequence(candles, zero_volume_streak_threshold=5)
    streak_violations = [v for v in violations if v.check == "zero_volume_streak"]
    assert len(streak_violations) == 1
    assert streak_violations[0].details["streak_length"] == 6
    assert streak_violations[0].severity == "flagged"


def test_a_zero_volume_run_extending_to_the_end_of_the_batch_is_still_flagged() -> None:
    candles = [_candle(minute=m, volume="0") for m in range(6)]
    violations = validate_candle_sequence(candles, zero_volume_streak_threshold=5)
    assert any(v.check == "zero_volume_streak" for v in violations)


# --- validate_candle_sequence: price move > N sigma ---------------------


def _noisy_price_candles(count: int, *, start_price: str = "100") -> list[CandleRecord]:
    candles = []
    price = Decimal(start_price)
    for m in range(count):
        # Tiny, consistent noise establishes a small, stable rolling stddev.
        price += Decimal("0.01") if m % 2 == 0 else Decimal("-0.01")
        candles.append(
            _candle(
                minute=m,
                open_=str(price),
                high=str(price + 1),
                low=str(price - 1),
                close=str(price),
            )
        )
    return candles


def test_a_20_sigma_price_spike_is_flagged() -> None:
    """TASKS.md T-P1-05 acceptance criterion, verbatim: "A 20 sigma price
    spike is flagged but the candle is retained (not deleted).\""""
    candles = _noisy_price_candles(25)
    last_price = candles[-1].close
    # One enormous jump, far beyond the established baseline.
    spike_price = last_price * Decimal("3")
    candles.append(
        _candle(
            minute=25,
            open_=str(last_price),
            high=str(spike_price + 1),
            low=str(last_price - 1),
            close=str(spike_price),
        )
    )

    violations = validate_candle_sequence(
        candles, price_move_window=20, price_move_sigma_threshold=Decimal("10")
    )
    spike_violations = [v for v in violations if v.check == "price_move_sigma"]
    assert len(spike_violations) == 1
    assert spike_violations[0].severity == "flagged"


def test_ordinary_price_moves_are_not_flagged() -> None:
    candles = _noisy_price_candles(30)
    violations = validate_candle_sequence(
        candles, price_move_window=20, price_move_sigma_threshold=Decimal("10")
    )
    assert not any(v.check == "price_move_sigma" for v in violations)


def test_a_flat_price_baseline_does_not_raise_a_division_error() -> None:
    """A zero-variance baseline (identical returns) must not attempt to
    divide by zero when computing a sigma multiple."""
    candles = [_candle(minute=m, close="100") for m in range(30)]
    violations = validate_candle_sequence(candles, price_move_window=20)
    assert not any(v.check == "price_move_sigma" for v in violations)


# --- validate_candles: orchestration -----------------------------------


def test_validate_candles_retains_clean_bars_and_quarantines_bad_ones() -> None:
    good = _candle(minute=0)
    bad = _candle(minute=1, high="90", close="100")  # high < close
    result = validate_candles([good, bad])

    assert result.retained_candles == [good]
    assert len(result.quarantined) == 1
    assert result.quarantined[0].check == "ohlc_invariant"


def test_validate_candles_on_entirely_clean_data_has_zero_violations() -> None:
    candles = [_candle(minute=m) for m in range(10)]
    result = validate_candles(candles)
    assert result.quarantined == []
    assert result.flagged == []
    assert result.retained_candles == candles


def test_validation_result_violations_property_combines_both_severities() -> None:
    good = _candle(minute=0)
    bad = _candle(minute=1, high="90", close="100")
    result = validate_candles([good, bad])
    assert len(result.violations) == len(result.quarantined) + len(result.flagged)
    for violation in result.quarantined:
        assert violation in result.violations
