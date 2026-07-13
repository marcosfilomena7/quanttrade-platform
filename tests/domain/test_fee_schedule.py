"""Tests for domain/fee_schedule.py (TASKS.md T-P2-05)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from domain.fee_schedule import (
    FeeSchedule,
    FeeTier,
    FundingAccrualTracker,
    compute_fill_fee,
    fees_as_percentage_of_gross_pnl,
    total_fees_paid,
)
from domain.fill import Fill
from domain.money import CurrencyMismatch, Money
from domain.order import OrderSide

TS = datetime(2026, 1, 1, tzinfo=UTC)


def _schedule(*, maker_rate: Decimal, taker_rate: Decimal) -> FeeSchedule:
    return FeeSchedule(tiers=(FeeTier(Decimal("0"), maker_rate, taker_rate),))


def _fill(*, qty: Decimal, price: Decimal, fee: Money, is_maker: bool) -> Fill:
    return Fill(
        id=uuid4(),
        order_id=uuid4(),
        venue_fill_id=str(uuid4()),
        side=OrderSide.BUY,
        qty=qty,
        price=price,
        fee=fee,
        ts=TS,
        is_maker=is_maker,
    )


# --- acceptance criterion 1: taker fee -------------------------------------


def test_a_taker_order_for_1_btc_at_50000_with_a_01_pct_taker_fee_produces_50_usdt() -> None:
    """TASKS.md T-P2-05 acceptance criterion, verbatim: "A taker order
    for 1 BTC at $50,000 with a 0.1% taker fee produces a fee of
    Money(Decimal("50.0"), "USDT").\""""
    schedule = _schedule(maker_rate=Decimal("0.0005"), taker_rate=Decimal("0.001"))

    fee = compute_fill_fee(
        qty=Decimal("1"),
        price=Decimal("50000"),
        quote_currency="USDT",
        is_maker=False,
        fee_schedule=schedule,
    )

    assert fee == Money(Decimal("50.0"), "USDT")


# --- acceptance criterion 2: maker uses maker rate, not taker rate ---------


def test_a_maker_order_uses_the_maker_fee_rate_not_the_taker_rate() -> None:
    """TASKS.md T-P2-05 acceptance criterion, verbatim: "A maker order
    uses the maker fee rate, not the taker rate.\""""
    schedule = _schedule(maker_rate=Decimal("0.0002"), taker_rate=Decimal("0.001"))

    maker_fee = compute_fill_fee(
        qty=Decimal("1"),
        price=Decimal("50000"),
        quote_currency="USDT",
        is_maker=True,
        fee_schedule=schedule,
    )
    taker_fee = compute_fill_fee(
        qty=Decimal("1"),
        price=Decimal("50000"),
        quote_currency="USDT",
        is_maker=False,
        fee_schedule=schedule,
    )

    assert maker_fee == Money(Decimal("10.0"), "USDT")  # 50000 * 0.0002
    assert taker_fee == Money(Decimal("50.0"), "USDT")  # 50000 * 0.001
    assert maker_fee != taker_fee


def test_tier_selection_picks_the_highest_qualifying_threshold() -> None:
    schedule = FeeSchedule(
        tiers=(
            FeeTier(Decimal("0"), Decimal("0.0010"), Decimal("0.0020")),
            FeeTier(Decimal("100000"), Decimal("0.0005"), Decimal("0.0010")),
            FeeTier(Decimal("1000000"), Decimal("0.0002"), Decimal("0.0004")),
        )
    )

    assert schedule.tier_for_volume(Decimal("0")).maker_rate == Decimal("0.0010")
    assert schedule.tier_for_volume(Decimal("99999")).maker_rate == Decimal("0.0010")
    assert schedule.tier_for_volume(Decimal("100000")).maker_rate == Decimal("0.0005")
    assert schedule.tier_for_volume(Decimal("5000000")).maker_rate == Decimal("0.0002")


def test_fee_schedule_rejects_tiers_without_a_zero_threshold_base_tier() -> None:
    with pytest.raises(ValueError, match="base tier"):
        FeeSchedule(tiers=(FeeTier(Decimal("100"), Decimal("0.001"), Decimal("0.002")),))


def test_fee_schedule_rejects_empty_tiers() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        FeeSchedule(tiers=())


def test_fee_schedule_rejects_unsorted_tiers() -> None:
    with pytest.raises(ValueError, match="sorted"):
        FeeSchedule(
            tiers=(
                FeeTier(Decimal("100"), Decimal("0.001"), Decimal("0.002")),
                FeeTier(Decimal("0"), Decimal("0.001"), Decimal("0.002")),
            )
        )


def test_compute_fill_fee_rejects_float_qty_and_price() -> None:
    schedule = _schedule(maker_rate=Decimal("0.0005"), taker_rate=Decimal("0.001"))
    with pytest.raises(TypeError, match="Decimal"):
        compute_fill_fee(
            qty=1.0,  # type: ignore[arg-type]
            price=Decimal("50000"),
            quote_currency="USDT",
            is_maker=False,
            fee_schedule=schedule,
        )
    with pytest.raises(TypeError, match="Decimal"):
        compute_fill_fee(
            qty=Decimal("1"),
            price=50000.0,  # type: ignore[arg-type]
            quote_currency="USDT",
            is_maker=False,
            fee_schedule=schedule,
        )


# --- acceptance criterion 3: funding accrues exactly once per 8h ------------


def test_a_perp_position_open_for_8_hours_accrues_exactly_one_funding_payment() -> None:
    """TASKS.md T-P2-05 acceptance criterion, verbatim: "A perp position
    open for 8 hours accrues exactly one funding payment.\""""
    tracker = FundingAccrualTracker(opened_at=TS)

    payments = tracker.accrue(
        current_ts=TS + timedelta(hours=8),
        position_qty=Decimal("2"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        quote_currency="USDT",
    )

    assert len(payments) == 1
    assert payments[0].accrued_at == TS + timedelta(hours=8)


def test_funding_does_not_accrue_before_8_hours_have_elapsed() -> None:
    tracker = FundingAccrualTracker(opened_at=TS)

    payments = tracker.accrue(
        current_ts=TS + timedelta(hours=7, minutes=59),
        position_qty=Decimal("2"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        quote_currency="USDT",
    )

    assert payments == []


def test_funding_catches_up_one_payment_per_interval_boundary_crossed() -> None:
    """A gap of 16h (two full 8h intervals) between accrual calls yields
    exactly two payments, not one — no missed accrual is silently lost."""
    tracker = FundingAccrualTracker(opened_at=TS)

    payments = tracker.accrue(
        current_ts=TS + timedelta(hours=16),
        position_qty=Decimal("1"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        quote_currency="USDT",
    )

    assert len(payments) == 2
    assert payments[0].accrued_at == TS + timedelta(hours=8)
    assert payments[1].accrued_at == TS + timedelta(hours=16)


def test_funding_payment_sign_a_long_position_pays_when_funding_rate_is_positive() -> None:
    tracker = FundingAccrualTracker(opened_at=TS)

    payments = tracker.accrue(
        current_ts=TS + timedelta(hours=8),
        position_qty=Decimal("1"),  # long
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),  # positive: longs pay
        quote_currency="USDT",
    )

    assert payments[0].amount == Money(Decimal("-5.0"), "USDT")  # -(1 * 50000 * 0.0001)


def test_funding_payment_sign_a_short_position_receives_when_funding_rate_is_positive() -> None:
    tracker = FundingAccrualTracker(opened_at=TS)

    payments = tracker.accrue(
        current_ts=TS + timedelta(hours=8),
        position_qty=Decimal("-1"),  # short
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        quote_currency="USDT",
    )

    assert payments[0].amount == Money(Decimal("5.0"), "USDT")


def test_funding_accrual_tracker_rejects_a_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        FundingAccrualTracker(opened_at=TS, funding_interval=timedelta(0))


# --- acceptance criterion 4: fees as % of gross P&L is computable ----------


def test_fees_as_percentage_of_gross_pnl_is_computable_from_a_sequence_of_fills() -> None:
    """TASKS.md T-P2-05 acceptance criterion, verbatim: "Fees-as-
    percentage-of-gross-P&L is computable from the output of any
    backtest run.\""""
    fills = [
        _fill(
            qty=Decimal("1"),
            price=Decimal("50000"),
            fee=Money(Decimal("50.0"), "USDT"),
            is_maker=False,
        ),
        _fill(
            qty=Decimal("1"),
            price=Decimal("51000"),
            fee=Money(Decimal("25.5"), "USDT"),
            is_maker=True,
        ),
    ]

    fees = total_fees_paid(fills)
    assert fees == Money(Decimal("75.5"), "USDT")

    ratio = fees_as_percentage_of_gross_pnl(
        total_fees=fees, gross_pnl=Money(Decimal("1000.0"), "USDT")
    )
    assert ratio == Decimal("7.55")  # 75.5 / 1000 * 100


def test_fees_as_percentage_of_gross_pnl_uses_absolute_value_of_a_losing_run() -> None:
    ratio = fees_as_percentage_of_gross_pnl(
        total_fees=Money(Decimal("10.0"), "USDT"), gross_pnl=Money(Decimal("-200.0"), "USDT")
    )
    assert ratio == Decimal("5")  # 10 / |-200| * 100


def test_fees_as_percentage_of_gross_pnl_rejects_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatch):
        fees_as_percentage_of_gross_pnl(
            total_fees=Money(Decimal("10.0"), "USDT"), gross_pnl=Money(Decimal("100.0"), "EUR")
        )


def test_fees_as_percentage_of_gross_pnl_rejects_zero_gross_pnl() -> None:
    with pytest.raises(ZeroDivisionError):
        fees_as_percentage_of_gross_pnl(
            total_fees=Money(Decimal("10.0"), "USDT"), gross_pnl=Money(Decimal("0"), "USDT")
        )


def test_total_fees_paid_rejects_an_empty_sequence() -> None:
    with pytest.raises(ValueError, match="at least one fill"):
        total_fees_paid([])


def test_total_fees_paid_raises_on_mismatched_fee_currencies() -> None:
    fills = [
        _fill(
            qty=Decimal("1"),
            price=Decimal("50000"),
            fee=Money(Decimal("50.0"), "USDT"),
            is_maker=False,
        ),
        _fill(
            qty=Decimal("1"),
            price=Decimal("50000"),
            fee=Money(Decimal("40.0"), "EUR"),
            is_maker=False,
        ),
    ]
    with pytest.raises(CurrencyMismatch):
        total_fees_paid(fills)
