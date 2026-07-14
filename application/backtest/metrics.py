"""Backtest metrics and tearsheet (TASKS.md T-P2-11).

"Implement the metrics computation module in `application/backtest
/metrics.py`: Total return, CAGR, max drawdown, drawdown duration,
Sharpe ratio, Sortino ratio, Calmar ratio, Omega ratio, win rate,
profit factor, avg win/loss, expectancy, time in market, total fees,
slippage, fees-as-%-of-gross-P&L. Produce a tearsheet as a structured
dict that serializes to JSON."

Design decisions, and why:

- **Placed exactly at `application/backtest/metrics.py`**, per the
  task's own literal, explicit path — unlike T-P2-05/06 (which named no
  target path and left `domain/` vs `infrastructure/` for this session
  to resolve), there is nothing to infer here.
- **A pure, self-contained calculation module — not a consumer of
  `BacktestResult` (T-P2-04), `BacktestTrade`, or any DB-persisted
  entity.** T-P2-11's own dependency list is "T-P2-06, T-P0-06" only
  (the fill/slippage model and the `Fill`/`Position` domain types) —
  not T-P2-04's event loop, and not T-P2-12's `backtest_run`/
  `backtest_metrics` tables (a separate, *later* task that explicitly
  depends on *this one*, and whose own job is "log every run" — that
  is persistence, not computation). This module accepts exactly two
  raw ingredients any backtest run already has: an equity curve and a
  sequence of `Fill`s — mirroring the same "operates on whatever the
  caller already has" resolution already used for T-P2-05/06/08/09.
- **`EquityPoint` is a small, local dataclass, not a new `domain/`
  type.** DATABASE.md's own `EquitySnapshot` (#18, "Portfolio" group)
  is a different, larger, not-yet-built entity (`account_id`, `cash`,
  `positions_value`, ...) belonging to a later phase's live-portfolio
  tracking, not a listed dependency here. `EquityPoint` is the minimal
  `(ts, equity)` shape this module's own calculations need — matching
  T-P2-04's own precedent of defining small, task-scoped types locally
  in `application/backtest/` rather than promoting them to `domain/`.
- **Every ratio/return calculation is pure `Decimal` arithmetic — no
  `float` anywhere.** ARCHITECTURE.md §3.5's float ban is enforced by
  `scripts/check_no_float.py` on `domain/` *and* `application/`
  (`pyproject.toml`), so — unlike T-P2-08/09's `infrastructure/`
  indicator library, which is explicitly exempt and uses `numpy`/
  `polars` — this module has no float-conversion boundary available at
  all. `Decimal` supports fractional exponents (`**`) and `.sqrt()`
  natively (verified: `Decimal('1.2') ** (Decimal(1)/Decimal('1.5'))`
  and `Decimal('0.0004').sqrt()` both compute correctly, to the
  context's 28-significant-digit precision), which is what CAGR and
  every stdev-based ratio (Sharpe/Sortino/Omega) need. Elapsed time is
  converted to a `Decimal` day count using only `timedelta.days`/
  `.seconds` (both plain `int`) — never `.total_seconds()` (a `float`).
- **"Produce a tearsheet as a structured dict"** is read completely
  literally: the return type is a `TypedDict` (for `mypy --strict`),
  which *is* a plain `dict` at runtime. **"Serializes to JSON without
  errors" (AC4)** is satisfied by converting every `Decimal` value to
  `str` *inside* the dict itself, so a plain `json.dumps(tearsheet)` —
  with no custom encoder — works out of the box: JSON has no native
  arbitrary-precision decimal type, and `str(Decimal(...))` preserves
  the exact value without the binary-float rounding a raw `float`
  would introduce. `None` values pass through unchanged (JSON `null`).
  All monetary fields (`total_fees`, `avg_win`, `avg_loss`, `slippage`)
  share one top-level `currency` field rather than repeating
  `{"amount": ..., "currency": ...}` four times, since every monetary
  figure in one tearsheet is necessarily denominated in the same
  currency (the shared `fills[0].fee.currency`).
- **"Fees-as-%-of-gross-P&L is always computable... return `None`
  instead" (AC3) reuses T-P2-05's own
  `fees_as_percentage_of_gross_pnl` unmodified — its `ZeroDivisionError`
  on zero `gross_pnl` is caught here, at the call site, and converted
  to `None`.** T-P2-05's function is already committed, working code
  whose own test suite asserts it *raises* on zero `gross_pnl`
  (`test_fees_as_percentage_of_gross_pnl_rejects_zero_gross_pnl`);
  changing its contract to return `None` instead would break that
  prior task's own behavior. Catching the exception here — rather than
  modifying T-P2-05 — satisfies T-P2-11's own distinct requirement
  without touching a previously-completed task's contract.
- **"Gross P&L" (for that same calculation) is reconstructed as `(the
  equity curve's own net change) + total fees paid`, not tracked as a
  separate per-trade "gross" figure.** ARCHITECTURE.md's own words
  frame gross P&L as the P&L *before* the fee "toll" is paid ("gross
  P&L that is 95% consumed by fees"); since every fee is deducted from
  equity as it's paid (matching `Position`'s own "every fill's fee is
  booked to `realized_pnl` immediately" convention), adding total fees
  back to the equity curve's net change recovers exactly that
  pre-fee figure, with no separate gross-P&L bookkeeping to invent.
- **Per-trade P&L (needed for win rate, profit factor, avg win/loss,
  expectancy) is derived by replaying `fills` through `Position
  .apply_fill` (T-P0-06) — reused exactly as committed, not
  reimplemented.** No upstream task builds a "trade blotter"
  (DATABASE.md's `BacktestTrade` is a separate, not-yet-built entity,
  and not a listed dependency); replaying fills through the existing
  `Position` accounting model is the direct, literal way to derive
  "one closed trade's realized P&L" without duplicating any of
  `Position`'s own weighted-average-cost/realization math. Since
  `Position.apply_fill` doesn't itself expose "was this fill a
  reduction/close" as part of its return value, that one boolean
  (`old_qty` and the incoming fill's sign disagree) is recomputed
  externally from data this module already has (the running position's
  `qty` *before* applying the fill, and the fill's own `signed_qty`) —
  the smallest possible amount of "duplication," and not a
  reimplementation of `Position`'s actual P&L arithmetic, which is
  delegated to `Position.apply_fill` in full. The scratch `Position`
  used for this replay is constructed with throwaway
  `instrument_id`/`strategy_instance_id` (`uuid4()`) — its accounting
  math never depends on either value, and no persisted `Position` is
  ever created or returned.
- **"Time in market"** is derived from that same fill replay: the
  fraction of the equity curve's own total elapsed time during which
  the replayed position's `qty` was non-zero (including, for a
  buy-and-hold position still open at the series' end, the interval
  from the last fill through the equity curve's final timestamp).
- **"Slippage" cannot be derived from `Fill` alone.** T-P0-06's `Fill`
  fields (`id, order_id, venue_fill_id, side, qty, price, fee, ts,
  is_maker`) do not carry a slippage figure — `simulate_fill`
  (T-P2-06) computes `slippage_amount` only as an internal, unreturned
  local. DATABASE.md's `BacktestTrade.slippage_applied` column confirms
  slippage *is* meant to be tracked, but as part of a separate,
  not-yet-built entity. `compute_tearsheet` therefore accepts an
  optional, caller-supplied `total_slippage: Money | None` — the same
  "caller supplies what this module cannot derive on its own" pattern
  already used for `thirty_day_volume` (T-P2-05) and `spread`/
  `volatility` (T-P2-06). `None` means "not measured," distinct from
  a measured value of zero.
- **Sortino's downside deviation and Omega's gain/loss split both use
  `risk_free_rate` as their minimum-acceptable-return threshold** —
  the same parameter Sharpe's numerator already uses — rather than
  introducing separate, additional threshold parameters no acceptance
  criterion asks for.
- **Every ratio that can divide by zero (Sharpe, Sortino, Calmar,
  Omega, win rate, profit factor, avg win, avg loss, expectancy, CAGR)
  returns `None` rather than raising or silently substituting `0`** —
  the same "no division-by-zero guard returning 0... return `None`
  instead" principle AC3 states explicitly for fees-as-%-of-gross,
  applied consistently to every other ratio in this same tearsheet for
  the identical reason: a `0` would misrepresent an undefined quantity
  (e.g., a strategy with zero closed trades does not have a "0% win
  rate" — it has no win rate at all).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TypedDict
from uuid import uuid4

from domain.fee_schedule import fees_as_percentage_of_gross_pnl, total_fees_paid
from domain.fill import Fill
from domain.money import CurrencyMismatch, Money
from domain.position import Position

_DAYS_PER_YEAR = Decimal("365.25")


def _reject_float(value: object, name: str) -> None:
    if isinstance(value, float):  # float-guard
        raise TypeError(f"{name} must be Decimal, not float")
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One `(timestamp, total equity)` sample of a backtest's own
    equity curve — the minimal shape `compute_tearsheet`'s own
    calculations need; see this module's docstring for why this is not
    DATABASE.md's (larger, not-yet-built) `EquitySnapshot`."""

    ts: datetime
    equity: Decimal

    def __post_init__(self) -> None:
        _reject_float(self.equity, "EquityPoint.equity")


