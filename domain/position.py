"""Position domain model — current holding of one instrument by one strategy instance.

DATABASE.md §16 Position: "instrument_id, strategy_instance_id, qty
(signed: + long, − short), avg_entry_price, realized_pnl." §10.1 point 6:
"the risk engine must be able to derive exposure independently. Position
is the materialized, queryable answer, checkable as Σ(fills) via property
test (§3.9)."

`Position.apply_fill` uses running weighted-average cost accounting, not a
literal per-lot FIFO queue — there is no lot table to maintain one in:
neither T-P0-06's field list nor DATABASE.md's Position entity stores
anything beyond a single scalar `avg_entry`. This is not a shortcut;
ARCHITECTURE.md §3.9 states the reason precisely as its own invariant:
"Realized + unrealized P&L is invariant under FIFO vs. LIFO lot ordering
for *total* (though not per-lot)." For the TOTAL P&L this model tracks,
weighted-average-cost, FIFO, and LIFO all agree by construction. Per-lot
attribution — needed only for tax reporting — is P-09 (Phase 9),
explicitly out of scope here.

Every fill's fee is booked to `realized_pnl` immediately, whether the fill
opens, adds to, reduces, or closes a position. Only price-based P&L is
deferred until a position-reducing fill realizes it. This keeps the two
effects orthogonal and simple to reason about, and matches how "realized
P&L" is reported in practice: inclusive of every transaction cost incurred
on the instrument, not just the ones tied to a closing trade.

One field beyond the task's literal list is unavoidable:
`applied_fill_ids`. Without tracking which fills have already been
applied, "applying the same fill twice produces the same state as once"
(TASKS.md T-P0-06; ARCHITECTURE.md §3.9) cannot hold as a property of this
class itself — it would depend entirely on the caller never making a
mistake, which is exactly the failure mode this invariant exists to rule
out (M7: "Lost fills... Belt and suspenders — this is one place
redundancy is warranted").
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from domain.fill import Fill
from domain.money import Money


@dataclass(frozen=True, slots=True)
class Position:
    instrument_id: UUID
    strategy_instance_id: UUID
    qty: Decimal
    avg_entry: Decimal
    realized_pnl: Money
    applied_fill_ids: frozenset[UUID] = frozenset()

    def __post_init__(self) -> None:
        if isinstance(self.qty, float) or isinstance(self.avg_entry, float):  # float-guard
            raise TypeError("Position.qty and Position.avg_entry must be Decimal, not float")
        if not isinstance(self.qty, Decimal) or not isinstance(self.avg_entry, Decimal):
            raise TypeError("Position.qty and Position.avg_entry must be Decimal")

    @classmethod
    def flat(cls, instrument_id: UUID, strategy_instance_id: UUID, currency: str) -> Position:
        """A brand-new position with no holdings and no realized P&L yet."""
        return cls(
            instrument_id=instrument_id,
            strategy_instance_id=strategy_instance_id,
            qty=Decimal("0"),
            avg_entry=Decimal("0"),
            realized_pnl=Money(Decimal("0"), currency),
        )

    def apply_fill(self, fill: Fill) -> Position:
        """Apply one fill, returning a new `Position`.

        Idempotent: re-applying a fill whose `id` has already been applied
        returns `self` unchanged, regardless of how many times it's retried.
        """
        if fill.id in self.applied_fill_ids:
            return self

        signed_delta = fill.signed_qty
        new_qty = self.qty + signed_delta
        new_applied = self.applied_fill_ids | {fill.id}

        same_direction = self.qty == 0 or (self.qty > 0) == (signed_delta > 0)

        if same_direction:
            # Opening from flat, or adding to the position in the current
            # direction: pure weighted-average cost basis update. No price
            # P&L is realized — only the fee, an immediate cost regardless
            # of direction.
            old_abs = abs(self.qty)
            new_avg_entry = (old_abs * self.avg_entry + fill.qty * fill.price) / (
                old_abs + fill.qty
            )
            price_pnl = Decimal("0")
        else:
            # Reduces, exactly closes, or closes-and-flips the position.
            direction = Decimal(1) if self.qty > 0 else Decimal(-1)
            closing_qty = min(abs(self.qty), fill.qty)
            price_pnl = closing_qty * (fill.price - self.avg_entry) * direction

            if new_qty == 0:
                new_avg_entry = Decimal("0")
            elif (new_qty > 0) != (self.qty > 0):
                # Flipped: the excess beyond closing_qty opened a fresh
                # position in the opposite direction, at the fill price.
                new_avg_entry = fill.price
            else:
                # Reduced but still same direction: the remaining lot
                # keeps its original cost basis.
                new_avg_entry = self.avg_entry

        realized_delta = Money(price_pnl, fill.fee.currency) - fill.fee

        return replace(
            self,
            qty=new_qty,
            avg_entry=new_avg_entry,
            realized_pnl=self.realized_pnl + realized_delta,
            applied_fill_ids=new_applied,
        )
