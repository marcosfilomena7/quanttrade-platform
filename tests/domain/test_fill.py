"""Tests for the Fill domain model (domain/fill.py)."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from domain.fill import Fill
from domain.money import Money
from domain.order import OrderSide

TS = datetime(2026, 1, 1, tzinfo=UTC)


def _fill(
    side: OrderSide = OrderSide.BUY,
    qty: Decimal = Decimal("1"),
    price: Decimal = Decimal("100"),
    fee: Decimal = Decimal("0.1"),
) -> Fill:
    return Fill(
        id=uuid4(),
        order_id=uuid4(),
        venue_fill_id="v1",
        side=side,
        qty=qty,
        price=price,
        fee=Money(fee, "USDT"),
        ts=TS,
        is_maker=False,
    )


def test_notional_is_qty_times_price() -> None:
    fill = _fill(qty=Decimal("2.5"), price=Decimal("40000"))
    assert fill.notional == Decimal("100000.0")


def test_signed_qty_is_positive_for_buy() -> None:
    fill = _fill(side=OrderSide.BUY, qty=Decimal("3"))
    assert fill.signed_qty == Decimal("3")


def test_signed_qty_is_negative_for_sell() -> None:
    fill = _fill(side=OrderSide.SELL, qty=Decimal("3"))
    assert fill.signed_qty == Decimal("-3")


def test_qty_must_be_positive() -> None:
    with pytest.raises(ValueError, match="qty"):
        _fill(qty=Decimal("0"))
    with pytest.raises(ValueError, match="qty"):
        _fill(qty=Decimal("-1"))


def test_price_must_be_positive() -> None:
    with pytest.raises(ValueError, match="price"):
        _fill(price=Decimal("0"))
    with pytest.raises(ValueError, match="price"):
        _fill(price=Decimal("-100"))


def test_qty_rejects_float() -> None:
    with pytest.raises(TypeError):
        _fill(qty=1.5)  # type: ignore[arg-type]


def test_price_rejects_float() -> None:
    with pytest.raises(TypeError):
        _fill(price=100.0)  # type: ignore[arg-type]


def test_fill_is_immutable() -> None:
    fill = _fill()
    with pytest.raises(FrozenInstanceError):
        fill.qty = Decimal("5")  # type: ignore[misc]
