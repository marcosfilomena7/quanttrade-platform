"""Live database tests for the Backtest Run Registry (TASKS.md T-P2-12).

Three of T-P2-12's four acceptance criteria are statements about real
Postgres state (distinct rows, a `COUNT(*)` trial-count query, and a
DB-level append-only trigger), so — per the same two-layer
Docker-usability strategy duplicated across this repo's integration
tests (see `test_db_migrations.py`'s own module docstring) — this suite
spins up a real `timescale/timescaledb` container, applies the real
migrations (including this task's own append-only trigger and nullable
`backtest_metrics` columns), and exercises `log_backtest_run` against
it directly — real Postgres rows, no mocking of SQLAlchemy or Alembic.

AC1 (`BacktestRegistryRequired` without an active registry) is already
covered, without needing a database at all, in `test_run_registry.py`.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from application.backtest.metrics import Tearsheet
from infrastructure.backtest.dataset_version_repository import create_dataset_version
from infrastructure.backtest.run_registry import BacktestRunRegistry, log_backtest_run
from infrastructure.db.tables.backtest import backtest_run

try:
    from testcontainers.postgres import PostgresContainer

    _container_probe = PostgresContainer(
        image="timescale/timescaledb:2.17.2-pg16", driver="psycopg"
    )
    _container_probe.get_docker_client().client.ping()
    _DOCKER_AVAILABLE = True
except Exception:  # noqa: BLE001 — any failure here just means "skip this module"
    _DOCKER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _DOCKER_AVAILABLE, reason="Docker is not available in this environment"
)

REPO_ROOT = Path(__file__).resolve().parents[3]

_CONNECTIVITY_CHECK_ATTEMPTS = 5
_CONNECTIVITY_CHECK_DELAY_SECONDS = 1.0


@pytest.fixture(scope="module")
def db_engine() -> Iterator[sa.Engine]:
    """A running TimescaleDB container with `alembic upgrade head` already
    applied. See test_db_migrations.py::db_engine for why this performs a
    real host-side connectivity check rather than trusting the
    container's own internal readiness probe."""
    try:
        container = PostgresContainer(
            image="timescale/timescaledb:2.17.2-pg16", driver="psycopg"
        ).start()
    except Exception as exc:  # noqa: BLE001 — unusable environment, not a test failure
        pytest.skip(f"TimescaleDB container could not be started: {exc!r}")

    url = container.get_connection_url()
    engine = sa.create_engine(url)

    unreachable: Exception | None = None
    for attempt in range(_CONNECTIVITY_CHECK_ATTEMPTS):
        try:
            with engine.connect():
                pass
            unreachable = None
            break
        except Exception as exc:  # noqa: BLE001 — see docstring
            unreachable = exc
            if attempt + 1 < _CONNECTIVITY_CHECK_ATTEMPTS:
                time.sleep(_CONNECTIVITY_CHECK_DELAY_SECONDS)

    if unreachable is not None:
        engine.dispose()
        with contextlib.suppress(Exception):
            container.stop()
        pytest.skip(
            "TimescaleDB container started but its host-mapped port is not "
            f"reachable from the test process: {unreachable!r}"
        )

    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
    except Exception as exc:  # noqa: BLE001 — see docstring
        engine.dispose()
        with contextlib.suppress(Exception):
            container.stop()
        pytest.skip(f"alembic upgrade against the TimescaleDB container failed: {exc!r}")

    yield engine

    engine.dispose()
    container.stop()


@pytest.fixture
def conn(db_engine: sa.Engine) -> Iterator[sa.Connection]:
    """One connection per test, with its own transaction rolled back at
    the end so tests don't leak rows into each other."""
    with db_engine.connect() as connection:
        yield connection
        connection.rollback()


def _insert_strategy(conn: sa.Connection) -> uuid.UUID:
    strategy_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO strategy (id, name, code_hash, params_schema, created_at) "
            "VALUES (:id, :name, :code_hash, '{}', now())"
        ),
        {"id": strategy_id, "name": f"strategy-{strategy_id}", "code_hash": "abc123"},
    )
    return strategy_id


def _insert_dataset_version(conn: sa.Connection) -> uuid.UUID:
    version = create_dataset_version(
        conn,
        symbol_set=[uuid.uuid4()],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=1,
        sample_hashes=["abc"],
    )
    return version.id


