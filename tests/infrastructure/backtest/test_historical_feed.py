"""Pure unit tests for infrastructure/backtest/historical_feed.py
(TASKS.md T-P2-03) — no database needed.

The `load_candle_series_from_dataset_version` Postgres loader (AC3:
"loading a DatasetVersion... produces events deterministically") lives
in test_historical_feed_integration.py, gated on Docker per this repo's
established convention.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from domain.candle import Candle
from domain.ports import MarketDataFeed
from infrastructure.backtest.historical_feed import FeedExhausted, HistoricalFeed

_INSTRUMENT_ID = uuid4()


def _candle(*, open_time: datetime, interval: str) -> Candle:
    return Candle(
        instrument_id=_INSTRUMENT_ID,
        interval=interval,
        open_time=open_time,
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close=Decimal("100.50"),
        volume=Decimal("10"),
        is_closed=True,
    )


async def _drain(feed: HistoricalFeed) -> list[Candle]:
    events: list[Candle] = []
    while True:
        try:
            events.append(await feed.next_event())
        except FeedExhausted:
            return events


# --- acceptance criterion 1: two symbols interleaved by timestamp ----------


def test_events_from_two_symbols_interleaved_by_timestamp_are_emitted_in_chronological_order() -> (
    None
):
    """TASKS.md T-P2-03 acceptance criterion, verbatim: "Events from two
    symbols interleaved by timestamp are emitted in correct chronological
    order.\""""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    btc = [_candle(open_time=base + i * timedelta(minutes=2), interval="1m") for i in range(3)]
    eth = [
        _candle(open_time=base + timedelta(minutes=1) + i * timedelta(minutes=2), interval="1m")
        for i in range(3)
    ]
    feed = HistoricalFeed({("BTC/USDT", "1m"): btc, ("ETH/USDT", "1m"): eth})

    events = asyncio.run(_drain(feed))

    close_times = [c.open_time + timedelta(minutes=1) for c in events]
    assert close_times == sorted(close_times)  # strictly non-decreasing chronological order
    assert len(events) == 6
    # Interleaved: BTC(0:00) ETH(0:01) BTC(0:02) ETH(0:03) BTC(0:04) ETH(0:05)
    expected_open_times = [
        base,
        base + timedelta(minutes=1),
        base + timedelta(minutes=2),
        base + timedelta(minutes=3),
        base + timedelta(minutes=4),
        base + timedelta(minutes=5),
    ]
    assert [e.open_time for e in events] == expected_open_times


# --- acceptance criterion 2: 1h bar never before all 60 constituent 1m bars -


def test_1h_bar_is_never_emitted_before_all_60_constituent_1m_bars() -> None:
    """TASKS.md T-P2-03 acceptance criterion, verbatim: "A 1h
    CandleClosed event is never emitted before all 60 constituent 1m
    bars.\""""
    hour_start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    one_minute_bars = [
        _candle(open_time=hour_start + i * timedelta(minutes=1), interval="1m") for i in range(60)
    ]
    one_hour_bar = [_candle(open_time=hour_start, interval="1h")]

    feed = HistoricalFeed(
        {("BTC/USDT", "1m"): one_minute_bars, ("BTC/USDT", "1h"): one_hour_bar}
    )

    events_with_keys: list[tuple[str, Candle]] = []

    async def make_handler(timeframe: str) -> None:
        async def handler(candle: Candle) -> None:
            events_with_keys.append((timeframe, candle))

        await feed.subscribe("BTC/USDT", timeframe, handler)

    asyncio.run(make_handler("1m"))
    asyncio.run(make_handler("1h"))
    asyncio.run(_drain(feed))

    assert len(events_with_keys) == 61
    timeframes_in_order = [tf for tf, _ in events_with_keys]
    assert timeframes_in_order.count("1m") == 60
    assert timeframes_in_order.count("1h") == 1
    assert timeframes_in_order[-1] == "1h"  # the 1h bar is strictly last
    assert timeframes_in_order.index("1h") == 60  # after all 60 "1m" entries


def test_1h_bar_sharing_a_close_time_with_a_different_symbols_1m_bar_still_orders_correctly() -> (
    None
):
    """The tie-break is by interval duration, independent of symbol —
    confirms the ordering guarantee isn't accidentally symbol-scoped."""
    hour_start = datetime(2026, 1, 1, tzinfo=UTC)
    btc_1m = [
        _candle(open_time=hour_start + i * timedelta(minutes=1), interval="1m")
        for i in range(60)
    ]
    eth_1h = [_candle(open_time=hour_start, interval="1h")]

    feed = HistoricalFeed({("BTC/USDT", "1m"): btc_1m, ("ETH/USDT", "1h"): eth_1h})
    events = asyncio.run(_drain(feed))

    assert len(events) == 61
    assert events[-1].interval == "1h"  # 1h (ETH) still lands after all 60 1m (BTC)


