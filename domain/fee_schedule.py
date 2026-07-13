"""FeeSchedule and fee/funding calculation — SimulatedVenue's Fee Model
(TASKS.md T-P2-05).

"Implement fee calculation inside `SimulatedVenue`: maker/taker fees by
configurable tier schedule (as a percentage of notional), funding rate
charges for perps (accrued every 8 hours against the open position at
the simulated time). Fee schedules are loaded from config, not
hardcoded. Fees are deducted in the fill and propagated to the ledger."

Design decisions, and why:

- **This is the fee-calculation *model*, not the full `SimulatedVenue`
  class.** T-P2-05's own dependency list is "T-P0-05, T-P0-06" —
  `Order` and `Fill`/`Position` only. It does not depend on T-P2-01
  through T-P2-04's backtest engine, nor does T-P2-06 ("SimulatedVenue —
  Slippage and Fill Model," a separate, parallel task building the
  *other* half of the same eventual class) appear as a dependency here
  either. Building a full `VenuePort`-implementing `SimulatedVenue` now
  would require order-matching/fill-simulation logic that is
  explicitly T-P2-06's own scope. This module is the pure,
  self-contained calculation `SimulatedVenue` (wherever it is finally
  assembled — infrastructure/, an adapter, per ARCHITECTURE.md's
  "swapped adapters" framing already used for `RealClock`/
  `SimulatedClock` and `HistoricalFeed`) will import and use.
- **Placed in `domain/`, not `infrastructure/`.** Unlike T-P2-01/02/03
  (concrete implementations of a domain *port*), nothing here adapts an
  external system or implements a `Protocol` — it is pure,
  side-effect-free financial arithmetic over `Money`/`Decimal`, exactly
  the kind of "shared code, identical in live and backtest, no forks"
  ARCHITECTURE.md places alongside "Position Sizing... Portfolio Math."
  It sits naturally next to `domain/money.py`, `domain/fill.py`, and
  `domain/position.py`, which it depends on and mirrors in style
  (frozen, slotted dataclasses; explicit float-guards).
- **"Loaded from config, not hardcoded" means dependency injection, not
  a file-parsing config loader.** No acceptance criterion tests reading
  a YAML/JSON/env file — AC1/AC2 both give concrete numeric rates
  directly. `FeeSchedule`/`FeeTier` are plain, constructible value
  objects; whatever later task wires up real configuration (env,
  `infrastructure/secrets/`, a settings file) constructs one and passes
  it in — nothing in this module reads any external source itself,
  matching the same resolution already used for T-P1-11's "S3 or local
  filesystem" and T-P2-01's "lint rule from T-P0-01."
- **"Tier schedule" is modeled as a sorted tuple of `FeeTier`s, each
  qualified by a minimum trailing 30-day volume — not a single flat
  rate pair.** The task's own wording names "tier schedule" explicitly;
  a schedule that structurally supports multiple tiers (with a
  mandatory zero-threshold base tier, so every schedule is usable
  without tracking any volume at all) is a literal, minimal reading of
  that phrase. Computing a trader's actual trailing 30-day volume is
  explicitly out of scope — no acceptance criterion needs it, and it
  would require account/history state this task has no access to;
  `thirty_day_volume` is simply an input the caller supplies (default
  zero, landing on the base tier — exactly what AC1/AC2 exercise).
- **The funding rate itself is a per-accrual input to `accrue()`, not a
  field of `FeeSchedule`.** Real perpetual funding rates change over
  time (typically recomputed every funding interval from market
  conditions); baking a single static rate into the schedule would
  contradict how funding actually behaves. Only `funding_interval` (the
  8-hour cadence itself — a genuine, static schedule parameter) lives on
  `FeeSchedule`.
- **`FundingAccrualTracker` is a small stateful class, not a pure
  function — accrual inherently needs to remember "when was funding
  last paid."** It advances its own reference point by exactly one
  `funding_interval` per accrued payment (not by jumping straight to
  `current_ts`), so calling it after a longer gap than one interval
  correctly emits one payment per interval boundary actually crossed,
  rather than silently coalescing multiple missed accruals into one.
- **Funding payment sign**: `-(qty * mark_price * funding_rate)`,
  matching the standard perpetual-futures convention (e.g. Binance): a
  positive `funding_rate` means longs (`qty > 0`) pay shorts — the
  position's own cash flow is negative (pays) — and shorts (`qty < 0`)
  receive. This sign convention is documented on `FundingPayment.amount`
  itself; no acceptance criterion tests the sign, only the *count* of
  payments (AC3), which is unaffected by it.
- **AC4 ("fees-as-percentage-of-gross-P&L is computable from the output
  of any backtest run") is satisfied by two small, pure utility
  functions over a plain sequence of `Fill`s — not by modifying
  `BacktestResult` (T-P2-04) or building the metrics/tearsheet system.**
  `fees_pct_of_gross` is already a real column on the existing
  `backtest_metrics` table (DATABASE.md/T-P0-11); *computing and storing
  it as a tracked metric* is T-P2-11's own later, dedicated task
  ("Backtest Metrics and Tearsheet"). T-P2-05's job — not a listed
  dependency of T-P2-04's loop, and no acceptance criterion here
  mentions `BacktestResult` — is only to make the underlying arithmetic
  possible from data any backtest run already produces: a sequence of
  `Fill`s, each already carrying `fee: Money` (T-P0-06), plus a realized
  gross P&L figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from domain.fill import Fill
from domain.money import CurrencyMismatch, Money

_DEFAULT_FUNDING_INTERVAL = timedelta(hours=8)


def _reject_float(value: object, name: str) -> None:
    if isinstance(value, float):  # float-guard
        raise TypeError(f"{name} must be Decimal, not float")
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")


@dataclass(frozen=True, slots=True)
class FeeTier:
    """One tier of a maker/taker fee schedule, qualified by a minimum
    trailing 30-day trading volume. Rates are fractions of notional
    (e.g. `Decimal("0.001")` for 0.1%), not percentages."""

    thirty_day_volume_threshold: Decimal
    maker_rate: Decimal
    taker_rate: Decimal

    def __post_init__(self) -> None:
        _reject_float(self.thirty_day_volume_threshold, "FeeTier.thirty_day_volume_threshold")
        _reject_float(self.maker_rate, "FeeTier.maker_rate")
        _reject_float(self.taker_rate, "FeeTier.taker_rate")
        if self.thirty_day_volume_threshold < 0:
            raise ValueError("FeeTier.thirty_day_volume_threshold must be >= 0")
        if self.maker_rate < 0 or self.taker_rate < 0:
            raise ValueError("FeeTier.maker_rate and taker_rate must be >= 0")


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """A configurable, tiered maker/taker fee schedule plus the funding
    accrual cadence — constructed by the caller from whatever config
    source it uses; this type itself reads nothing external."""

    tiers: tuple[FeeTier, ...]
    funding_interval: timedelta = _DEFAULT_FUNDING_INTERVAL

    def __post_init__(self) -> None:
        if not self.tiers:
            raise ValueError("FeeSchedule.tiers must be non-empty")
        if not any(tier.thirty_day_volume_threshold == 0 for tier in self.tiers):
            raise ValueError("FeeSchedule.tiers must include a base tier (threshold 0)")
        thresholds = [tier.thirty_day_volume_threshold for tier in self.tiers]
        if thresholds != sorted(thresholds) or len(set(thresholds)) != len(thresholds):
            raise ValueError(
                "FeeSchedule.tiers must be sorted strictly ascending by volume threshold"
            )
        if self.funding_interval <= timedelta(0):
            raise ValueError("FeeSchedule.funding_interval must be positive")

    def tier_for_volume(self, thirty_day_volume: Decimal) -> FeeTier:
        """The highest-threshold tier `thirty_day_volume` qualifies for."""
        _reject_float(thirty_day_volume, "thirty_day_volume")
        qualifying = [
            tier for tier in self.tiers if thirty_day_volume >= tier.thirty_day_volume_threshold
        ]
        return max(qualifying, key=lambda tier: tier.thirty_day_volume_threshold)


def compute_fill_fee(
    *,
    qty: Decimal,
    price: Decimal,
    quote_currency: str,
    is_maker: bool,
    fee_schedule: FeeSchedule,
    thirty_day_volume: Decimal = Decimal(0),
) -> Money:
    """The fee owed on one fill of `qty` at `price`: `notional * rate`,
    where `rate` is the maker or taker rate of the `fee_schedule` tier
    `thirty_day_volume` qualifies for. `notional = qty * price`, the
    same formula as `Fill.notional` (T-P0-06) — computed here directly
    since no `Fill` exists yet at the point its own fee is decided.
    """
    _reject_float(qty, "compute_fill_fee(qty=...)")
    _reject_float(price, "compute_fill_fee(price=...)")
    notional = Money(qty * price, quote_currency)
    tier = fee_schedule.tier_for_volume(thirty_day_volume)
    rate = tier.maker_rate if is_maker else tier.taker_rate
    return notional * rate


@dataclass(frozen=True, slots=True)
class FundingPayment:
    """One accrued funding payment, from the position's own point of
    view: `amount` is negative when the position pays (longs pay when
    `funding_rate > 0`) and positive when it receives."""

    amount: Money
    accrued_at: datetime


class FundingAccrualTracker:
    """Accrues a perp position's funding payment every
    `funding_interval` (TASKS.md default: 8 hours) of simulated time it
    stays open, against the open position at each accrual moment.
    """

    def __init__(
        self, *, opened_at: datetime, funding_interval: timedelta = _DEFAULT_FUNDING_INTERVAL
    ) -> None:
        if funding_interval <= timedelta(0):
            raise ValueError("FundingAccrualTracker.funding_interval must be positive")
        self._funding_interval = funding_interval
        self._last_accrual_at = opened_at

    @property
    def last_accrual_at(self) -> datetime:
        return self._last_accrual_at

    def accrue(
        self,
        *,
        current_ts: datetime,
        position_qty: Decimal,
        mark_price: Decimal,
        funding_rate: Decimal,
        quote_currency: str,
    ) -> list[FundingPayment]:
        """Returns zero or more `FundingPayment`s — one for every
        `funding_interval` boundary crossed since the last call (or
        since the position opened, on the first call)."""
        _reject_float(position_qty, "accrue(position_qty=...)")
        _reject_float(mark_price, "accrue(mark_price=...)")
        _reject_float(funding_rate, "accrue(funding_rate=...)")

        payments: list[FundingPayment] = []
        while current_ts - self._last_accrual_at >= self._funding_interval:
            self._last_accrual_at += self._funding_interval
            amount = -(position_qty * mark_price * funding_rate)
            payments.append(
                FundingPayment(
                    amount=Money(amount, quote_currency),
                    accrued_at=self._last_accrual_at,
                )
            )
        return payments


def total_fees_paid(fills: Sequence[Fill]) -> Money:
    """Sums every fill's own `fee` (T-P0-06). All fills must share one
    fee currency; a genuine cross-currency backtest is out of scope
    (`Money.__add__`'s existing `CurrencyMismatch` guard enforces this).
    """
    if not fills:
        raise ValueError("total_fees_paid requires at least one fill")
    total = fills[0].fee
    for fill in fills[1:]:
        total = total + fill.fee
    return total


def fees_as_percentage_of_gross_pnl(*, total_fees: Money, gross_pnl: Money) -> Decimal:
    """TASKS.md T-P2-05 AC4: "Fees-as-percentage-of-gross-P&L is
    computable from the output of any backtest run" — this is that
    computation, given the two aggregates any backtest run's fills and
    realized P&L already provide. `gross_pnl` is taken by absolute
    value: fee drag is reported as a positive percentage regardless of
    whether the run was profitable.
    """
    if total_fees.currency != gross_pnl.currency:
        raise CurrencyMismatch(total_fees.currency, gross_pnl.currency)
    if gross_pnl.amount == 0:
        raise ZeroDivisionError("fees_as_percentage_of_gross_pnl: gross_pnl is zero")
    return (total_fees.amount / abs(gross_pnl.amount)) * Decimal(100)