class _ReferenceStrategy:
    """A fixture "strategy class" whose own source is hashed by
    `log_backtest_run` — stands in for a real `domain.strategy.Strategy`
    subclass without depending on T-P2-07 (not one of T-P2-12's own
    listed dependencies)."""

    def run(self) -> None:
        return None


def _tearsheet(*, win_rate: str | None = "0.6") -> Tearsheet:
    return {
        "total_return": "0.15",
        "cagr": "0.2",
        "max_drawdown": "0.05",
        "drawdown_duration_days": "3",
        "sharpe": "1.5",
        "sortino": "2.0",
        "calmar": "4.0",
        "omega": "1.2",
        "win_rate": win_rate,
        "profit_factor": "1.8",
        "avg_win": "10",
        "avg_loss": "-5",
        "expectancy": "3",
        "time_in_market": "0.9",
        "total_fees": "12.5",
        "slippage": None,
        "fees_pct_of_gross": "1.1",
        "currency": "USDT",
    }


# --- acceptance criterion 2: two runs, different params, distinct rows -------------


def test_two_runs_of_the_same_strategy_with_different_params_produce_two_distinct_rows(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P2-12 acceptance criterion, verbatim: "Two runs of the
    same strategy with different params produce two distinct registry
    rows with different hashes." `code_hash` (a hash of the *strategy's
    code*, per DATABASE.md) is identical for both runs — same strategy —
    while each run's own `id` and stored `params` differ, which is what
    actually makes them "two distinct registry rows.\""""
    strategy_id = _insert_strategy(conn)
    dataset_version_id = _insert_dataset_version(conn)

    with BacktestRunRegistry():
        run_id_1 = log_backtest_run(
            conn,
            strategy_id=strategy_id,
            strategy_cls=_ReferenceStrategy,
            params={"fast_period": 10, "slow_period": 20},
            dataset_version_id=dataset_version_id,
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),
            operator="tester",
        )
        run_id_2 = log_backtest_run(
            conn,
            strategy_id=strategy_id,
            strategy_cls=_ReferenceStrategy,
            params={"fast_period": 12, "slow_period": 26},
            dataset_version_id=dataset_version_id,
            seed=2,
            started_at=datetime(2026, 1, 2, tzinfo=UTC),
            tearsheet=_tearsheet(),
            operator="tester",
        )

    assert run_id_1 != run_id_2

    rows = conn.execute(
        sa.select(backtest_run.c.id, backtest_run.c.code_hash, backtest_run.c.params).where(
            backtest_run.c.id.in_([run_id_1, run_id_2])
        )
    ).all()
    assert len(rows) == 2
    by_id = {row.id: row for row in rows}
    assert by_id[run_id_1].code_hash == by_id[run_id_2].code_hash  # same strategy code
    assert by_id[run_id_1].params != by_id[run_id_2].params  # different params


# --- acceptance criterion 3: COUNT(*) returns the correct trial count ---------------