# --- acceptance criterion 3 lives in test_historical_feed_integration.py ---


# --- acceptance criterion 4: exhaustion raises, never silently stops -------


def test_exhausting_the_feed_raises_feed_exhausted_rather_than_silently_stopping() -> None:
    """TASKS.md T-P2-03 acceptance criterion, verbatim: "Exhausting the
    feed raises FeedExhausted rather than silently stopping.\""""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [_candle(open_time=base + i * timedelta(minutes=1), interval="1m") for i in range(2)]
    feed = HistoricalFeed({("BTC/USDT", "1m"): candles})

    async def run() -> None:
        await feed.next_event()
        await feed.next_event()
        with pytest.raises(FeedExhausted):
            await feed.next_event()

    asyncio.run(run())


def test_an_empty_feed_raises_feed_exhausted_on_the_first_call() -> None:
    feed = HistoricalFeed({})

    async def run() -> None:
        with pytest.raises(FeedExhausted):
            await feed.next_event()

    asyncio.run(run())


def test_calling_next_event_repeatedly_after_exhaustion_keeps_raising() -> None:
    single_candle = _candle(open_time=datetime(2026, 1, 1, tzinfo=UTC), interval="1m")
    feed = HistoricalFeed({("BTC/USDT", "1m"): [single_candle]})

    async def run() -> None:
        await feed.next_event()
        with pytest.raises(FeedExhausted):
            await feed.next_event()
        with pytest.raises(FeedExhausted):
            await feed.next_event()

    asyncio.run(run())


# --- structural / protocol conformance -------------------------------------


def test_historical_feed_satisfies_the_market_data_feed_protocol() -> None:
    assert isinstance(HistoricalFeed({}), MarketDataFeed)


def test_subscribe_registers_a_handler_that_is_called_with_the_candle() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candle = _candle(open_time=base, interval="1m")
    feed = HistoricalFeed({("BTC/USDT", "1m"): [candle]})
    received: list[Candle] = []

    async def handler(c: Candle) -> None:
        received.append(c)

    async def run() -> None:
        await feed.subscribe("BTC/USDT", "1m", handler)
        await feed.next_event()

    asyncio.run(run())
    assert received == [candle]


def test_unsubscribe_stops_the_handler_from_being_called_but_next_event_still_delivers() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candle = _candle(open_time=base, interval="1m")
    feed = HistoricalFeed({("BTC/USDT", "1m"): [candle]})
    received: list[Candle] = []

    async def handler(c: Candle) -> None:
        received.append(c)

    async def run() -> Candle:
        await feed.subscribe("BTC/USDT", "1m", handler)
        await feed.unsubscribe("BTC/USDT", "1m")
        return await feed.next_event()

    result = asyncio.run(run())
    assert received == []  # handler was removed before delivery
    assert result == candle  # next_event() still returns the candle itself


def test_next_event_works_with_no_subscription_at_all() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candle = _candle(open_time=base, interval="1m")
    feed = HistoricalFeed({("BTC/USDT", "1m"): [candle]})

    async def run() -> Candle:
        return await feed.next_event()

    assert asyncio.run(run()) == candle
