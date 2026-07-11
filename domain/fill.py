"""Fill domain model — a single, possibly partial, execution against an order.

DATABASE.md §13 Fill: "id, order_id, venue_id, venue_fill_id, qty, price,
fee, fee_currency, is_maker, ts." `venue_fill_id` plus a UNIQUE(venue_id,
venue_fill_id) constraint (a persistence-layer concern, not modeled here)
is what makes fill processing exactly-once — ARCHITECTURE.md §7.3/M7.

One field beyond DATABASE.md's and TASKS.md T-P0-06's literal list is
unavoidable: `side`. Neither DATABASE.md's Fill entity nor T-P0-06's own
field list ("id, order_id, venue_fill_id, qty: Decimal, price: Decimal,
fee: Money, ts, is_maker") includes it, and DATABASE.md constrains
`qty > 0` — unsigned. Without a side, nothing could tell
`Position.apply_fill` whether a fill increases or decreases the position,
which is the entire point of this task. `venue_id` and `fee_currency`
(DATABASE.md has both; `fee: Money` already carries its own currency,
making a separate `fee_currency` field redundant here) are persistence and
idempotency concerns with no role in the pure Position math this task
implements, so they are intentionally not modeled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from domain.money import Money
from domain.order import OrderSide


@dataclass(frozen=True, slots=True)
class Fill:
    """An individual execution. `qty` and `price` are always positive
    (DATABASE.md constraint); `side` gives them their sign against a
    Position via `signed_qty`.
    """

    id: UUID
    order_id: UUID
    venue_fill_id: str
    side: OrderSide
    qty: Decimal
    price: Decimal
    fee: Money
    ts: datetime
    is_maker: bool

    def __post_init__(self) -> None:
        if isinstance(self.qty, float) or isinstance(self.price, float):  # float-guard
            raise TypeError("Fill.qty and Fill.price must be Decimal, not float")
        if not isinstance(self.qty, Decimal) or not isinstance(self.price, Decimal):
            raise TypeError("Fill.qty and Fill.price must be Decimal")
        if self.qty <= 0:
            raise ValueError("Fill.qty must be > 0 (DATABASE.md constraint)")
        if self.price <= 0:
            raise ValueError("Fill.price must be > 0 (DATABASE.md constraint)")

    @property
    def notional(self) -> Decimal:
        """qty * price. Always positive; denominated in the instrument's
        quote currency, which this task assumes matches `fee.currency`
        (see domain/position.py's module docstring)."""
        return self.qty * self.price

    @property
    def signed_qty(self) -> Decimal:
        """+qty for a buy, -qty for a sell — matches Position.qty's sign
        convention (DATABASE.md: "signed: + long, − short")."""
        return self.qty if self.side is OrderSide.BUY else -self.qty
