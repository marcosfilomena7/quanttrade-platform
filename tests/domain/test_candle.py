"""Tests for the Candle domain value (domain/candle.py)."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from domain.candle import Candle

TS = datetime(2026, 1, 1, tzinfo=UTC)


def _candle(is_closed: bool = True) -> Candle:
    return Candle(
        instrument_id=uuid4(),
        interval="1m",
        open_time=TS,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("95"),
        close=Decimal("105"),
        volume=Decimal("1000"),
        is_closed=is_closed,
    )


def test_construction_holds_all_fields() -> None:
    candle = _candle()
    assert candle.interval == "1m"
    assert candle.open_time == TS
    assert candle.open == Decimal("100")
    assert candle.high == Decimal("110")
    assert candle.low == Decimal("95")
    assert candle.close == Decimal("105")
    assert candle.volume == Decimal("1000")
    assert candle.is_closed is True


def test_is_closed_distinguishes_partial_from_final_bars() -> None:
    assert _candle(is_closed=False).is_closed is False
    assert _candle(is_closed=True).is_closed is True


def test_candle_is_immutable() -> None:
    candle = _candle()
    with pytest.raises(FrozenInstanceError):
        candle.close = Decimal("999")  # type: ignore[misc]


@pytest.mark.parametrize("field_name", ["open", "high", "low", "close", "volume"])
def test_ohlcv_fields_reject_float(field_name: str) -> None:
    kwargs = {
        "instrument_id": uuid4(),
        "interval": "1m",
        "open_time": TS,
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("95"),
        "close": Decimal("105"),
        "volume": Decimal("1000"),
        "is_closed": True,
    }
    kwargs[field_name] = 1.5
    with pytest.raises(TypeError):
        Candle(**kwargs)  # type: ignore[arg-type]
