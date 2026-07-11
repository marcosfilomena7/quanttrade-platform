"""Tests for the Money value object (domain/money.py)."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from domain.money import CurrencyMismatch, Money


def test_addition_of_same_currency() -> None:
    assert Money(Decimal("10.5"), "USD") + Money(Decimal("4.5"), "USD") == Money(
        Decimal("15.0"), "USD"
    )


def test_addition_of_different_currencies_raises_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatch):
        Money(Decimal("10"), "USD") + Money(Decimal("5"), "EUR")


def test_subtraction_of_same_currency() -> None:
    assert Money(Decimal("10"), "USD") - Money(Decimal("4"), "USD") == Money(
        Decimal("6"), "USD"
    )


def test_subtraction_of_different_currencies_raises_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatch):
        Money(Decimal("10"), "USD") - Money(Decimal("5"), "EUR")


def test_currency_mismatch_message_names_both_currencies() -> None:
    with pytest.raises(CurrencyMismatch) as exc_info:
        Money(Decimal("10"), "USD") + Money(Decimal("5"), "EUR")
    assert "USD" in str(exc_info.value)
    assert "EUR" in str(exc_info.value)


def test_multiplication_by_decimal_scalar() -> None:
    assert Money(Decimal("10"), "USD") * Decimal("3") == Money(Decimal("30"), "USD")


def test_multiplication_by_int_scalar() -> None:
    assert Money(Decimal("10"), "USD") * 3 == Money(Decimal("30"), "USD")


def test_right_multiplication_by_decimal_scalar() -> None:
    assert Decimal("3") * Money(Decimal("10"), "USD") == Money(Decimal("30"), "USD")


def test_right_multiplication_by_int_scalar() -> None:
    assert 3 * Money(Decimal("10"), "USD") == Money(Decimal("30"), "USD")


def test_division_by_decimal_scalar() -> None:
    assert Money(Decimal("10"), "USD") / Decimal("2") == Money(Decimal("5"), "USD")


def test_division_by_int_scalar() -> None:
    assert Money(Decimal("10"), "USD") / 2 == Money(Decimal("5"), "USD")


def test_money_is_immutable() -> None:
    money = Money(Decimal("10"), "USD")
    with pytest.raises(FrozenInstanceError):
        money.amount = Decimal("20")  # type: ignore[misc]


def test_construction_rejects_float_amount() -> None:
    with pytest.raises(TypeError):
        Money(1.5, "USD")  # type: ignore[arg-type]


def test_construction_rejects_non_decimal_amount() -> None:
    with pytest.raises(TypeError):
        Money(10, "USD")  # type: ignore[arg-type]


def test_construction_rejects_empty_currency() -> None:
    with pytest.raises(ValueError, match="currency"):
        Money(Decimal("10"), "")


def test_multiplication_rejects_float_scalar() -> None:
    with pytest.raises(TypeError):
        Money(Decimal("10"), "USD") * 1.5  # type: ignore[operator]


def test_division_rejects_float_scalar() -> None:
    with pytest.raises(TypeError):
        Money(Decimal("10"), "USD") / 2.0  # type: ignore[operator]


def test_multiplication_rejects_bool_scalar() -> None:
    with pytest.raises(TypeError):
        Money(Decimal("10"), "USD") * True  # type: ignore[operator]


def test_multiplication_of_money_by_money_is_rejected() -> None:
    with pytest.raises(TypeError):
        Money(Decimal("10"), "USD") * Money(Decimal("2"), "USD")  # type: ignore[operator]


def test_addition_with_non_money_operand_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Money(Decimal("10"), "USD") + Decimal("5")  # type: ignore[operator]


def test_no_float_path_exists_across_full_arithmetic_chain() -> None:
    """End-to-end: build a Money value through +, -, *, / and confirm the
    internal amount is always a Decimal, never a float, at every step."""
    result = Money(Decimal("100"), "USD")
    result = result + Money(Decimal("50"), "USD")
    result = result - Money(Decimal("25"), "USD")
    result = result * Decimal("2")
    result = result / 5
    assert isinstance(result.amount, Decimal)
    assert result == Money(Decimal("50"), "USD")
