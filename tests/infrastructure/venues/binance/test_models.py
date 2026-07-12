"""Tests for infrastructure/venues/binance/models.py.

TASKS.md T-P1-01 acceptance criterion: "All typed response fields use
Decimal for price and quantity fields, never float." These tests parse
realistic Binance JSON (numeric fields arrive as strings) and assert the
resulting Python values are `Decimal`, never `float`.
"""

from __future__ import annotations

from decimal import Decimal

import pydantic
import pytest

from infrastructure.venues.binance.models import AccountResponse, ExchangeInfoResponse

_EXCHANGE_INFO_BODY = {
    "timezone": "UTC",
    "serverTime": 1735689600000,
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "baseAssetPrecision": 8,
            "quoteAssetPrecision": 8,
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.01000000",
                    "maxPrice": "1000000.00000000",
                    "tickSize": "0.01000000",
                },
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.00001000",
                    "maxQty": "9000.00000000",
                    "stepSize": "0.00001000",
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "minNotional": "10.00000000",
                },
                {
                    "filterType": "MARKET_LOT_SIZE",
                    "minQty": "0.00000000",
                    "maxQty": "3000.00000000",
                    "stepSize": "0.00000000",
                },
            ],
        }
    ],
}

_ACCOUNT_BODY = {
    "makerCommission": 10,
    "takerCommission": 10,
    "canTrade": True,
    "canWithdraw": False,
    "canDeposit": True,
    "updateTime": 1735689600000,
    "accountType": "SPOT",
    "balances": [
        {"asset": "BTC", "free": "1.50000000", "locked": "0.00000000"},
        {"asset": "USDT", "free": "10000.12345678", "locked": "500.00000000"},
    ],
}


def test_exchange_info_parses_price_and_quantity_fields_as_decimal() -> None:
    parsed = ExchangeInfoResponse.model_validate(_EXCHANGE_INFO_BODY)

    symbol = parsed.symbols[0]
    price_filter = next(f for f in symbol.filters if f.filter_type == "PRICE_FILTER")
    lot_size_filter = next(f for f in symbol.filters if f.filter_type == "LOT_SIZE")
    min_notional_filter = next(f for f in symbol.filters if f.filter_type == "MIN_NOTIONAL")

    assert price_filter.tick_size == Decimal("0.01000000")
    assert isinstance(price_filter.tick_size, Decimal)
    assert not isinstance(price_filter.tick_size, float)

    assert lot_size_filter.step_size == Decimal("0.00001000")
    assert isinstance(lot_size_filter.step_size, Decimal)

    assert min_notional_filter.min_notional == Decimal("10.00000000")
    assert isinstance(min_notional_filter.min_notional, Decimal)


def test_exchange_info_tolerates_filter_types_it_does_not_model_explicitly() -> None:
    parsed = ExchangeInfoResponse.model_validate(_EXCHANGE_INFO_BODY)
    filter_types = {f.filter_type for f in parsed.symbols[0].filters}
    assert "MARKET_LOT_SIZE" in filter_types


def test_exchange_info_top_level_fields() -> None:
    parsed = ExchangeInfoResponse.model_validate(_EXCHANGE_INFO_BODY)
    assert parsed.timezone == "UTC"
    assert parsed.server_time == 1735689600000
    assert parsed.symbols[0].symbol == "BTCUSDT"
    assert parsed.symbols[0].base_asset == "BTC"
    assert parsed.symbols[0].quote_asset == "USDT"


def test_account_response_parses_balances_as_decimal() -> None:
    parsed = AccountResponse.model_validate(_ACCOUNT_BODY)

    btc = next(b for b in parsed.balances if b.asset == "BTC")
    usdt = next(b for b in parsed.balances if b.asset == "USDT")

    assert btc.free == Decimal("1.50000000")
    assert isinstance(btc.free, Decimal)
    assert usdt.locked == Decimal("500.00000000")
    assert isinstance(usdt.locked, Decimal)


def test_account_response_boolean_and_integer_fields() -> None:
    parsed = AccountResponse.model_validate(_ACCOUNT_BODY)
    assert parsed.can_trade is True
    assert parsed.can_withdraw is False
    assert parsed.account_type == "SPOT"
    assert parsed.maker_commission == 10


def test_models_are_frozen() -> None:
    parsed = AccountResponse.model_validate(_ACCOUNT_BODY)
    with pytest.raises(pydantic.ValidationError):
        parsed.can_trade = False  # type: ignore[misc]
