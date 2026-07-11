"""Money value object.

ARCHITECTURE.md §3.3 / §3.5: "Decimal everywhere. Float is banned in any
code path touching price, quantity, or balance. Enforced by lint rule and
type checker, not by discipline."

`Money` pairs a `Decimal` amount with a currency code and is immutable.
Arithmetic between two `Money` instances of different currencies is a
programming error, not a runtime edge case to tolerate — it raises
`CurrencyMismatch` rather than silently producing a nonsensical value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class CurrencyMismatch(ValueError):  # noqa: N818 — name fixed by docs/TASKS.md T-P0-03
    """Raised when an operation combines Money instances of different currencies."""

    def __init__(self, left_currency: str, right_currency: str) -> None:
        self.left_currency = left_currency
        self.right_currency = right_currency
        super().__init__(
            f"Cannot combine Money in different currencies: "
            f"{left_currency!r} vs {right_currency!r}"
        )


@dataclass(frozen=True, slots=True)
class Money:
    """An immutable amount of a given currency.

    `amount` must be a `Decimal`. Float is rejected at construction time and
    at every arithmetic operation — this is a runtime backstop in addition
    to the static `float` ban enforced by `scripts/check_no_float.py`
    (wired into `make lint`).
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):  # float-guard: rejects float, does not use it
            raise TypeError("Money.amount must be a Decimal, not float")
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"Money.amount must be a Decimal, got {type(self.amount).__name__}")
        if not isinstance(self.currency, str) or not self.currency:
            raise ValueError("Money.currency must be a non-empty string")

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(self.currency, other.currency)

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, scalar: object) -> Money:
        return Money(self.amount * self._require_decimal_scalar(scalar), self.currency)

    def __rmul__(self, scalar: object) -> Money:
        return self.__mul__(scalar)

    def __truediv__(self, scalar: object) -> Money:
        return Money(self.amount / self._require_decimal_scalar(scalar), self.currency)

    @staticmethod
    def _require_decimal_scalar(scalar: object) -> Decimal:
        if isinstance(scalar, bool):
            raise TypeError("Money arithmetic does not support bool as a scalar")
        if isinstance(scalar, float):  # float-guard: rejects float, does not use it
            raise TypeError("Money arithmetic does not support float; use Decimal")
        if isinstance(scalar, Decimal):
            return scalar
        if isinstance(scalar, int):
            return Decimal(scalar)
        raise TypeError(
            f"Money can only be multiplied or divided by Decimal or int, "
            f"got {type(scalar).__name__}"
        )
