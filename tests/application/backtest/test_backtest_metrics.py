"""Tests for application/backtest/metrics.py (TASKS.md T-P2-11)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import numpy as np
import pytest

from application.backtest.metrics import EquityPoint, compute_tearsheet
from domain.fill import Fill
from domain.money import CurrencyMismatch, Money
from domain.order import OrderSide

_BASE_TS = datetime(2025, 1, 1, tzinfo=UTC)


def _fill(
    *,
    side: OrderSide,
    qty: Decimal,
    price: Decimal,
    fee: Decimal,
    day: int,
    currency: str = "USDT",
) -> Fill:
    return Fill(
        id=uuid4(),
        order_id=uuid4(),
        venue_fill_id=str(uuid4()),
        side=side,
        qty=qty,
        price=price,
        fee=Money(fee, currency),
        ts=_BASE_TS + timedelta(days=day),
        is_maker=False,
    )


def _buy(day: int, price: str = "100", qty: str = "1", fee: str = "1") -> Fill:
    return _fill(
        side=OrderSide.BUY, qty=Decimal(qty), price=Decimal(price), fee=Decimal(fee), day=day
    )


def _sell(day: int, price: str = "100", qty: str = "1", fee: str = "1") -> Fill:
    return _fill(
        side=OrderSide.SELL, qty=Decimal(qty), price=Decimal(price), fee=Decimal(fee), day=day
    )


# --- acceptance criterion 1: buy-and-hold BTC for 1 year -----------------------------


def test_buy_and_hold_btc_1_year_matches_hand_computed_sharpe_cagr_max_dd_within_001_pct() -> (
    None
):
    """TASKS.md T-P2-11 acceptance criterion, verbatim: "A buy-and-hold
    strategy on BTC for 1 year produces metrics matching hand-computed
    references (Sharpe, CAGR, max DD) within 0.01%.\" The independent
    reference is computed directly with numpy/float — a separate
    implementation from `metrics.py`'s own pure-Decimal formulas — so
    this is a genuine cross-check, not a tautology."""
    rng = np.random.default_rng(42)
    n_days = 365
    daily_returns = rng.normal(0.0007, 0.02, n_days)
    prices = 50_000.0 * np.cumprod(1 + daily_returns)
    prices = np.insert(prices, 0, 50_000.0)

    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=i), equity=Decimal(repr(float(p))))
        for i, p in enumerate(prices)
    ]
    fill = _fill(
        side=OrderSide.BUY,
        qty=Decimal("1"),
        price=Decimal(repr(float(prices[0]))),
        fee=Decimal("10"),
        day=0,
    )

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=[fill], periods_per_year=365)

    returns = np.diff(prices) / prices[:-1]
    sharpe_ref = returns.mean() / returns.std(ddof=1) * np.sqrt(365)
    years = (equity_curve[-1].ts - equity_curve[0].ts).days / 365.25
    cagr_ref = (float(prices[-1]) / float(prices[0])) ** (1 / years) - 1
    running_max = np.maximum.accumulate(prices)
    max_dd_ref = ((running_max - prices) / running_max).max()

    assert sheet["sharpe"] is not None
    assert sheet["cagr"] is not None
    assert float(sheet["sharpe"]) == pytest.approx(sharpe_ref, rel=1e-4)
    assert float(sheet["cagr"]) == pytest.approx(cagr_ref, rel=1e-4)
    assert float(sheet["max_drawdown"]) == pytest.approx(max_dd_ref, rel=1e-4)


def test_buy_and_hold_has_no_closed_trades_so_trade_stats_are_none() -> None:
    """A pure buy-and-hold position, never closed within the analyzed
    window, has no win rate/profit factor/avg win-loss/expectancy —
    these are genuinely undefined, not zero."""
    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=i), equity=Decimal("10000") + i * Decimal("5"))
        for i in range(10)
    ]

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=[_buy(0, price="10000")])

    assert sheet["win_rate"] is None
    assert sheet["profit_factor"] is None
    assert sheet["avg_win"] is None
    assert sheet["avg_loss"] is None
    assert sheet["expectancy"] is None
    assert sheet["time_in_market"] == "1"


# --- acceptance criterion 2: all-losing trades ---------------------------------------


def test_a_strategy_with_all_losing_trades_produces_profit_factor_below_1_and_negative_sharpe() -> (
    None
):
    """TASKS.md T-P2-11 acceptance criterion, verbatim: "A strategy with
    all losing trades produces a profit factor < 1 and negative
    Sharpe.\""""
    equity_values = [
        Decimal("10000"), Decimal("9800"), Decimal("9600"),
        Decimal("9350"), Decimal("9100"), Decimal("8900"),
    ]
    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=i), equity=v)
        for i, v in enumerate(equity_values)
    ]
    round_trip_prices = ["100", "95", "95", "90", "90", "85"]
    fills: list[Fill] = []
    for i in range(0, len(round_trip_prices), 2):
        fills.append(_buy(i, price=round_trip_prices[i], qty="10"))
        fills.append(_sell(i + 1, price=round_trip_prices[i + 1], qty="10"))

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=fills, periods_per_year=365)

    assert sheet["profit_factor"] is not None
    assert sheet["sharpe"] is not None
    assert Decimal(sheet["profit_factor"]) < 1
    assert Decimal(sheet["sharpe"]) < 0
    assert sheet["win_rate"] == "0"
    assert sheet["avg_win"] is None


def test_profit_factor_is_none_when_there_are_zero_losing_trades() -> None:
    """The reverse edge case: no losses at all means profit factor
    (gross profit / gross loss) has nothing to divide by."""
    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=i), equity=Decimal("10000") + i * Decimal("100"))
        for i in range(4)
    ]
    fills = [_buy(0, price="100", qty="10"), _sell(1, price="110", qty="10")]

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=fills)

    assert sheet["profit_factor"] is None
    assert sheet["avg_loss"] is None


# --- acceptance criterion 3: fees-as-%-of-gross-P&L always computable ---------------


def test_fees_pct_of_gross_is_computable_for_a_profitable_run() -> None:
    """TASKS.md T-P2-11 acceptance criterion, verbatim: "Fees-as-%-of-
    gross-P&L is always computable (no division-by-zero guard returning
    0 when gross P&L = 0 — return `None` instead).\""""
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("10000")),
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("10100")),
    ]

    sheet = compute_tearsheet(
        equity_curve=equity_curve, fills=[_buy(0, price="10000", fee="5")]
    )

    assert sheet["fees_pct_of_gross"] is not None
    assert Decimal(sheet["fees_pct_of_gross"]) > 0


def test_fees_pct_of_gross_is_none_not_zero_when_gross_pnl_is_zero() -> None:
    """Gross P&L = net equity change + total fees. A net change that
    exactly offsets total fees makes gross P&L exactly zero."""
    fee = Decimal("5")
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("10000")),
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("10000") - fee),
    ]

    sheet = compute_tearsheet(
        equity_curve=equity_curve, fills=[_buy(0, price="10000", fee=str(fee))]
    )

    assert sheet["fees_pct_of_gross"] is None


# --- acceptance criterion 4: tearsheet serializes to JSON ---------------------------


def test_tearsheet_serializes_to_json_without_errors() -> None:
    """TASKS.md T-P2-11 acceptance criterion, verbatim: "Tearsheet
    serializes to JSON without errors.\""""
    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=i), equity=Decimal("10000") + i * Decimal("5"))
        for i in range(10)
    ]

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=[_buy(0, price="10000")])

    serialized = json.dumps(sheet)
    assert json.loads(serialized) == sheet


def test_tearsheet_with_every_field_populated_serializes_to_json() -> None:
    """A run with both up and down days (so drawdown/downside deviation
    are nonzero) and both a winning and a losing closed trade (so every
    trade stat is non-None too) populates every field, and still
    serializes cleanly."""
    equity_values = [
        Decimal("10000"), Decimal("10200"), Decimal("10100"),
        Decimal("10000"), Decimal("9950"), Decimal("10050"),
    ]
    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=i), equity=v)
        for i, v in enumerate(equity_values)
    ]
    fills = [
        _buy(0, price="100", qty="10"),
        _sell(1, price="110", qty="10"),  # winning trade
        _buy(2, price="100", qty="10"),
        _sell(3, price="95", qty="10"),  # losing trade
    ]

    sheet = compute_tearsheet(
        equity_curve=equity_curve, fills=fills, total_slippage=Money(Decimal("2"), "USDT")
    )

    assert all(value is not None for value in sheet.values())
    json.dumps(sheet)


# --- structural sanity ----------------------------------------------------------------


def test_compute_tearsheet_rejects_fewer_than_2_equity_points() -> None:
    single_point = [EquityPoint(ts=_BASE_TS, equity=Decimal("100"))]
    with pytest.raises(ValueError, match="at least 2 points"):
        compute_tearsheet(equity_curve=single_point, fills=[_buy(0)])


def test_compute_tearsheet_rejects_an_unsorted_equity_curve() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("100")),
        EquityPoint(ts=_BASE_TS, equity=Decimal("100")),
    ]
    with pytest.raises(ValueError, match="sorted"):
        compute_tearsheet(equity_curve=equity_curve, fills=[_buy(0)])


def test_compute_tearsheet_rejects_empty_fills() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("100")),
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("110")),
    ]
    with pytest.raises(ValueError, match="fills must be non-empty"):
        compute_tearsheet(equity_curve=equity_curve, fills=[])


def test_compute_tearsheet_rejects_non_positive_starting_equity() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("0")),
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("110")),
    ]
    with pytest.raises(ValueError, match="positive equity"):
        compute_tearsheet(equity_curve=equity_curve, fills=[_buy(0)])


def test_compute_tearsheet_rejects_a_slippage_currency_mismatch() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("100")),
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("110")),
    ]
    with pytest.raises(CurrencyMismatch):
        compute_tearsheet(
            equity_curve=equity_curve,
            fills=[_buy(0)],
            total_slippage=Money(Decimal("1"), "EUR"),
        )


def test_total_fees_reuses_total_fees_paid_and_sums_every_fill() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("10000")),
        EquityPoint(ts=_BASE_TS + timedelta(days=2), equity=Decimal("10050")),
    ]
    fills = [
        _buy(0, price="100", qty="10", fee="1"),
        _sell(1, price="105", qty="10", fee="1.5"),
    ]

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=fills)

    assert sheet["total_fees"] == "2.5"


def test_slippage_is_none_when_not_supplied() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("100")),
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("110")),
    ]

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=[_buy(0)])

    assert sheet["slippage"] is None


def test_slippage_is_reported_verbatim_when_supplied() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("100")),
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("110")),
    ]

    sheet = compute_tearsheet(
        equity_curve=equity_curve,
        fills=[_buy(0)],
        total_slippage=Money(Decimal("3.25"), "USDT"),
    )

    assert sheet["slippage"] == "3.25"


def test_max_drawdown_is_zero_for_a_monotonically_increasing_equity_curve() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=i), equity=Decimal("100") + i)
        for i in range(5)
    ]

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=[_buy(0)])

    assert sheet["max_drawdown"] == "0"
    assert sheet["drawdown_duration_days"] == "0"


def test_time_in_market_reflects_the_flat_interval_between_a_closed_trade_and_the_next_open() -> (
    None
):
    equity_curve = [
        EquityPoint(ts=_BASE_TS + timedelta(days=i), equity=Decimal("10000"))
        for i in range(11)
    ]
    fills = [
        _buy(0, price="100", qty="10", fee="0"),
        _sell(5, price="100", qty="10", fee="0"),
    ]

    sheet = compute_tearsheet(equity_curve=equity_curve, fills=fills)

    # In market days 0-5 (5 days) out of a 10-day total span = 0.5.
    assert sheet["time_in_market"] == "0.5"


def test_compute_tearsheet_rejects_a_float_risk_free_rate() -> None:
    equity_curve = [
        EquityPoint(ts=_BASE_TS, equity=Decimal("100")),
        EquityPoint(ts=_BASE_TS + timedelta(days=1), equity=Decimal("110")),
    ]
    with pytest.raises(TypeError, match="Decimal"):
        compute_tearsheet(
            equity_curve=equity_curve,
            fills=[_buy(0)],
            risk_free_rate=0.01,  # type: ignore[arg-type]
        )


def test_equity_point_rejects_a_float_equity() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        EquityPoint(ts=_BASE_TS, equity=100.0)  # type: ignore[arg-type]