class Tearsheet(TypedDict):
    """A structured, JSON-serializable-as-is tearsheet: every numeric
    field is a decimal string (never a raw `float`); fields that can be
    undefined for a given run (e.g. no closed trades) are `None`."""

    total_return: str
    cagr: str | None
    max_drawdown: str
    drawdown_duration_days: str
    sharpe: str | None
    sortino: str | None
    calmar: str | None
    omega: str | None
    win_rate: str | None
    profit_factor: str | None
    avg_win: str | None
    avg_loss: str | None
    expectancy: str | None
    time_in_market: str
    total_fees: str
    slippage: str | None
    fees_pct_of_gross: str | None
    currency: str


def _elapsed_days(start: datetime, end: datetime) -> Decimal:
    """Elapsed time between two timestamps, as a `Decimal` day count —
    built only from `timedelta.days`/`.seconds` (both plain `int`),
    never `.total_seconds()` (a `float`)."""
    delta = end - start
    return Decimal(delta.days) + Decimal(delta.seconds) / Decimal(86400)


def _period_returns(equity_curve: Sequence[EquityPoint]) -> list[Decimal]:
    returns: list[Decimal] = []
    for previous, current in zip(equity_curve, equity_curve[1:], strict=False):
        if previous.equity <= 0:
            raise ValueError(
                "compute_tearsheet: equity must stay positive to compute period returns"
            )
        returns.append((current.equity - previous.equity) / previous.equity)
    return returns


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


