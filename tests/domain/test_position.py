"""Tests for the Position domain model and FIFO-equivalent fill application
(domain/position.py).

Property-based tests use Hypothesis per ARCHITECTURE.md §3.9 ("Property-
based tests | Critical invariants | Hypothesis | Position math, order
state machine, risk rules").
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from domain.fill import Fill
from domain.money import CurrencyMismatch, Money
from domain.order import OrderSide
from domain.position import Position

TS = datetime(2026, 1, 1, tzinfo=UTC)

QTY = st.decimals(
    min_value="0.00000001", max_value="1000", places=8, allow_nan=False, allow_infinity=False
)
PRICE = st.decimals(
    min_value="0.01", max_value="1000000", places=8, allow_nan=False, allow_infinity=False
)
FEE = st.decimals(min_value="0", max_value="100", places=8, allow_nan=False, allow_infinity=False)
SIDE = st.sampled_from([OrderSide.BUY, OrderSide.SELL])


def _fill(
    side: OrderSide,
    qty: Decimal,
    price: Decimal,
    fee: Decimal = Decimal("0"),
    currency: str = "USDT",
) -> Fill:
    return Fill(
        id=uuid4(),
        order_id=uuid4(),
        venue_fill_id=str(uuid4()),
        side=side,
        qty=qty,
        price=price,
        fee=Money(fee, currency),
        ts=TS,
        is_maker=False,
    )


def _flat() -> Position:
    return Position.flat(uuid4(), uuid4(), "USDT")


# --- Position.flat() --------------------------------------------------------


def test_flat_position_has_zero_qty_and_pnl() -> None:
    position = _flat()
    assert position.qty == Decimal("0")
    assert position.avg_entry == Decimal("0")
    assert position.realized_pnl == Money(Decimal("0"), "USDT")


def test_position_rejects_float_qty_and_avg_entry() -> None:
    with pytest.raises(TypeError):
        Position(
            instrument_id=uuid4(),
            strategy_instance_id=uuid4(),
            qty=1.5,  # type: ignore[arg-type]
            avg_entry=Decimal("100"),
            realized_pnl=Money(Decimal("0"), "USDT"),
        )


def test_position_is_immutable() -> None:
    position = _flat()
    with pytest.raises(FrozenInstanceError):
        position.qty = Decimal("5")  # type: ignore[misc]


# --- Property: Σ(signed fill quantities) == position.qty -------------------


@given(st.lists(st.tuples(SIDE, QTY, PRICE, FEE), min_size=1, max_size=20))
@settings(max_examples=100)
def test_position_qty_equals_sum_of_signed_fill_quantities(
    fill_specs: list[tuple[OrderSide, Decimal, Decimal, Decimal]],
) -> None:
    position = _flat()
    fills = [_fill(side, qty, price, fee) for side, qty, price, fee in fill_specs]
    for fill in fills:
        position = position.apply_fill(fill)

    expected_qty = sum((f.signed_qty for f in fills), start=Decimal("0"))
    assert position.qty == expected_qty


# --- Partial close: qty reduces, avg_entry unchanged, P&L booked exactly ----


def test_partial_close_reduces_qty_and_books_realized_pnl_correctly_example() -> None:
    position = _flat()
    position = position.apply_fill(
        _fill(OrderSide.BUY, Decimal("10"), Decimal("100"), Decimal("1"))
    )
    position = position.apply_fill(
        _fill(OrderSide.SELL, Decimal("4"), Decimal("120"), Decimal("1"))
    )

    assert position.qty == Decimal("6")
    assert position.avg_entry == Decimal("100")
    # -1 (open fee) + (120-100)*4 (price P&L) - 1 (close fee) = -1 + 80 - 1 = 78
    assert position.realized_pnl == Money(Decimal("78"), "USDT")


@given(
    open_qty=st.decimals(
        min_value="1", max_value="1000", places=4, allow_nan=False, allow_infinity=False
    ),
    open_price=st.decimals(
        min_value="0.01", max_value="100000", places=4, allow_nan=False, allow_infinity=False
    ),
    close_fraction=st.decimals(
        min_value="0.01", max_value="0.99", places=4, allow_nan=False, allow_infinity=False
    ),
    close_price=st.decimals(
        min_value="0.01", max_value="100000", places=4, allow_nan=False, allow_infinity=False
    ),
    open_fee=st.decimals(
        min_value="0", max_value="50", places=4, allow_nan=False, allow_infinity=False
    ),
    close_fee=st.decimals(
        min_value="0", max_value="50", places=4, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=100)
def test_partial_close_reduces_qty_and_books_realized_pnl_correctly_property(
    open_qty: Decimal,
    open_price: Decimal,
    close_fraction: Decimal,
    close_price: Decimal,
    open_fee: Decimal,
    close_fee: Decimal,
) -> None:
    close_qty = (open_qty * close_fraction).quantize(Decimal("0.0001"))
    assume(Decimal("0") < close_qty < open_qty)

    position = _flat()
    position = position.apply_fill(_fill(OrderSide.BUY, open_qty, open_price, open_fee))
    position = position.apply_fill(_fill(OrderSide.SELL, close_qty, close_price, close_fee))

    expected_qty = open_qty - close_qty
    expected_realized = -open_fee + (close_price - open_price) * close_qty - close_fee

    assert position.qty == expected_qty
    assert position.avg_entry == open_price
    assert position.realized_pnl == Money(expected_realized, "USDT")


# --- Exact close and flip ----------------------------------------------------


def test_exact_close_resets_qty_and_avg_entry_to_zero() -> None:
    position = _flat()
    position = position.apply_fill(_fill(OrderSide.BUY, Decimal("5"), Decimal("100")))
    position = position.apply_fill(_fill(OrderSide.SELL, Decimal("5"), Decimal("110")))
    assert position.qty == Decimal("0")
    assert position.avg_entry == Decimal("0")
    assert position.realized_pnl == Money(Decimal("50"), "USDT")  # (110-100)*5


def test_close_and_flip_long_to_short() -> None:
    position = _flat()
    position = position.apply_fill(_fill(OrderSide.BUY, Decimal("5"), Decimal("100")))
    position = position.apply_fill(_fill(OrderSide.SELL, Decimal("8"), Decimal("120")))
    assert position.qty == Decimal("-3")
    assert position.avg_entry == Decimal("120")
    assert position.realized_pnl == Money(Decimal("100"), "USDT")  # (120-100)*5 closed


def test_close_and_flip_short_to_long() -> None:
    position = _flat()
    position = position.apply_fill(_fill(OrderSide.SELL, Decimal("5"), Decimal("100")))
    position = position.apply_fill(_fill(OrderSide.BUY, Decimal("8"), Decimal("90")))
    assert position.qty == Decimal("3")
    assert position.avg_entry == Decimal("90")
    assert position.realized_pnl == Money(Decimal("50"), "USDT")  # (100-90)*5 covered


# --- Short-side lifecycle ----------------------------------------------------


def test_opening_and_adding_to_a_short_position_uses_weighted_average() -> None:
    position = _flat()
    position = position.apply_fill(_fill(OrderSide.SELL, Decimal("5"), Decimal("100")))
    assert position.qty == Decimal("-5")
    assert position.avg_entry == Decimal("100")

    position = position.apply_fill(_fill(OrderSide.SELL, Decimal("5"), Decimal("110")))
    assert position.qty == Decimal("-10")
    assert position.avg_entry == Decimal("105")  # (5*100 + 5*110)/10


def test_partial_cover_of_short_keeps_avg_entry() -> None:
    position = _flat()
    position = position.apply_fill(_fill(OrderSide.SELL, Decimal("5"), Decimal("100")))
    position = position.apply_fill(_fill(OrderSide.BUY, Decimal("3"), Decimal("90")))
    assert position.qty == Decimal("-2")
    assert position.avg_entry == Decimal("100")
    assert position.realized_pnl == Money(Decimal("30"), "USDT")  # (100-90)*3


# --- Idempotency -------------------------------------------------------------


def test_applying_same_fill_twice_is_a_no_op_example() -> None:
    position = _flat()
    position = position.apply_fill(_fill(OrderSide.BUY, Decimal("5"), Decimal("100"), Decimal("1")))
    fill2 = _fill(OrderSide.SELL, Decimal("2"), Decimal("110"), Decimal("1"))
    once = position.apply_fill(fill2)
    twice = once.apply_fill(fill2)
    assert once == twice


@given(SIDE, QTY, PRICE, FEE)
@settings(max_examples=100)
def test_apply_fill_is_idempotent_property(
    side: OrderSide, qty: Decimal, price: Decimal, fee: Decimal
) -> None:
    position = _flat()
    fill = _fill(side, qty, price, fee)
    once = position.apply_fill(fill)
    twice = once.apply_fill(fill)
    assert once == twice


@given(st.lists(st.tuples(SIDE, QTY, PRICE, FEE), min_size=1, max_size=10))
@settings(max_examples=100)
def test_reapplying_any_earlier_fill_in_a_sequence_is_a_no_op(
    fill_specs: list[tuple[OrderSide, Decimal, Decimal, Decimal]],
) -> None:
    position = _flat()
    fills = [_fill(side, qty, price, fee) for side, qty, price, fee in fill_specs]
    for fill in fills:
        position = position.apply_fill(fill)

    # Reapplying every fill again, in order, must change nothing.
    replayed = position
    for fill in fills:
        replayed = replayed.apply_fill(fill)
    assert replayed == position


# --- Cash after fill = cash before - notional - fee, exactly in Decimal ----


def test_cash_after_fill_is_exact_no_float_drift() -> None:
    """0.1 * 3 in binary float is 0.30000000000000004 — Decimal must not drift."""
    fill = _fill(OrderSide.BUY, Decimal("0.1"), Decimal("3"), Decimal("0.01"))
    assert fill.notional == Decimal("0.3")

    cash_before = Decimal("1000")
    cash_after = cash_before - fill.notional - fill.fee.amount
    assert cash_after == Decimal("999.69")


@given(QTY, PRICE, FEE, SIDE)
@settings(max_examples=200)
def test_cash_after_fill_matches_independent_decimal_computation(
    qty: Decimal, price: Decimal, fee: Decimal, side: OrderSide
) -> None:
    cash_before = Decimal("100000")
    fill = _fill(side, qty, price, fee)

    signed_notional = fill.notional if side is OrderSide.BUY else -fill.notional
    cash_after = cash_before - signed_notional - fill.fee.amount

    # Recomputed directly from the raw inputs, independent of Fill.notional.
    expected_signed_notional = (qty * price) if side is OrderSide.BUY else -(qty * price)
    expected = cash_before - expected_signed_notional - fee

    assert cash_after == expected
    assert isinstance(cash_after, Decimal)


# --- Currency mismatch fails loudly, never silently -------------------------


def test_mismatched_fee_currency_across_fills_raises_currency_mismatch() -> None:
    position = _flat()
    first_fill = _fill(OrderSide.BUY, Decimal("1"), Decimal("100"), currency="USDT")
    position = position.apply_fill(first_fill)
    second_fill = _fill(OrderSide.BUY, Decimal("1"), Decimal("100"), currency="EUR")
    with pytest.raises(CurrencyMismatch):
        position.apply_fill(second_fill)
