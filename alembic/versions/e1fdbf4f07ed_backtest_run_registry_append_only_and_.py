"""backtest run registry append only and nullable metrics

TASKS.md T-P2-12: "Never deleted... The registry is append-only:
attempting to `DELETE` or `UPDATE` a row raises an RLS or trigger
error." `backtest_run` (T-P0-11) has no such enforcement yet — T-P0-11
only created the table's columns; this is the first task whose own
acceptance criteria actually require the append-only guarantee to be
real, not just documented. A `BEFORE UPDATE OR DELETE` trigger is used
rather than RLS: the "an RLS or trigger error" wording explicitly
accepts either mechanism, and a trigger requires no role/policy setup
to take effect for every connection uniformly (RLS policies are
per-role and a no-op for superusers/table owners unless `FORCE ROW
LEVEL SECURITY` is also set — a trigger has no such escape hatch).

`backtest_metrics` (T-P0-11) declares `volatility`, `sharpe`, `sortino`,
`calmar`, `deflated_sharpe`, `probabilistic_sharpe`, `win_rate`,
`profit_factor`, `avg_trade_pnl`, `turnover`, and `fees_pct_of_gross`
all `NOT NULL` — but `application/backtest/metrics.py`'s own
`Tearsheet` (T-P2-11), built after this table, deliberately returns
`None` for several of these (e.g. `win_rate`/`profit_factor` when a run
has zero closed trades; `sharpe`/`sortino`/`calmar` when the underlying
standard deviation is zero; `fees_pct_of_gross` when gross P&L is
zero — literally this task's own T-P2-05 precedent, "no
division-by-zero guard returning 0... return `None` instead"). Nothing
currently inserts rows into `backtest_metrics` (T-P2-12 is the first),
so relaxing these columns to nullable breaks no existing data or
behavior; it is what makes storing a real, valid `Tearsheet` — including
the entirely ordinary "zero closed trades" case T-P2-11's own buy-and-
hold acceptance test exercises — possible at all. `volatility`,
`deflated_sharpe`, `probabilistic_sharpe`, and `turnover` have no
computation anywhere in this codebase yet (Deflated Sharpe / PBO is
ARCHITECTURE.md's own B-10, an explicitly later, P1-tagged task; T-P2-11
does not compute volatility or turnover either) — nullable, and left
`NULL` until whichever future task computes them, rather than a
fabricated placeholder value. `total_return`, `max_drawdown`, and
`trial_count_at_time_of_run` stay `NOT NULL`: `Tearsheet` always
produces the first two, and the registry itself always computes the
third.

Revision ID: e1fdbf4f07ed
Revises: 652e22543f17
Create Date: 2026-07-14 09:43:38.097314

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1fdbf4f07ed'
down_revision: str | Sequence[str] | None = '652e22543f17'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NULLABLE_METRICS_COLUMNS = (
    "cagr",
    "volatility",
    "sharpe",
    "sortino",
    "calmar",
    "deflated_sharpe",
    "probabilistic_sharpe",
    "win_rate",
    "profit_factor",
    "avg_trade_pnl",
    "turnover",
    "fees_pct_of_gross",
)


def upgrade() -> None:
    """Upgrade schema."""
    for column in _NULLABLE_METRICS_COLUMNS:
        op.execute(f"ALTER TABLE backtest_metrics ALTER COLUMN {column} DROP NOT NULL")

    op.execute(
        """
CREATE OR REPLACE FUNCTION prevent_backtest_run_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'backtest_run is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
CREATE TRIGGER backtest_run_append_only
BEFORE UPDATE OR DELETE ON backtest_run
FOR EACH ROW EXECUTE FUNCTION prevent_backtest_run_mutation()
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER backtest_run_append_only ON backtest_run")
    op.execute("DROP FUNCTION prevent_backtest_run_mutation()")

    for column in _NULLABLE_METRICS_COLUMNS:
        op.execute(f"ALTER TABLE backtest_metrics ALTER COLUMN {column} SET NOT NULL")
