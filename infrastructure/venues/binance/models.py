"""Typed Pydantic v2 response models for the Binance Spot REST API.

TASKS.md T-P1-01: "Return typed response models (Pydantic v2), never raw
dicts. ... All typed response fields use Decimal for price and quantity
fields, never float." Every price/quantity-shaped field below (tick
size, step size, min/max price, min/max quantity, min notional, account
balances) is typed `Decimal` — Pydantic v2 parses Binance's JSON string
values (e.g. `"tickSize": "0.00000100"`) straight into `Decimal` without
an intermediate `float`. Fields that are genuinely integers (timestamps
in epoch milliseconds, commission rates in whole basis-point-style
units) are typed `int`; they are not price, quantity, or balance data,
so ARCHITECTURE.md §3.3's Decimal rule does not apply to them.

Only the fields actually needed downstream (T-P1-02's `Instrument`
mapping needs `tickSize`/`stepSize`/`minNotional`; nothing yet needs the
full Binance filter/permission zoo) are modeled explicitly. `SymbolFilter`
uses `extra="allow"` so an exchangeInfo response is never rejected for
carrying a filter type or field this task doesn't need to read.

Binance's JSON uses camelCase keys; every field below is declared in
this repo's snake_case convention with a matching `alias`, and
`populate_by_name=True` so tests can also construct instances directly
by the Python attribute name.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SymbolFilter(BaseModel):
    """One entry from a symbol's `filters` array.

    Only the price/lot-size/notional filters carry fields T-P1-01
    guarantees as `Decimal`; other filter types (e.g. `MARKET_LOT_SIZE`,
    `PERCENT_PRICE_BY_SIDE`) still parse correctly via `filter_type`
    alone — mapping them to `Instrument.tick_size`/`lot_size`/
    `min_notional` is T-P1-02's job, not this one's.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="allow")

    filter_type: str = Field(alias="filterType")
    tick_size: Decimal | None = Field(default=None, alias="tickSize")
    min_price: Decimal | None = Field(default=None, alias="minPrice")
    max_price: Decimal | None = Field(default=None, alias="maxPrice")
    step_size: Decimal | None = Field(default=None, alias="stepSize")
    min_qty: Decimal | None = Field(default=None, alias="minQty")
    max_qty: Decimal | None = Field(default=None, alias="maxQty")
    min_notional: Decimal | None = Field(default=None, alias="minNotional")


class ExchangeSymbol(BaseModel):
    """One entry from `exchangeInfo`'s `symbols` array."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    symbol: str
    status: str
    base_asset: str = Field(alias="baseAsset")
    quote_asset: str = Field(alias="quoteAsset")
    base_asset_precision: int = Field(alias="baseAssetPrecision")
    quote_asset_precision: int = Field(alias="quoteAssetPrecision")
    filters: list[SymbolFilter] = Field(default_factory=list)


class ExchangeInfoResponse(BaseModel):
    """`GET /api/v3/exchangeInfo` — the response T-P1-02 will map into
    `Instrument` domain objects."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    timezone: str
    server_time: int = Field(alias="serverTime")
    symbols: list[ExchangeSymbol] = Field(default_factory=list)


class AccountBalance(BaseModel):
    """One entry from `account`'s `balances` array."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    asset: str
    free: Decimal
    locked: Decimal


class AccountResponse(BaseModel):
    """`GET /api/v3/account` — a signed endpoint, used here mainly to
    exercise and test this client's HMAC-SHA256 signing path."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    maker_commission: int = Field(alias="makerCommission")
    taker_commission: int = Field(alias="takerCommission")
    can_trade: bool = Field(alias="canTrade")
    can_withdraw: bool = Field(alias="canWithdraw")
    can_deposit: bool = Field(alias="canDeposit")
    update_time: int = Field(alias="updateTime")
    account_type: str = Field(alias="accountType")
    balances: list[AccountBalance] = Field(default_factory=list)