def test_backtest_run_count_by_strategy_id_returns_the_correct_trial_count(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P2-12 acceptance criterion, verbatim: "Querying `SELECT
    COUNT(*) FROM backtest_run WHERE strategy_id = $1` returns the
    correct trial count for DSR computation.\""""
    strategy_id = _insert_strategy(conn)
    other_strategy_id = _insert_strategy(conn)
    dataset_version_id = _insert_dataset_version(conn)

    with BacktestRunRegistry():
        for seed in range(3):
            log_backtest_run(
                conn,
                strategy_id=strategy_id,
                strategy_cls=_ReferenceStrategy,
                params={"seed": seed},
                dataset_version_id=dataset_version_id,
                seed=seed,
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                tearsheet=_tearsheet(),
                operator="tester",
            )
        log_backtest_run(
            conn,
            strategy_id=other_strategy_id,
            strategy_cls=_ReferenceStrategy,
            params={},
            dataset_version_id=dataset_version_id,
            seed=99,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),
            operator="tester",
        )

    count = conn.execute(
        text("SELECT COUNT(*) FROM backtest_run WHERE strategy_id = :id"),
        {"id": strategy_id},
    ).scalar_one()
    assert count == 3

    other_count = conn.execute(
        text("SELECT COUNT(*) FROM backtest_run WHERE strategy_id = :id"),
        {"id": other_strategy_id},
    ).scalar_one()
    assert other_count == 1


def test_trial_count_at_time_of_run_is_snapshotted_per_run(conn: sa.Connection) -> None:
    strategy_id = _insert_strategy(conn)
    dataset_version_id = _insert_dataset_version(conn)

    with BacktestRunRegistry():
        first_run_id = log_backtest_run(
            conn,
            strategy_id=strategy_id,
            strategy_cls=_ReferenceStrategy,
            params={},
            dataset_version_id=dataset_version_id,
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),
            operator="tester",
        )
        second_run_id = log_backtest_run(
            conn,
            strategy_id=strategy_id,
            strategy_cls=_ReferenceStrategy,
            params={},
            dataset_version_id=dataset_version_id,
            seed=2,
            started_at=datetime(2026, 1, 2, tzinfo=UTC),
            tearsheet=_tearsheet(),
            operator="tester",
        )

    trial_counts = conn.execute(
        text(
            "SELECT backtest_run_id, trial_count_at_time_of_run FROM backtest_metrics "
            "WHERE backtest_run_id IN (:first, :second)"
        ),
        {"first": first_run_id, "second": second_run_id},
    ).all()
    by_id = {row.backtest_run_id: row.trial_count_at_time_of_run for row in trial_counts}
    assert by_id[first_run_id] == 0  # no prior runs existed yet
    assert by_id[second_run_id] == 1  # the first run already existed


# --- acceptance criterion 4: append-only — DELETE/UPDATE raise ----------------------


def test_deleting_a_backtest_run_row_raises(conn: sa.Connection) -> None:
    """TASKS.md T-P2-12 acceptance criterion, verbatim: "The registry is
    append-only: attempting to `DELETE` or `UPDATE` a row raises an RLS
    or trigger error.\""""
    strategy_id = _insert_strategy(conn)
    dataset_version_id = _insert_dataset_version(conn)

    with BacktestRunRegistry():
        run_id = log_backtest_run(
            conn,
            strategy_id=strategy_id,
            strategy_cls=_ReferenceStrategy,
            params={},
            dataset_version_id=dataset_version_id,
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),
            operator="tester",
        )

    with pytest.raises(DBAPIError, match="append-only"):
        conn.execute(sa.delete(backtest_run).where(backtest_run.c.id == run_id))
    conn.rollback()


def test_updating_a_backtest_run_row_raises(conn: sa.Connection) -> None:
    strategy_id = _insert_strategy(conn)
    dataset_version_id = _insert_dataset_version(conn)

    with BacktestRunRegistry():
        run_id = log_backtest_run(
            conn,
            strategy_id=strategy_id,
            strategy_cls=_ReferenceStrategy,
            params={},
            dataset_version_id=dataset_version_id,
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),
            operator="tester",
        )

    with pytest.raises(DBAPIError, match="append-only"):
        conn.execute(
            sa.update(backtest_run).where(backtest_run.c.id == run_id).values(operator="changed")
        )
    conn.rollback()


# --- structural sanity: a full round trip storing a real Tearsheet -----------------


def test_a_run_with_zero_closed_trades_stores_null_win_rate_not_zero(
    conn: sa.Connection,
) -> None:
    """A `Tearsheet` with `win_rate=None` (T-P2-11's own "zero closed
    trades" case) must round-trip as SQL `NULL`, not `0` — this is
    exactly why `backtest_metrics.win_rate` was relaxed to nullable."""
    strategy_id = _insert_strategy(conn)
    dataset_version_id = _insert_dataset_version(conn)

    with BacktestRunRegistry():
        run_id = log_backtest_run(
            conn,
            strategy_id=strategy_id,
            strategy_cls=_ReferenceStrategy,
            params={},
            dataset_version_id=dataset_version_id,
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(win_rate=None),
            operator="tester",
        )

    stored_win_rate = conn.execute(
        text("SELECT win_rate FROM backtest_metrics WHERE backtest_run_id = :id"),
        {"id": run_id},
    ).scalar_one()
    assert stored_win_rate is None
