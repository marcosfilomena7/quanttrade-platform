"""Tests for domain/fill_simulation.py (TASKS.md T-P2-06)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from domain.candle import Candle
from domain.fee_schedule import FeeSchedule, FeeTier
from domain.fill_simulation import (
    RejectionReason,
    SimulatedFillResult,
    SimulatedRejection,
    simulate_fill,
)
from domain.instrument import Instrument, Spot
from domain.money import CurrencyMismatch, Money
from domain.order import Order, OrderSide, OrderType, TimeInForce
from domain.position import Position

TS = datetime(2026, 1, 1, tzinfo=UTC)
_INSTRUMENT_ID = uuid4()
_VENUE_ID = uuid4()
_STRATEGY_INSTANCE_ID = uuid4()

_ZERO_FEE_SCHEDULE = FeeSchedule(tiers=(FeeTier(Decimal("0"), Decimal("0"), Decimal("0")),))
_TAKER_01_PCT_SCHEDULE = FeeSchedule(
    tiers=(FeeTier(Decimal("0"), Decimal("0.0005"), Decimal("0.001")),)
)
_HUGE_BALANCE = Money(Decimal("100000000"), "USDT")


def _instrument(
    *, lot_size: Decimal = Decimal("0.0001"), min_notional: Decimal = Decimal("10")
) -> Instrument:
    return Instrument(
        id=_INSTRUMENT_ID,
        venue_id=_VENUE_ID,
        symbol="BTCUSDT",
        base_currency="BTC",
        quote_currency="USDT",
        tick_size=Decimal("0.01"),
        lot_size=lot_size,
        min_notional=min_notional,
        status="trading",
        details=Spot(),
    )


def _order(*, side: OrderSide = OrderSide.BUY, qty: Decimal = Decimal("1")) -> Order:
    order, _ = Order.new(
        id=uuid4(),
        client_order_id=str(uuid4()),
        venue_id=_VENUE_ID,
        instrument_id=_INSTRUMENT_ID,
        strategy_instance_id=_STRATEGY_INSTANCE_ID,
        risk_decision_id=uuid4(),
        side=side,
        order_type=OrderType.MARKET,
        qty=qty,
        tif=TimeInForce.GTC,
        ts=TS,
        event_id=uuid4(),
    )
    return order


def _bar(*, open_time: datetime, open_: Decimal, close: Decimal, volume: Decimal) -> Candle:
    return Candle(
        instrument_id=_INSTRUMENT_ID,
        interval="1m",
        open_time=open_time,
        open=open_,
        high=max(open_, close) + Decimal("10"),
        low=min(open_, close) - Decimal("10"),
        close=close,
        volume=volume,
        is_closed=True,
    )


# --- acceptance criterion 1: never fills at bar t's close ------------------


def test_an_order_never_fills_at_bar_ts_close_price_only_at_next_bar_or_later() -> None:
    """TASKS.md T-P2-06 acceptance criterion, verbatim: "An order
    submitted on bar t never fills at bar t's close price — fills are
    always at bar t+1 or later.\""""
    bar_t_close = Decimal("49999")  # deliberately different from bar t+1's open
    bar_t_plus_1 = _bar(
        open_time=TS + timedelta(minutes=1),
        open_=Decimal("50100"),
        close=Decimal("50200"),
        volume=Decimal("100"),
    )

    result = simulate_fill(
        order=_order(),
        instrument=_instrument(),
        next_bar=bar_t_plus_1,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(result, SimulatedFillResult)
    assert result.fill is not None
    assert result.fill.price != bar_t_close
    assert result.fill.price == bar_t_plus_1.open
    assert result.fill.ts >= bar_t_plus_1.open_time


def test_simulate_fill_signature_has_no_access_to_bar_ts_data_at_all() -> None:
    """Structural reinforcement of AC1: `simulate_fill` accepts a single
    `next_bar` — there is no parameter through which bar t's own close
    could ever reach the fill price."""
    import inspect

    params = inspect.signature(simulate_fill).parameters
    assert "next_bar" in params
    assert "prior_bar" not in params
    assert "current_bar" not in params


# --- acceptance criterion 2: zero slippage -> fill price == next bar open --


def test_with_zero_slippage_configured_fill_price_equals_next_bars_open_exactly() -> None:
    """TASKS.md T-P2-06 acceptance criterion, verbatim: "With zero
    slippage configured, fill price equals next bar's open exactly.\""""
    next_bar = _bar(
        open_time=TS, open_=Decimal("50123.45"), close=Decimal("50200"), volume=Decimal("50")
    )

    result = simulate_fill(
        order=_order(),
        instrument=_instrument(),
        next_bar=next_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(result, SimulatedFillResult)
    assert result.fill is not None
    assert result.fill.price == Decimal("50123.45")


def test_zero_slippage_holds_for_a_sell_order_too() -> None:
    next_bar = _bar(
        open_time=TS, open_=Decimal("50123.45"), close=Decimal("50200"), volume=Decimal("50")
    )

    result = simulate_fill(
        order=_order(side=OrderSide.SELL),
        instrument=_instrument(),
        next_bar=next_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(result, SimulatedFillResult)
    assert result.fill is not None
    assert result.fill.price == Decimal("50123.45")


def test_nonzero_slippage_worsens_price_for_a_buy_and_a_sell_in_opposite_directions() -> None:
    next_bar = _bar(
        open_time=TS, open_=Decimal("50000"), close=Decimal("50100"), volume=Decimal("100")
    )

    buy_result = simulate_fill(
        order=_order(side=OrderSide.BUY),
        instrument=_instrument(),
        next_bar=next_bar,
        spread=Decimal("2"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )
    sell_result = simulate_fill(
        order=_order(side=OrderSide.SELL),
        instrument=_instrument(),
        next_bar=next_bar,
        spread=Decimal("2"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(buy_result, SimulatedFillResult) and buy_result.fill is not None
    assert isinstance(sell_result, SimulatedFillResult) and sell_result.fill is not None
    assert buy_result.fill.price == Decimal("50001")  # 50000 + 2/2
    assert sell_result.fill.price == Decimal("49999")  # 50000 - 2/2


# --- acceptance criterion 3: fractional fill limited to available volume --


def test_a_fractional_fill_fills_only_the_available_volume_fraction() -> None:
    """TASKS.md T-P2-06 acceptance criterion, verbatim: "A fractional
    fill (order size > bar volume) fills only the available fraction.\""""
    next_bar = _bar(
        open_time=TS, open_=Decimal("50000"), close=Decimal("50000"), volume=Decimal("0.4")
    )

    result = simulate_fill(
        order=_order(qty=Decimal("1")),  # order size (1) > bar volume (0.4)
        instrument=_instrument(),
        next_bar=next_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(result, SimulatedFillResult)
    assert result.fill is not None
    assert result.fill.qty == Decimal("0.4")  # only the available fraction
    assert result.remaining_qty == Decimal("0.6")  # 1 - 0.4 left unfilled


def test_a_bar_with_zero_volume_yields_no_fill_not_a_rejection() -> None:
    next_bar = _bar(
        open_time=TS, open_=Decimal("50000"), close=Decimal("50000"), volume=Decimal("0")
    )

    result = simulate_fill(
        order=_order(qty=Decimal("1")),
        instrument=_instrument(),
        next_bar=next_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(result, SimulatedFillResult)
    assert result.fill is None
    assert result.remaining_qty == Decimal("1")


def test_a_configured_lower_participation_fraction_further_restricts_the_fill() -> None:
    next_bar = _bar(
        open_time=TS, open_=Decimal("50000"), close=Decimal("50000"), volume=Decimal("1")
    )

    result = simulate_fill(
        order=_order(qty=Decimal("1")),
        instrument=_instrument(),
        next_bar=next_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
        max_fill_fraction_of_volume=Decimal("0.1"),  # only 10% of bar volume available
    )

    assert isinstance(result, SimulatedFillResult)
    assert result.fill is not None
    assert result.fill.qty == Decimal("0.1")


# --- simulated rejections ---------------------------------------------------


def test_an_order_below_min_notional_is_rejected() -> None:
    next_bar = _bar(
        open_time=TS, open_=Decimal("1"), close=Decimal("1"), volume=Decimal("1000")
    )

    result = simulate_fill(
        order=_order(qty=Decimal("1")),  # notional = 1 * 1 = 1, below min_notional=10
        instrument=_instrument(min_notional=Decimal("10")),
        next_bar=next_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(result, SimulatedRejection)
    assert result.reason == RejectionReason.MIN_NOTIONAL


def test_an_order_qty_not_a_multiple_of_lot_size_is_rejected_for_precision() -> None:
    next_bar = _bar(
        open_time=TS, open_=Decimal("50000"), close=Decimal("50000"), volume=Decimal("100")
    )

    result = simulate_fill(
        order=_order(qty=Decimal("1.00005")),  # not a multiple of lot_size=0.0001
        instrument=_instrument(lot_size=Decimal("0.0001")),
        next_bar=next_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(result, SimulatedRejection)
    assert result.reason == RejectionReason.PRECISION


def test_an_order_exceeding_available_balance_is_rejected_for_insufficient_margin() -> None:
    next_bar = _bar(
        open_time=TS, open_=Decimal("50000"), close=Decimal("50000"), volume=Decimal("100")
    )

    result = simulate_fill(
        order=_order(qty=Decimal("1")),  # notional = 50000
        instrument=_instrument(),
        next_bar=next_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_ZERO_FEE_SCHEDULE,
        is_maker=False,
        available_balance=Money(Decimal("100"), "USDT"),  # far too little
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(result, SimulatedRejection)
    assert result.reason == RejectionReason.INSUFFICIENT_MARGIN


def test_simulate_fill_rejects_a_currency_mismatched_available_balance() -> None:
    next_bar = _bar(
        open_time=TS, open_=Decimal("50000"), close=Decimal("50000"), volume=Decimal("100")
    )

    with pytest.raises(CurrencyMismatch):
        simulate_fill(
            order=_order(),
            instrument=_instrument(),
            next_bar=next_bar,
            spread=Decimal("0"),
            volatility=Decimal("0"),
            fee_schedule=_ZERO_FEE_SCHEDULE,
            is_maker=False,
            available_balance=Money(Decimal("1000000"), "EUR"),
            fill_id=uuid4(),
            venue_fill_id=str(uuid4()),
        )


def test_simulate_fill_rejects_an_order_with_no_remaining_quantity() -> None:
    from dataclasses import replace

    next_bar = _bar(
        open_time=TS, open_=Decimal("50000"), close=Decimal("50000"), volume=Decimal("100")
    )
    fully_filled_order = replace(_order(), filled_qty=Decimal("1"))

    with pytest.raises(ValueError, match="no remaining quantity"):
        simulate_fill(
            order=fully_filled_order,
            instrument=_instrument(),
            next_bar=next_bar,
            spread=Decimal("0"),
            volatility=Decimal("0"),
            fee_schedule=_ZERO_FEE_SCHEDULE,
            is_maker=False,
            available_balance=_HUGE_BALANCE,
            fill_id=uuid4(),
            venue_fill_id=str(uuid4()),
        )


# --- acceptance criterion 4: manual buy-and-hold matches to the cent ------


def test_a_manually_computed_buy_and_hold_pnl_matches_simulate_fill_plus_position_to_the_cent() -> (
    None
):
    """TASKS.md T-P2-06 acceptance criterion, verbatim: "A buy-and-hold
    backtest computed manually against the Parquet data matches the
    engine's output to the cent."

    T-P2-06's own dependencies are "T-P2-05" only — no Parquet loading
    (T-P1-11) or backtest engine (T-P2-01..04) is available here. This
    test instead constructs a small, hand-built candle series and
    verifies that a *manually computed* buy-and-hold P&L (entry price,
    exit price, and known fees, worked out by hand below) matches
    exactly what composing this task's own `simulate_fill` with the
    already-existing `Position.apply_fill` (T-P0-06) produces — the
    literal "computed manually... matches... to the cent" comparison,
    using only what this task and its own listed dependency provide.
    """
    entry_bar = _bar(
        open_time=TS + timedelta(minutes=1),
        open_=Decimal("50100.00"),
        close=Decimal("50150.00"),
        volume=Decimal("100"),
    )
    exit_bar = _bar(
        open_time=TS + timedelta(days=30),
        open_=Decimal("52000.00"),
        close=Decimal("52050.00"),
        volume=Decimal("100"),
    )
    instrument = _instrument()

    buy_result = simulate_fill(
        order=_order(side=OrderSide.BUY, qty=Decimal("1")),
        instrument=instrument,
        next_bar=entry_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_TAKER_01_PCT_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )
    sell_result = simulate_fill(
        order=_order(side=OrderSide.SELL, qty=Decimal("1")),
        instrument=instrument,
        next_bar=exit_bar,
        spread=Decimal("0"),
        volatility=Decimal("0"),
        fee_schedule=_TAKER_01_PCT_SCHEDULE,
        is_maker=False,
        available_balance=_HUGE_BALANCE,
        fill_id=uuid4(),
        venue_fill_id=str(uuid4()),
    )

    assert isinstance(buy_result, SimulatedFillResult) and buy_result.fill is not None
    assert isinstance(sell_result, SimulatedFillResult) and sell_result.fill is not None
    buy_fill, sell_fill = buy_result.fill, sell_result.fill

    # Hand-worked reference numbers:
    assert buy_fill.price == Decimal("50100.00")
    assert sell_fill.price == Decimal("52000.00")
    assert buy_fill.fee.amount == Decimal("50.10000")  # 50100.00 * 0.001
    assert sell_fill.fee.amount == Decimal("52.00000")  # 52000.00 * 0.001

    manual_price_pnl = (sell_fill.price - buy_fill.price) * Decimal("1")  # 1900.00
    manual_net_pnl = manual_price_pnl - buy_fill.fee.amount - sell_fill.fee.amount

    position = Position.flat(instrument.id, _STRATEGY_INSTANCE_ID, "USDT")
    position = position.apply_fill(buy_fill)
    position = position.apply_fill(sell_fill)

    assert position.qty == Decimal("0")  # flat again: bought 1, sold 1
    assert position.realized_pnl == Money(manual_net_pnl, "USDT")
    assert position.realized_pnl.amount == Decimal("1797.90000")  # 1900 - 50.1 - 52.0, to the cent
