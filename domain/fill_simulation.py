"""Slippage and fill simulation — SimulatedVenue's Fill Model (TASKS.md T-P2-06).

"Implement slippage and fill logic: (1) fills execute at next bar's
open + slippage, never the current bar's close; (2) slippage is
`f(spread, order_size / bar_volume, volatility)` — configurable model,
pessimistic defaults; (3) partial fills: order quantity vs. available
volume fraction; (4) simulated rejections: min-notional check,
precision check, insufficient-margin check. Fill timestamp is
`next_bar.open_time + simulated_latency` sampled from config."

Design decisions, and why:

- **This is the fill-simulation *model*, not the full `SimulatedVenue`
  class.** T-P2-06's own dependency list is "T-P2-05" only — the fee
  model, not T-P2-01 through T-P2-04's backtest engine. Building a full
  `VenuePort`-implementing `SimulatedVenue` now would go beyond what
  either T-P2-05 or T-P2-06 individually depend on; both are the pure,
  self-contained calculation pieces a later assembly (wherever
  `SimulatedVenue` is finally built, per ARCHITECTURE.md's own "swapped
  adapters" framing) will import and compose.
- **Placed in `domain/`, not `infrastructure/`.** Same reasoning as
  T-P2-05's `fee_schedule.py`: no port/`Protocol` is implemented, no
  I/O occurs, and every input (bar data, spread, volatility, available
  balance, latency) is supplied by the caller. This is pure arithmetic
  over `Order`/`Instrument`/`Candle`/`Fill`/`Money` — all domain
  types — mirroring their own style (frozen/slotted dataclasses,
  explicit float-guards, `StrEnum` for closed value sets).
- **`next_bar` is the *only* candle this module ever reads.** AC1
  ("an order submitted on bar `t` never fills at bar `t`'s close price
  — fills are always at bar `t+1` or later") holds structurally: this
  module's only price input is `next_bar.open`; bar `t`'s own close
  price is never a parameter anywhere in this file, so there is no
  code path that could reach for it. The caller (a future backtest
  loop) is responsible for actually passing bar `t+1`, not bar `t`.
- **Slippage is an absolute price adjustment, not a fractional
  multiplier**, added for a buy / subtracted for a sell, computed by an
  injectable `SlippageModel` — "configurable model" (TASKS.md's own
  words). `default_slippage_model` is the pessimistic default: half the
  spread (the cost of crossing it) plus a participation-rate-scaled,
  volatility-scaled market-impact term — a simple, monotonically
  worsening function of order size relative to bar volume and of
  volatility, never improving the trader's price. AC2 ("with zero
  slippage configured, fill price equals next bar's open exactly")
  holds for *any* slippage model that returns `Decimal("0")` given
  zero spread and zero volatility — including the default one — without
  needing a special-cased "no-slippage mode."
- **`spread` and `volatility` are caller-supplied numbers, not derived
  internally.** No order-book model or indicator library exists yet in
  this codebase (order books are explicitly deferred per DATABASE.md;
  indicators are T-P2-08/09, later tasks) — this module has no way to
  compute either from scratch, and doing so would be scope far beyond
  "implement slippage and fill logic." The caller supplies whatever
  numbers it has.
- **"Available volume fraction" (AC3) is `next_bar.volume *
  max_fill_fraction_of_volume`, defaulting to the full bar volume
  (fraction `1`).** No acceptance criterion names a specific throttling
  fraction below 100% of bar volume — AC3's own scenario ("order size >
  bar volume fills only the available fraction") is already fully
  satisfied by treating the *entire* bar's volume as available by
  default; `max_fill_fraction_of_volume` exists as the "configurable...
  pessimistic" knob a caller can tighten, without this module assuming
  a specific value nobody specified.
- **`simulated_latency` is an explicit `timedelta` parameter, not
  something this module samples from a distribution itself.** "Sampled
  from config" is read the same way this session has already resolved
  "loaded from config, not hardcoded" for T-P2-05's fee schedules: the
  caller (wherever real config/sampling lives) supplies an
  already-sampled value. A function that samples its own randomness
  internally could not satisfy AC4's implied determinism ("computed
  manually... matches... to the cent"); `simulated_latency` defaults to
  `timedelta(0)`, matching the zero-friction scenarios AC2/AC4 exercise.
- **Rejection reasons are a `StrEnum`** (`RejectionReason`), matching
  this codebase's own established convention for closed string-valued
  sets (`OrderStatus`, `OrderEventType`, `OrderSide`, ...). Precision
  and min-notional checks reuse `Instrument.lot_size`/`.min_notional`
  (T-P0-04) directly rather than duplicating those constraints;
  "insufficient margin" is checked against an explicit
  `available_balance: Money` parameter — no account/margin/portfolio
  concept exists anywhere yet in this codebase (it is not a listed
  dependency), so the balance to check against is simply supplied by
  the caller, exactly like `thirty_day_volume` in T-P2-05's fee model.
- **A bar with zero (or otherwise insufficient) available volume
  produces `SimulatedFillResult(fill=None, ...)`, not a rejection.**
  `Fill` (T-P0-06) itself requires `qty > 0`; there is nothing to
  reject about the *order* when a bar simply has no liquidity to offer
  it — the order remains open, unfilled, for the next bar. This is
  distinct from the three named rejection reasons, none of which is
  "no volume available."
- **Fee computation reuses `compute_fill_fee`/`FeeSchedule` from
  T-P2-05 unmodified** — the exact, literal dependency link TASKS.md's
  own dependency list names.
- **The Order's own state-machine transition (e.g., into `Rejected`,
  `PartiallyFilled`, or `Filled`) is left to the caller.**
  `Order.transition()` (T-P0-05) requires `seq`/`event_id` bookkeeping
  this module has no basis to invent; `simulate_fill` reports *what
  would happen* (a fill, a partial fill, no fill, or a named rejection
  reason) and leaves recording that against the order aggregate to
  whoever owns that sequence numbering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from domain.candle import Candle
from domain.fee_schedule import FeeSchedule, compute_fill_fee
from domain.fill import Fill
from domain.instrument import Instrument
from domain.money import CurrencyMismatch, Money
from domain.order import Order, OrderSide


def _reject_float(value: object, name: str) -> None:
    if isinstance(value, float):  # float-guard
        raise TypeError(f"{name} must be Decimal, not float")
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")


class SlippageModel(Protocol):
    """`f(spread, order_size / bar_volume, volatility)` (TASKS.md
    T-P2-06's own literal formula) — returns a non-negative absolute
    price adjustment, always worse for the trader, never better."""

    def __call__(
        self, *, spread: Decimal, participation_rate: Decimal, volatility: Decimal
    ) -> Decimal: ...


def default_slippage_model(
    *, spread: Decimal, participation_rate: Decimal, volatility: Decimal
) -> Decimal:
    """Pessimistic default: half the spread (the cost of crossing it)
    plus a participation-rate-scaled, volatility-scaled market-impact
    term. Returns exactly `Decimal("0")` when `spread` and `volatility`
    are both zero, regardless of `participation_rate` (TASKS.md AC2).
    """
    return spread / 2 + participation_rate * volatility


class RejectionReason(StrEnum):
    MIN_NOTIONAL = "min_notional"
    PRECISION = "precision"
    INSUFFICIENT_MARGIN = "insufficient_margin"


@dataclass(frozen=True, slots=True)
class SimulatedRejection:
    """`simulate_fill`'s result when the order is rejected outright."""

    reason: RejectionReason
    detail: str


@dataclass(frozen=True, slots=True)
class SimulatedFillResult:
    """`simulate_fill`'s result when no rejection occurred. `fill` is
    `None` if no volume was available this bar (not a rejection — the
    order simply carries forward, unfilled, to the next bar).
    `remaining_qty` is the order quantity still unfilled after this
    result — `0` once the order is fully filled.
    """

    fill: Fill | None
    remaining_qty: Decimal


def simulate_fill(
    *,
    order: Order,
    instrument: Instrument,
    next_bar: Candle,
    spread: Decimal,
    volatility: Decimal,
    fee_schedule: FeeSchedule,
    is_maker: bool,
    available_balance: Money,
    fill_id: UUID,
    venue_fill_id: str,
    slippage_model: SlippageModel = default_slippage_model,
    max_fill_fraction_of_volume: Decimal = Decimal("1"),
    simulated_latency: timedelta = timedelta(0),
    thirty_day_volume: Decimal = Decimal(0),
) -> SimulatedFillResult | SimulatedRejection:
    """Simulates filling `order`'s outstanding quantity against
    `next_bar` (bar `t+1` relative to whatever bar the order was
    submitted on — the caller's responsibility to supply correctly).

    Checks, in order: precision (remaining qty vs. `instrument
    .lot_size`) → available volume this bar → min notional (vs.
    `instrument.min_notional`) → available margin (vs.
    `available_balance`) → fee (via T-P2-05's `compute_fill_fee`).
    """
    for value, name in (
        (spread, "simulate_fill(spread=...)"),
        (volatility, "simulate_fill(volatility=...)"),
        (max_fill_fraction_of_volume, "simulate_fill(max_fill_fraction_of_volume=...)"),
        (thirty_day_volume, "simulate_fill(thirty_day_volume=...)"),
    ):
        _reject_float(value, name)
    if spread < 0:
        raise ValueError("simulate_fill: spread must be >= 0")
    if volatility < 0:
        raise ValueError("simulate_fill: volatility must be >= 0")
    if available_balance.currency != instrument.quote_currency:
        raise CurrencyMismatch(available_balance.currency, instrument.quote_currency)

    remaining_qty = order.qty - order.filled_qty
    if remaining_qty <= 0:
        raise ValueError("simulate_fill: order has no remaining quantity to fill")

    if remaining_qty % instrument.lot_size != 0:
        return SimulatedRejection(
            reason=RejectionReason.PRECISION,
            detail=(
                f"remaining qty {remaining_qty} is not a multiple of "
                f"lot_size {instrument.lot_size}"
            ),
        )

    available_qty = next_bar.volume * max_fill_fraction_of_volume
    fillable_qty = min(remaining_qty, available_qty)
    if fillable_qty <= 0:
        return SimulatedFillResult(fill=None, remaining_qty=remaining_qty)

    participation_rate = remaining_qty / next_bar.volume if next_bar.volume > 0 else Decimal(0)
    slippage_amount = slippage_model(
        spread=spread, participation_rate=participation_rate, volatility=volatility
    )
    direction = Decimal(1) if order.side is OrderSide.BUY else Decimal(-1)
    fill_price = next_bar.open + direction * slippage_amount

    notional = fillable_qty * fill_price
    if notional < instrument.min_notional:
        return SimulatedRejection(
            reason=RejectionReason.MIN_NOTIONAL,
            detail=f"notional {notional} is below min_notional {instrument.min_notional}",
        )

    if notional > available_balance.amount:
        return SimulatedRejection(
            reason=RejectionReason.INSUFFICIENT_MARGIN,
            detail=f"notional {notional} exceeds available balance {available_balance.amount}",
        )

    fee = compute_fill_fee(
        qty=fillable_qty,
        price=fill_price,
        quote_currency=instrument.quote_currency,
        is_maker=is_maker,
        fee_schedule=fee_schedule,
        thirty_day_volume=thirty_day_volume,
    )

    fill = Fill(
        id=fill_id,
        order_id=order.id,
        venue_fill_id=venue_fill_id,
        side=order.side,
        qty=fillable_qty,
        price=fill_price,
        fee=fee,
        ts=next_bar.open_time + simulated_latency,
        is_maker=is_maker,
    )

    return SimulatedFillResult(fill=fill, remaining_qty=remaining_qty - fillable_qty)