def _sample_stdev(values: Sequence[Decimal], mean: Decimal) -> Decimal:
    """Sample (`ddof=1`) standard deviation. `0` for fewer than two
    observations, matching the same "not enough data" convention used
    elsewhere (e.g. `domain/fee_schedule.py`'s tier validation)."""
    if len(values) < 2:
        return Decimal(0)
    variance = sum(((v - mean) * (v - mean) for v in values), Decimal(0)) / (len(values) - 1)
    return variance.sqrt()


def _max_drawdown_and_duration(equity_curve: Sequence[EquityPoint]) -> tuple[Decimal, Decimal]:
    """Max drawdown (a positive fraction of the running peak) and the
    longest time (in days) spent between any peak and its eventual
    recovery to a new peak — or, if never recovered, until the series'
    own end."""
    peak = equity_curve[0].equity
    peak_ts = equity_curve[0].ts
    max_drawdown = Decimal(0)
    max_duration = Decimal(0)
    for point in equity_curve:
        if point.equity > peak:
            peak = point.equity
            peak_ts = point.ts
            continue
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - point.equity) / peak)
        max_duration = max(max_duration, _elapsed_days(peak_ts, point.ts))
    return max_drawdown, max_duration


def _replay_fills(
    fills: Sequence[Fill], equity_curve: Sequence[EquityPoint], currency: str
) -> tuple[list[Decimal], Decimal]:
    """Replays `fills` through a scratch `Position` (T-P0-06), in
    chronological order, returning: (1) one realized P&L per
    reducing/closing/flipping fill (a "trade"), and (2) the total
    number of days the resulting position was non-flat, including the
    interval from the last fill through the equity curve's own end if
    still open."""
    position = Position.flat(uuid4(), uuid4(), currency)
    trade_pnls: list[Decimal] = []
    days_in_market = Decimal(0)
    cursor_ts = equity_curve[0].ts

    for fill in sorted(fills, key=lambda f: f.ts):
        if position.qty != 0:
            days_in_market += _elapsed_days(cursor_ts, fill.ts)
        is_closing = position.qty != 0 and (position.qty > 0) != (fill.signed_qty > 0)
        realized_before = position.realized_pnl
        position = position.apply_fill(fill)
        if is_closing:
            trade_pnls.append((position.realized_pnl - realized_before).amount)
        cursor_ts = fill.ts

    if position.qty != 0:
        days_in_market += _elapsed_days(cursor_ts, equity_curve[-1].ts)

    return trade_pnls, days_in_market


