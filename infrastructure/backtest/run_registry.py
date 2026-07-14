"""Backtest Run Registry (TASKS.md T-P2-12).

"Implement auto-logging of every backtest run to the `backtest_run`
table: git SHA (from `git rev-parse HEAD`), strategy code hash (SHA-256
of the strategy class source), full params, `dataset_version_id`,
random seed, start timestamp, all metrics (JSON), and the operator
(username). Never deleted. Implement as a decorator/context manager so
no run can escape logging. Store in `backtest_metrics` as a child
record."

Design decisions, and why:

- **Placed in `infrastructure/backtest/`, not `domain/`/`application/`.**
  This module performs real I/O throughout: a `subprocess` call to `git
  rev-parse HEAD`, and SQLAlchemy Core `INSERT`s against `backtest_run`/
  `backtest_metrics` (T-P0-11). It sits alongside T-P1-12's
  `dataset_version_repository.py` — the same package, the same "persist
  a backtest-related entity via SQLAlchemy Core" shape.
- **No dependency on `application/backtest/loop.py::run_backtest`
  (T-P2-04).** T-P2-12's own dependency list is "T-P0-11, T-P2-11"
  only — not T-P2-04. `log_backtest_run` therefore doesn't call, wrap,
  or even import `run_backtest`; it is a self-contained "persist this
  already-computed run" operation, decoupled from *how* a backtest was
  actually executed. The caller supplies everything this function
  needs — `strategy_id`, `strategy_cls` (for the code hash), `params`,
  `dataset_version_id`, `seed`, timestamps, and the `Tearsheet` (T-P2-11)
  already computed — regardless of what produced them.
- **`BacktestRegistryRequired` (AC1) is enforced via a `contextvars
  .ContextVar`, not a parameter or a global flag.** `BacktestRunRegistry`
  is a context manager (`with BacktestRunRegistry():`) *and* a decorator
  (`@BacktestRunRegistry()`) — both forms set the same context var for
  the duration of the wrapped scope. `log_backtest_run` checks that
  context var at its own entry and raises `BacktestRegistryRequired` if
  it isn't set — "no run can escape logging" (TASKS.md's own words)
  because there is no way to call `log_backtest_run` successfully
  without first entering a registry scope, by construction, regardless
  of what code path leads there. A context var (rather than, say, a
  module-level boolean) is also correctly async-task-local and
  reentrant-safe, matching how the rest of this codebase's own
  concurrency-sensitive code is written.
- **"Two runs... produce two distinct registry rows with different
  hashes" (AC2) is read as: two distinct primary-key identities, not
  two different `code_hash` values.** DATABASE.md's own words for
  `BacktestRun.code_hash` are explicit: "pinned copy of `Strategy
  .code_hash` *as of this run*" — a hash of the strategy's *code*, which
  is identical for two runs of the *same* strategy regardless of
  differing params, exactly as AC2's own premise states ("same
  strategy... different params"). Two calls to `log_backtest_run`
  therefore produce two distinct `backtest_run.id` values (each a fresh
  UUID), each with its own faithfully-stored, differing `params` — this
  is what makes them genuinely distinguishable, queryable rows, which is
  the property AC2 is actually after.
- **Append-only enforcement (AC4) is a database trigger
  (`alembic/versions/e1fdbf4f07ed_*.py`), not application-level code.**
  TASKS.md's own wording — "raises an RLS or trigger error" — names two
  acceptable, DB-level mechanisms; nothing about the property
  ("DELETE/UPDATE always fails") can be reliably enforced from Python
  alone, since any other code path (a future admin script, a different
  service) could otherwise bypass it. A trigger requires no per-role
  policy setup to apply universally (unlike RLS, which is a no-op for
  table owners/superusers unless `FORCE ROW LEVEL SECURITY` is also
  set) — see the migration's own docstring for the full reasoning.
- **`backtest_metrics` columns without a `Tearsheet` source
  (`volatility`, `deflated_sharpe`, `probabilistic_sharpe`, `turnover`)
  are always written as `NULL`, never a fabricated value.** Nothing in
  this codebase computes Deflated Sharpe / Probabilistic Sharpe / PBO
  yet (ARCHITECTURE.md's own B-10, an explicitly later, P1-tagged
  task), nor turnover or a separate annualized-volatility figure
  (T-P2-11's own `Tearsheet` doesn't carry one either — its
  `total_return`/`cagr` cover return, and volatility only appears
  indirectly inside the Sharpe/Sortino calculations). The migration
  companion to this module relaxes exactly these columns (plus every
  other metric `Tearsheet` can validly return `None` for) to nullable.
- **`avg_trade_pnl` (a `backtest_metrics` column) is populated from
  `Tearsheet["expectancy"]`.** Both are defined identically: the mean
  P&L across every closed trade, win or lose (see
  `application/backtest/metrics.py`'s own `expectancy` computation) —
  reusing that value rather than recomputing an equivalent one from
  scratch.
- **`trial_count_at_time_of_run` (DATABASE.md: "how many prior runs
  existed for this strategy... snapshotted so it can never quietly
  drift") is computed here, once, as `COUNT(*) FROM backtest_run WHERE
  strategy_id = :id` evaluated *before* inserting the new row** — the
  same literal query AC3 itself names ("Querying `SELECT COUNT(*) FROM
  backtest_run WHERE strategy_id = $1` returns the correct trial count
  for DSR computation"), reused directly as this column's own value
  rather than a separately-invented computation.
- **`finished_at` is the wall-clock time `log_backtest_run` is actually
  called (`datetime.now(UTC)`); `started_at` is a required caller-
  supplied parameter.** This is registry bookkeeping — recording *when
  this audit record itself was written* — not a strategy's own internal
  notion of time (ARCHITECTURE.md §4.7's "no wall clock" rule governs
  `Strategy` subclasses specifically, T-P2-07; it has no bearing on
  infrastructure-layer audit timestamps, which are inherently real
  wall-clock events). The caller, which actually orchestrated the run,
  is the only party that knows when it *started*.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import subprocess
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from decimal import Decimal
from typing import ParamSpec, TypeVar
from uuid import UUID, uuid4

import sqlalchemy as sa

from application.backtest.metrics import Tearsheet
from infrastructure.db.tables.backtest import backtest_metrics, backtest_run

_P = ParamSpec("_P")
_R = TypeVar("_R")


class BacktestRegistryRequired(RuntimeError):  # noqa: N818 — name fixed by TASKS.md T-P2-12
    """Raised by `log_backtest_run` when called without an active `BacktestRunRegistry`."""


class BacktestRunRegistry:
    """A context manager — and, via `__call__`, a decorator — that must
    be active for any call to `log_backtest_run` to succeed.

        with BacktestRunRegistry():
            run_id = log_backtest_run(conn, ...)

        @BacktestRunRegistry()
        def run_and_log() -> uuid.UUID:
            ...
            return log_backtest_run(conn, ...)
    """

    def __enter__(self) -> BacktestRunRegistry:
        self._token: Token[BacktestRunRegistry | None] = _active_registry.set(self)
        return self

    def __exit__(self, *exc_info: object) -> None:
        _active_registry.reset(self._token)

    def __call__(self, fn: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(fn)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with self:
                return fn(*args, **kwargs)

        return wrapper


_active_registry: ContextVar[BacktestRunRegistry | None] = ContextVar(
    "_active_registry", default=None
)


def _current_git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _strategy_code_hash(strategy_cls: type) -> str:
    return hashlib.sha256(inspect.getsource(strategy_cls).encode("utf-8")).hexdigest()


def _decimal_or_none(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def log_backtest_run(
    conn: sa.Connection,
    *,
    strategy_id: UUID,
    strategy_cls: type,
    params: Mapping[str, object],
    dataset_version_id: UUID,
    seed: int,
    started_at: datetime,
    tearsheet: Tearsheet,
    operator: str,
) -> UUID:
    """Logs one backtest run: inserts one `backtest_run` row (git SHA,
    strategy code hash, params, dataset version, seed, timestamps,
    operator) and one child `backtest_metrics` row (the `tearsheet`).

    Raises `BacktestRegistryRequired` unless called inside an active
    `BacktestRunRegistry` (`with BacktestRunRegistry():` or
    `@BacktestRunRegistry()`). Returns the new run's `id`.
    """
    if _active_registry.get() is None:
        raise BacktestRegistryRequired(
            "log_backtest_run() was called without an active BacktestRunRegistry — "
            "wrap the call in `with BacktestRunRegistry():` or `@BacktestRunRegistry()`"
        )

    trial_count = conn.execute(
        sa.select(sa.func.count())
        .select_from(backtest_run)
        .where(backtest_run.c.strategy_id == strategy_id)
    ).scalar_one()

    run_id = uuid4()
    conn.execute(
        sa.insert(backtest_run),
        {
            "id": run_id,
            "strategy_id": strategy_id,
            "code_hash": _strategy_code_hash(strategy_cls),
            "params": dict(params),
            "dataset_version_id": dataset_version_id,
            "seed": seed,
            "git_sha": _current_git_sha(),
            "started_at": started_at,
            "finished_at": datetime.now(UTC),
            "operator": operator,
        },
    )
    conn.execute(
        sa.insert(backtest_metrics),
        {
            "backtest_run_id": run_id,
            "total_return": Decimal(tearsheet["total_return"]),
            "cagr": _decimal_or_none(tearsheet["cagr"]),
            "volatility": None,
            "max_drawdown": Decimal(tearsheet["max_drawdown"]),
            "sharpe": _decimal_or_none(tearsheet["sharpe"]),
            "sortino": _decimal_or_none(tearsheet["sortino"]),
            "calmar": _decimal_or_none(tearsheet["calmar"]),
            "deflated_sharpe": None,
            "probabilistic_sharpe": None,
            "win_rate": _decimal_or_none(tearsheet["win_rate"]),
            "profit_factor": _decimal_or_none(tearsheet["profit_factor"]),
            "avg_trade_pnl": _decimal_or_none(tearsheet["expectancy"]),
            "turnover": None,
            "fees_pct_of_gross": _decimal_or_none(tearsheet["fees_pct_of_gross"]),
            "trial_count_at_time_of_run": trial_count,
        },
    )
    return run_id
