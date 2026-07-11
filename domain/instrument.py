"""Instrument domain model — sealed asset-class variant hierarchy.

ARCHITECTURE.md §3.8.1: "`Instrument` is a small, closed core: identity,
venue, base/quote currency, tick size, lot size, min notional, trading
calendar. Facts every tradeable thing has." Asset-class specifics — what
makes a perpetual swap different from a spot pair — live in a sealed
variant hierarchy instead of nullable fields bolted onto one flat class:
"The naive design makes `Instrument` a single class with nullable fields
... This is the design that rots." Consumers pattern-match exhaustively,
so mypy's `assert_never` flags every place that needs updating when a new
asset class is added — "the compiler becomes the migration checklist."

Phase 0 (T-P0-04) implements three variants: `Spot`, `PerpetualSwap`,
`DatedFuture`. `Equity` and `Option` are named in ARCHITECTURE.md §3.8.1
but are out of scope until T-P9-04.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, assert_never
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

InstrumentStatus = Literal["trading", "halted", "delisted"]


class Spot(BaseModel):
    """A spot instrument has no asset-class-specific data beyond the discriminator."""

    model_config = ConfigDict(frozen=True)

    asset_class: Literal["spot"] = "spot"


class PerpetualSwap(BaseModel):
    """A perpetual swap: funding payments accrue on a fixed interval against a mark price."""

    model_config = ConfigDict(frozen=True)

    asset_class: Literal["perpetual_swap"] = "perpetual_swap"
    funding_interval: timedelta
    mark_price_type: Literal["last_price", "index_price", "fair_price"]


class DatedFuture(BaseModel):
    """A dated future: expires on a fixed date and is rolled some days beforehand."""

    model_config = ConfigDict(frozen=True)

    asset_class: Literal["dated_future"] = "dated_future"
    expiry: datetime
    multiplier: Decimal
    roll_days: int


# Plain union alias for use in ordinary function signatures (pattern matching,
# helper functions, ...). The Pydantic-specific discriminator wiring lives
# only at the `Instrument.details` field declaration below, where it is
# actually needed to pick the right variant class during validation.
AssetClassDetails = Spot | PerpetualSwap | DatedFuture


class Instrument(BaseModel):
    """The small, closed core every tradeable thing has (ARCHITECTURE.md §3.8.1).

    Asset-class-specific data lives in `details`, a discriminated union over
    the sealed variant hierarchy — never as nullable fields on this class.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    venue_id: UUID
    symbol: str
    base_currency: str
    quote_currency: str
    tick_size: Decimal = Field(gt=Decimal("0"))
    lot_size: Decimal = Field(gt=Decimal("0"))
    min_notional: Decimal = Field(gt=Decimal("0"))
    status: InstrumentStatus
    details: Annotated[AssetClassDetails, Field(discriminator="asset_class")]

    @property
    def asset_class(self) -> Literal["spot", "perpetual_swap", "dated_future"]:
        """Convenience accessor mirroring `details.asset_class`.

        DATABASE.md models `asset_class` as a flat column directly on the
        Instrument table; this property bridges that persistence-shaped
        view without duplicating the discriminator as real state here.
        """
        return self.details.asset_class


def describe_asset_class(details: AssetClassDetails) -> str:
    """Canonical exhaustive match over the sealed variant hierarchy.

    This is the pattern every future consumer of `Instrument.details` must
    follow: match on the discriminated union and let `assert_never` make an
    unhandled variant a mypy error, not a runtime surprise.
    """
    match details:
        case Spot():
            return "spot"
        case PerpetualSwap(funding_interval=interval, mark_price_type=mark_price_type):
            return f"perpetual swap (funds every {interval}, marked to {mark_price_type})"
        case DatedFuture(expiry=expiry, multiplier=multiplier, roll_days=roll_days):
            return (
                f"dated future expiring {expiry.isoformat()} "
                f"(multiplier {multiplier}, rolls {roll_days}d before expiry)"
            )
        case _:
            assert_never(details)
