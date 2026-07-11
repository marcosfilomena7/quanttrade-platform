"""Tests for the Instrument domain model (domain/instrument.py)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain.instrument import (
    DatedFuture,
    Instrument,
    PerpetualSwap,
    Spot,
    describe_asset_class,
)


def _make_instrument(
    details: Spot | PerpetualSwap | DatedFuture, status: str = "trading"
) -> Instrument:
    return Instrument(
        id=uuid4(),
        venue_id=uuid4(),
        symbol="BTCUSDT",
        base_currency="BTC",
        quote_currency="USDT",
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.001"),
        min_notional=Decimal("10"),
        status=status,  # type: ignore[arg-type]
        details=details,
    )


# --- Construction of each variant --------------------------------------------


def test_spot_has_only_the_discriminator() -> None:
    spot = Spot()
    assert spot.asset_class == "spot"


def test_perpetual_swap_holds_its_declared_fields() -> None:
    swap = PerpetualSwap(funding_interval=timedelta(hours=8), mark_price_type="index_price")
    assert swap.asset_class == "perpetual_swap"
    assert swap.funding_interval == timedelta(hours=8)
    assert swap.mark_price_type == "index_price"


def test_dated_future_holds_its_declared_fields() -> None:
    future = DatedFuture(
        expiry=datetime(2026, 12, 31, tzinfo=UTC),
        multiplier=Decimal("100"),
        roll_days=5,
    )
    assert future.asset_class == "dated_future"
    assert future.expiry == datetime(2026, 12, 31, tzinfo=UTC)
    assert future.multiplier == Decimal("100")
    assert future.roll_days == 5


# --- Instrument core + asset_class property ----------------------------------


def test_instrument_asset_class_property_mirrors_details_for_spot() -> None:
    instrument = _make_instrument(Spot())
    assert instrument.asset_class == "spot"


def test_instrument_asset_class_property_mirrors_details_for_perpetual_swap() -> None:
    instrument = _make_instrument(
        PerpetualSwap(funding_interval=timedelta(hours=8), mark_price_type="last_price")
    )
    assert instrument.asset_class == "perpetual_swap"


def test_instrument_asset_class_property_mirrors_details_for_dated_future() -> None:
    instrument = _make_instrument(
        DatedFuture(
            expiry=datetime(2026, 3, 1, tzinfo=UTC),
            multiplier=Decimal("5"),
            roll_days=3,
        )
    )
    assert instrument.asset_class == "dated_future"


# --- Round-trip serialization (Pydantic v2) — all three variants ------------


def test_spot_instrument_round_trips_through_json() -> None:
    original = _make_instrument(Spot())
    restored = Instrument.model_validate_json(original.model_dump_json())
    assert restored == original
    assert isinstance(restored.details, Spot)
    assert restored.asset_class == "spot"


def test_perpetual_swap_instrument_round_trips_through_json() -> None:
    original = _make_instrument(
        PerpetualSwap(funding_interval=timedelta(hours=8), mark_price_type="fair_price")
    )
    restored = Instrument.model_validate_json(original.model_dump_json())
    assert restored == original
    assert isinstance(restored.details, PerpetualSwap)
    assert restored.details.funding_interval == timedelta(hours=8)
    assert restored.details.mark_price_type == "fair_price"
    assert restored.asset_class == "perpetual_swap"


def test_dated_future_instrument_round_trips_through_json() -> None:
    original = _make_instrument(
        DatedFuture(
            expiry=datetime(2026, 9, 26, tzinfo=UTC),
            multiplier=Decimal("100"),
            roll_days=7,
        )
    )
    restored = Instrument.model_validate_json(original.model_dump_json())
    assert restored == original
    assert isinstance(restored.details, DatedFuture)
    assert restored.details.expiry == datetime(2026, 9, 26, tzinfo=UTC)
    assert restored.details.multiplier == Decimal("100")
    assert restored.details.roll_days == 7
    assert restored.asset_class == "dated_future"


def test_round_trip_through_plain_dict_also_preserves_discriminator() -> None:
    original = _make_instrument(
        PerpetualSwap(funding_interval=timedelta(hours=1), mark_price_type="index_price")
    )
    restored = Instrument.model_validate(original.model_dump(mode="json"))
    assert restored == original
    assert isinstance(restored.details, PerpetualSwap)


def test_round_trip_preserves_every_core_field() -> None:
    instrument_id = uuid4()
    venue_id = uuid4()
    original = Instrument(
        id=instrument_id,
        venue_id=venue_id,
        symbol="ETHUSDT",
        base_currency="ETH",
        quote_currency="USDT",
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        min_notional=Decimal("5"),
        status="halted",
        details=Spot(),
    )
    restored = Instrument.model_validate_json(original.model_dump_json())
    assert restored.id == instrument_id
    assert restored.venue_id == venue_id
    assert restored.symbol == "ETHUSDT"
    assert restored.base_currency == "ETH"
    assert restored.quote_currency == "USDT"
    assert restored.tick_size == Decimal("0.01")
    assert restored.lot_size == Decimal("0.0001")
    assert restored.min_notional == Decimal("5")
    assert restored.status == "halted"


# --- Validation ---------------------------------------------------------


@pytest.mark.parametrize("field", ["tick_size", "lot_size", "min_notional"])
def test_non_positive_numeric_fields_are_rejected(field: str) -> None:
    kwargs = {
        "id": uuid4(),
        "venue_id": uuid4(),
        "symbol": "BTCUSDT",
        "base_currency": "BTC",
        "quote_currency": "USDT",
        "tick_size": Decimal("0.1"),
        "lot_size": Decimal("0.001"),
        "min_notional": Decimal("10"),
        "status": "trading",
        "details": Spot(),
    }
    kwargs[field] = Decimal("0")
    with pytest.raises(ValidationError):
        Instrument(**kwargs)  # type: ignore[arg-type]


def test_instrument_is_frozen() -> None:
    instrument = _make_instrument(Spot())
    with pytest.raises(ValidationError):
        instrument.symbol = "OTHER"  # type: ignore[misc]


def test_variant_is_frozen() -> None:
    swap = PerpetualSwap(funding_interval=timedelta(hours=8), mark_price_type="index_price")
    with pytest.raises(ValidationError):
        swap.mark_price_type = "last_price"  # type: ignore[misc]


# --- Exhaustive matching: describe_asset_class covers all three variants ----


def test_describe_asset_class_handles_spot() -> None:
    assert describe_asset_class(Spot()) == "spot"


def test_describe_asset_class_handles_perpetual_swap() -> None:
    result = describe_asset_class(
        PerpetualSwap(funding_interval=timedelta(hours=8), mark_price_type="index_price")
    )
    assert "perpetual swap" in result
    assert "index_price" in result


def test_describe_asset_class_handles_dated_future() -> None:
    result = describe_asset_class(
        DatedFuture(
            expiry=datetime(2026, 12, 31, tzinfo=UTC),
            multiplier=Decimal("100"),
            roll_days=5,
        )
    )
    assert "dated future" in result
    assert "100" in result
    assert "5d" in result
