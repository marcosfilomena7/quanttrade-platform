"""Unit tests for infrastructure/jobs/reference_data_importer.py's pure
mapping logic — no database required.

The end-to-end upsert/idempotency/change-detection behavior against a
real Postgres is covered separately by
test_reference_data_importer_integration.py (TASKS.md T-P1-02: "Integration
test runs against the test Postgres instance").
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from domain.instrument import Spot
from infrastructure.jobs.reference_data_importer import (
    ReferenceDataError,
    build_instrument,
    extract_trading_rules,
    map_status,
)
from infrastructure.venues.binance.models import ExchangeSymbol, SymbolFilter

_VALID_FILTERS = [
    SymbolFilter(filter_type="PRICE_FILTER", tick_size=Decimal("0.01000000")),
    SymbolFilter(filter_type="LOT_SIZE", step_size=Decimal("0.00001000")),
    SymbolFilter(filter_type="MIN_NOTIONAL", min_notional=Decimal("10.00000000")),
]


def _symbol(
    *,
    symbol: str = "BTCUSDT",
    status: str = "TRADING",
    filters: list[SymbolFilter] | None = None,
) -> ExchangeSymbol:
    return ExchangeSymbol(
        symbol=symbol,
        status=status,
        base_asset="BTC",
        quote_asset="USDT",
        base_asset_precision=8,
        quote_asset_precision=8,
        filters=filters if filters is not None else _VALID_FILTERS,
    )


# --- extract_trading_rules ---------------------------------------------


def test_extract_trading_rules_reads_price_lot_and_notional_filters() -> None:
    tick_size, lot_size, min_notional = extract_trading_rules(_symbol())
    assert tick_size == Decimal("0.01000000")
    assert lot_size == Decimal("0.00001000")
    assert min_notional == Decimal("10.00000000")
    assert isinstance(tick_size, Decimal)
    assert isinstance(lot_size, Decimal)
    assert isinstance(min_notional, Decimal)


def test_extract_trading_rules_accepts_the_notional_filter_type_alias() -> None:
    """Binance has used both `MIN_NOTIONAL` and `NOTIONAL` as the filter
    type name across API versions; both carry a `minNotional` field."""
    filters = [
        SymbolFilter(filter_type="PRICE_FILTER", tick_size=Decimal("0.01")),
        SymbolFilter(filter_type="LOT_SIZE", step_size=Decimal("0.001")),
        SymbolFilter(filter_type="NOTIONAL", min_notional=Decimal("5")),
    ]
    _, _, min_notional = extract_trading_rules(_symbol(filters=filters))
    assert min_notional == Decimal("5")


def test_extract_trading_rules_ignores_extra_unrelated_filters() -> None:
    filters = [*_VALID_FILTERS, SymbolFilter(filter_type="MARKET_LOT_SIZE")]
    tick_size, lot_size, min_notional = extract_trading_rules(_symbol(filters=filters))
    assert tick_size == Decimal("0.01000000")


@pytest.mark.parametrize(
    "filters",
    [
        [_VALID_FILTERS[1], _VALID_FILTERS[2]],  # missing PRICE_FILTER
        [_VALID_FILTERS[0], _VALID_FILTERS[2]],  # missing LOT_SIZE
        [_VALID_FILTERS[0], _VALID_FILTERS[1]],  # missing MIN_NOTIONAL
        [],
    ],
)
def test_extract_trading_rules_raises_when_a_required_filter_is_missing(
    filters: list[SymbolFilter],
) -> None:
    with pytest.raises(ReferenceDataError, match="BTCUSDT"):
        extract_trading_rules(_symbol(filters=filters))


# --- map_status ----------------------------------------------------------


def test_map_status_trading_maps_to_trading() -> None:
    assert map_status("TRADING") == "trading"


@pytest.mark.parametrize("binance_status", ["HALT", "BREAK", "AUCTION_MATCH", "END_OF_DAY", ""])
def test_map_status_everything_else_maps_to_halted_never_delisted(binance_status: str) -> None:
    assert map_status(binance_status) == "halted"


# --- build_instrument ------------------------------------------------------


def test_build_instrument_maps_every_field() -> None:
    instrument_id = uuid4()
    venue_id = uuid4()
    instrument = build_instrument(_symbol(), instrument_id=instrument_id, venue_id=venue_id)

    assert instrument.id == instrument_id
    assert instrument.venue_id == venue_id
    assert instrument.symbol == "BTCUSDT"
    assert instrument.base_currency == "BTC"
    assert instrument.quote_currency == "USDT"
    assert instrument.tick_size == Decimal("0.01000000")
    assert instrument.lot_size == Decimal("0.00001000")
    assert instrument.min_notional == Decimal("10.00000000")
    assert instrument.status == "trading"
    assert instrument.asset_class == "spot"
    assert isinstance(instrument.details, Spot)


def test_build_instrument_propagates_a_missing_filter_as_reference_data_error() -> None:
    with pytest.raises(ReferenceDataError):
        build_instrument(_symbol(filters=[]), instrument_id=uuid4(), venue_id=uuid4())


def test_build_instrument_is_frozen_and_decimal_only() -> None:
    instrument = build_instrument(_symbol(), instrument_id=uuid4(), venue_id=uuid4())
    assert not isinstance(instrument.tick_size, float)
    assert not isinstance(instrument.lot_size, float)
    assert not isinstance(instrument.min_notional, float)