def compute_tearsheet(
    *,
    equity_curve: Sequence[EquityPoint],
    fills: Sequence[Fill],
    risk_free_rate: Decimal = Decimal(0),
    periods_per_year: int = 252,
    total_slippage: Money | None = None,
) -> Tearsheet:
    """Computes the full tearsheet for one backtest run.

    `equity_curve` must be sorted by `ts` ascending, have at least two
    points, and start with strictly positive equity. `fills` must be
    non-empty and share one fee currency; `total_slippage`, if given,
    must share that same currency.
    """
    _reject_float(risk_free_rate, "compute_tearsheet(risk_free_rate=...)")
    if periods_per_year < 1:
        raise ValueError("compute_tearsheet: periods_per_year must be >= 1")
    if len(equity_curve) < 2:
        raise ValueError("compute_tearsheet: equity_curve must have at least 2 points")
    if any(a.ts > b.ts for a, b in zip(equity_curve, equity_curve[1:], strict=False)):
        raise ValueError("compute_tearsheet: equity_curve must be sorted by ts ascending")
    if not fills:
        raise ValueError("compute_tearsheet: fills must be non-empty")

    start, end = equity_curve[0], equity_curve[-1]
    if start.equity <= 0:
        raise ValueError("compute_tearsheet: equity_curve must start with positive equity")

    currency = fills[0].fee.currency
    if total_slippage is not None and total_slippage.currency != currency:
        raise CurrencyMismatch(total_slippage.currency, currency)

    total_return = (end.equity - start.equity) / start.equity

    years = _elapsed_days(start.ts, end.ts) / _DAYS_PER_YEAR
    cagr: Decimal | None
    if years <= 0 or end.equity <= 0:
        cagr = None
    else:
        try:
            cagr = (end.equity / start.equity) ** (Decimal(1) / years) - Decimal(1)
        except InvalidOperation:
            cagr = None

    max_drawdown, drawdown_duration = _max_drawdown_and_duration(equity_curve)

    returns = _period_returns(equity_curve)
    mean_return = _mean(returns)
    stdev_return = _sample_stdev(returns, mean_return)
    annualization = Decimal(periods_per_year).sqrt()

    sharpe = (
        None
        if stdev_return == 0
        else (mean_return - risk_free_rate) / stdev_return * annualization
    )

    downside_sq_sum = sum(
        (min(r - risk_free_rate, Decimal(0)) ** 2 for r in returns), Decimal(0)
    )
    downside_deviation = (downside_sq_sum / len(returns)).sqrt() if returns else Decimal(0)
    sortino = (
        None
        if downside_deviation == 0
        else (mean_return - risk_free_rate) / downside_deviation * annualization
    )

    calmar = None if cagr is None or max_drawdown == 0 else cagr / max_drawdown

    gains = sum((r - risk_free_rate for r in returns if r > risk_free_rate), Decimal(0))
    losses = sum((risk_free_rate - r for r in returns if r < risk_free_rate), Decimal(0))
    omega = None if losses == 0 else gains / losses

    trade_pnls, days_in_market = _replay_fills(fills, equity_curve, currency)
    total_days = _elapsed_days(start.ts, end.ts)
    time_in_market = days_in_market / total_days if total_days > 0 else Decimal(0)

    winning = [pnl for pnl in trade_pnls if pnl > 0]
    losing = [pnl for pnl in trade_pnls if pnl < 0]
    gross_profit = sum(winning, Decimal(0))
    gross_loss = -sum(losing, Decimal(0))

    win_rate = Decimal(len(winning)) / len(trade_pnls) if trade_pnls else None
    profit_factor = (
        None if not trade_pnls or gross_loss == 0 else gross_profit / gross_loss
    )
    avg_win = _mean(winning) if winning else None
    avg_loss = _mean(losing) if losing else None
    expectancy = _mean(trade_pnls) if trade_pnls else None

    total_fees_money = total_fees_paid(fills)
    gross_pnl_money = Money(end.equity - start.equity, currency) + total_fees_money
    try:
        fees_pct_of_gross: Decimal | None = fees_as_percentage_of_gross_pnl(
            total_fees=total_fees_money, gross_pnl=gross_pnl_money
        )
    except ZeroDivisionError:
        fees_pct_of_gross = None

    return {
        "total_return": str(total_return),
        "cagr": None if cagr is None else str(cagr),
        "max_drawdown": str(max_drawdown),
        "drawdown_duration_days": str(drawdown_duration),
        "sharpe": None if sharpe is None else str(sharpe),
        "sortino": None if sortino is None else str(sortino),
        "calmar": None if calmar is None else str(calmar),
        "omega": None if omega is None else str(omega),
        "win_rate": None if win_rate is None else str(win_rate),
        "profit_factor": None if profit_factor is None else str(profit_factor),
        "avg_win": None if avg_win is None else str(avg_win),
        "avg_loss": None if avg_loss is None else str(avg_loss),
        "expectancy": None if expectancy is None else str(expectancy),
        "time_in_market": str(time_in_market),
        "total_fees": str(total_fees_money.amount),
        "slippage": None if total_slippage is None else str(total_slippage.amount),
        "fees_pct_of_gross": None if fees_pct_of_gross is None else str(fees_pct_of_gross),
        "currency": currency,
    }
