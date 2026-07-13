"""Tests for infrastructure/backtest/market_data_view.py (TASKS.md T-P2-02).

Test data: 15 sequential 1-minute `BTC/USDT` candles, `open_time =
base + i minutes` for `i` in `0..14`. With `current_ts = base + 11m`,
the boundary worked out in the module's own docstring puts candles
`0..9` (10 bars) as revealed and candle `10` (open_time = base + 10m,
closing exactly at `current_ts`) as the first still-hidden one — this
is the exact scenario TASKS.md's own acceptance criteria describe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from domain.candle import Candle
from domain.ports import MarketDataView
from infrastructure.backtest.market_data_view import CursorMarketDataView

_INSTRUMENT_ID = uuid4()
_BASE = datetime(2026, 1, 1, tzinfo=UTC)
_STEP = timedelta(minutes=1)


def _candle(i: int) -> Candle:
    return Candle(
        instrument_id=_INSTRUMENT_ID,
        interval="1m",
        open_time=_BASE + i * _STEP,
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close=Decimal("100.50"),
        volume=Decimal("10"),
        is_closed=True,
    )


def _fifteen_candles() -> list[Candle]:
    return [_candle(i) for i in range(15)]


def _view_with_fifteen_candles() -> CursorMarketDataView:
    return CursorMarketDataView({("BTC/USDT", "1m"): _fifteen_candles()})


# --- acceptance criterion 1: bars() returns the 10 bars ending at T-1m -----


def test_bars_with_current_ts_t_returns_the_10_bars_ending_at_t_minus_1m() -> None:
    """TASKS.md T-P2-02 acceptance criterion, verbatim: "view.bars
    ("BTC/USDT", "1m", 10) with current_ts = T returns the 10 bars
    ending at T-1m (not T).\""""
    view = _view_with_fifteen_candles()
    t = _BASE + 11 * _STEP
    view.advance(t)

    result = view.bars("BTC/USDT", "1m", 10)

    assert len(result) == 10
    assert list(result) == _fifteen_candles()[0:10]
    last_bar = result[-1]
    assert last_bar.open_time == t - 2 * _STEP  # closes at T-1m
    assert last_bar.open_time + _STEP == t - _STEP  # close time == T-1m, not T


def test_current_ts_property_reflects_the_last_advance_to_value() -> None:
    view = _view_with_fifteen_candles()
    assert view.current_ts is None
    t = _BASE + 11 * _STEP
    view.advance(t)
    assert view.current_ts == t


# --- acceptance criterion 2: the current bar is never returned -------------


def test_a_strategy_calling_bars_never_sees_the_current_still_forming_bar() -> None:
    """TASKS.md T-P2-02 acceptance criterion, verbatim: "A strategy that
    attempts to use bars() to 'see' data from the current bar gets the
    bar before the cursor, never the current one.\""""
    view = _view_with_fifteen_candles()
    t = _BASE + 11 * _STEP
    view.advance(t)

    def toy_strategy(view: MarketDataView) -> Candle:
        # A strategy only ever gets data through bars() — no other path.
        return view.bars("BTC/USDT", "1m", 1)[0]

    latest_visible = toy_strategy(view)

    current_bar_open_time = t - _STEP  # the still-forming bar, closes at T
    assert latest_visible.open_time != current_bar_open_time
    assert latest_visible.open_time == t - 2 * _STEP  # the bar just before it


# --- acceptance criterion 3: clairvoyant strategy gets zero future signal --


def test_a_clairvoyant_strategy_requesting_far_more_bars_than_revealed_gets_no_future_data() -> (
    None
):
    """TASKS.md T-P2-02 acceptance criterion, verbatim: "A 'clairvoyant'
    test strategy that tries to peek at the next bar gets zero signal
    (cannot index past cursor).\""""
    view = _view_with_fifteen_candles()
    t = _BASE + 11 * _STEP
    view.advance(t)

    result = view.bars("BTC/USDT", "1m", 1000)  # absurdly oversized n

    assert len(result) == 10  # bounded by what's actually revealed, not n
    current_bar_open_time = t - _STEP
    future_bar_open_times = {c.open_time for c in _fifteen_candles()[10:]}
    assert all(c.open_time not in future_bar_open_times for c in result)
    assert all(c.open_time != current_bar_open_time for c in result)


def test_bars_before_any_advance_call_reveals_nothing() -> None:
    """The most extreme "clairvoyant" case: asking before the cursor has
    ever moved gets nothing at all, not an error and not future data."""
    view = _view_with_fifteen_candles()
    assert view.bars("BTC/USDT", "1m", 10) == ()


# --- acceptance criterion 4: structural — advancing further never leaks ----


def test_calling_bars_repeatedly_after_advancing_to_t_plus_1_never_reveals_data_after_t() -> (
    None
):
    """TASKS.md T-P2-02 acceptance criterion, verbatim: "The structural
    test: calling any view method after advancing the cursor to T+1
    never reveals data from after T.\""""
    view = _view_with_fifteen_candles()
    t = _BASE + 11 * _STEP
    view.advance(t)
    assert len(view.bars("BTC/USDT", "1m", 100)) == 10

    t_plus_1 = t + _STEP
    view.advance(t_plus_1)

    # Bar 10 (closes exactly at T) is now revealed — it closed *before* T+1.
    for n in (1, 5, 11, 1000):
        result = view.bars("BTC/USDT", "1m", n)
        assert len(result) == min(n, 11)
        assert all(c.open_time <= _BASE + 10 * _STEP for c in result)
        # Never bar 11 (open_time = T, the new still-forming bar) or later.
        assert all(c.open_time < t_plus_1 - _STEP for c in result)


def test_advancing_past_the_end_of_the_series_reveals_everything_and_stops() -> None:
    view = _view_with_fifteen_candles()
    far_future = _BASE + 1000 * _STEP
    view.advance(far_future)

    result = view.bars("BTC/USDT", "1m", 1000)
    assert list(result) == _fifteen_candles()


# --- structural / protocol conformance -------------------------------------


def test_cursor_market_data_view_satisfies_the_market_data_view_protocol() -> None:
    assert isinstance(_view_with_fifteen_candles(), MarketDataView)


def test_bars_for_an_unknown_symbol_or_timeframe_returns_empty_not_an_error() -> None:
    view = _view_with_fifteen_candles()
    view.advance(_BASE + 11 * _STEP)
    assert view.bars("ETH/USDT", "1m", 10) == ()
    assert view.bars("BTC/USDT", "1h", 10) == ()


def test_bars_with_zero_or_negative_n_returns_empty() -> None:
    view = _view_with_fifteen_candles()
    view.advance(_BASE + 11 * _STEP)
    assert view.bars("BTC/USDT", "1m", 0) == ()
    assert view.bars("BTC/USDT", "1m", -5) == ()


def test_multiple_series_advance_independently_without_cross_contamination() -> None:
    other_instrument = uuid4()
    eth_candles = [
        Candle(
            instrument_id=other_instrument,
            interval="1m",
            open_time=_BASE + i * _STEP,
            open=Decimal("2000"),
            high=Decimal("2010"),
            low=Decimal("1990"),
            close=Decimal("2005"),
            volume=Decimal("5"),
            is_closed=True,
        )
        for i in range(3)  # only 3 bars — far fewer than BTC's 15
    ]
    view = CursorMarketDataView(
        {
            ("BTC/USDT", "1m"): _fifteen_candles(),
            ("ETH/USDT", "1m"): eth_candles,
        }
    )

    t = _BASE + 11 * _STEP
    view.advance(t)

    assert len(view.bars("BTC/USDT", "1m", 100)) == 10
    # ETH only has 3 bars total; advancing far past them reveals all 3, not more.
    assert list(view.bars("ETH/USDT", "1m", 100)) == eth_candles
