"""Group G · Backtesting & Research: DatasetVersion, BacktestRun, BacktestTrade, BacktestMetrics.

DATABASE.md §G, entities 20–23.

`BacktestRun.finished_at` and `BacktestTrade.exit_price`/`exit_ts` are
implemented as nullable even though DATABASE.md does not explicitly mark
`finished_at` so, and only calls out `exit_ts` as nullable "if still open
at run end." A run is necessarily inserted (with `started_at`) before it
can possibly have a `finished_at`, and a trade with no `exit_ts` has no
`exit_price` either — the same "still open" reasoning DATABASE.md already
gives for `exit_ts` applies identically to its paired columns.

`BacktestMetrics`'s `cagr`, `volatility`, `sharpe`, `sortino`, `calmar`,
`deflated_sharpe`, `probabilistic_sharpe`, `win_rate`, `profit_factor`,
`avg_trade_pnl`, `turnover`, and `fees_pct_of_gross` are nullable as of
TASKS.md T-P2-12 (see `alembic/versions/e1fdbf4f07ed_*.py`'s own
docstring for the full rationale): `application/backtest/metrics.py`'s
`Tearsheet` (T-P2-11) deliberately returns `None` for several of these
in ordinary, valid scenarios (e.g. zero closed trades), and
`deflated_sharpe`/`probabilistic_sharpe`/`volatility`/`turnover` have no
computation anywhere in this codebase yet. `total_return`,
`max_drawdown`, and `trial_count_at_time_of_run` stay `NOT NULL`.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from infrastructure.db.tables._common import ORDER_SIDE_ENUM, created_at_column, uuid_pk
from infrastructure.db.tables._metadata import metadata

dataset_version = sa.Table(
    "dataset_version",
    metadata,
    uuid_pk(),
    sa.Column("content_hash", sa.String, nullable=False, unique=True),
    sa.Column("symbol_set", JSONB, nullable=False),
    sa.Column("date_range_start", sa.Date, nullable=False),
    sa.Column("date_range_end", sa.Date, nullable=False),
    created_at_column(),
)

backtest_run = sa.Table(
    "backtest_run",
    metadata,
    uuid_pk(),
    sa.Column("strategy_id", sa.Uuid(as_uuid=True), sa.ForeignKey("strategy.id"), nullable=False),
    sa.Column("code_hash", sa.String, nullable=False),
    sa.Column("params", JSONB, nullable=False),
    sa.Column(
        "dataset_version_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("dataset_version.id"),
        nullable=False,
    ),
    sa.Column("seed", sa.Integer, nullable=False),
    sa.Column("git_sha", sa.String, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("operator", sa.String, nullable=False),
)

sa.Index(
    "ix_backtest_run_strategy_id_started_at",
    backtest_run.c.strategy_id,
    backtest_run.c.started_at.desc(),
)

backtest_trade = sa.Table(
    "backtest_trade",
    metadata,
    uuid_pk(),
    sa.Column(
        "backtest_run_id", sa.Uuid(as_uuid=True), sa.ForeignKey("backtest_run.id"), nullable=False
    ),
    sa.Column(
        "instrument_id", sa.Uuid(as_uuid=True), sa.ForeignKey("instrument.id"), nullable=False
    ),
    sa.Column("side", ORDER_SIDE_ENUM, nullable=False),
    sa.Column("qty", sa.Numeric, nullable=False),
    sa.Column("entry_price", sa.Numeric, nullable=False),
    sa.Column("exit_price", sa.Numeric, nullable=True),
    sa.Column("entry_ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("exit_ts", sa.DateTime(timezone=True), nullable=True),
    sa.Column("pnl", sa.Numeric, nullable=False),
    sa.Column("fees", sa.Numeric, nullable=False),
    sa.Column("slippage_applied", sa.Numeric, nullable=False),
    sa.CheckConstraint(
        "exit_ts IS NULL OR entry_ts < exit_ts", name="ck_backtest_trade_entry_before_exit"
    ),
)

sa.Index("ix_backtest_trade_backtest_run_id", backtest_trade.c.backtest_run_id)

backtest_metrics = sa.Table(
    "backtest_metrics",
    metadata,
    sa.Column(
        "backtest_run_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("backtest_run.id"),
        primary_key=True,
    ),
    sa.Column("total_return", sa.Numeric, nullable=False),
    sa.Column("cagr", sa.Numeric, nullable=True),
    sa.Column("volatility", sa.Numeric, nullable=True),
    sa.Column("max_drawdown", sa.Numeric, nullable=False),
    sa.Column("sharpe", sa.Numeric, nullable=True),
    sa.Column("sortino", sa.Numeric, nullable=True),
    sa.Column("calmar", sa.Numeric, nullable=True),
    sa.Column("deflated_sharpe", sa.Numeric, nullable=True),
    sa.Column("probabilistic_sharpe", sa.Numeric, nullable=True),
    sa.Column("win_rate", sa.Numeric, nullable=True),
    sa.Column("profit_factor", sa.Numeric, nullable=True),
    sa.Column("avg_trade_pnl", sa.Numeric, nullable=True),
    sa.Column("turnover", sa.Numeric, nullable=True),
    sa.Column("fees_pct_of_gross", sa.Numeric, nullable=True),
    sa.Column("trial_count_at_time_of_run", sa.Integer, nullable=False),
)
