"""Live database tests for DatasetVersion creation and repository access
(TASKS.md T-P1-12).

Two of T-P1-12's four acceptance criteria are statements about Postgres
state (a repository round-trip, and a `backtest_run` FK relationship),
so — per the same reasoning already established for T-P1-02 through
T-P1-11 — this suite spins up a real `timescale/timescaledb` container
via `testcontainers` (the identical two-layer Docker-usability strategy
duplicated across this repo's integration tests) and exercises the real
`create_dataset_version` and `PostgresDatasetVersionRepository` against
it — real Postgres rows, no mocking of SQLAlchemy or Alembic.

Every test in this module is skipped, not failed, when Docker isn't
genuinely usable — see test_db_migrations.py's module docstring for the
full rationale behind the two-layer strategy duplicated below.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from infrastructure.backtest.dataset_version_repository import (
    PostgresDatasetVersionRepository,
    create_dataset_version,
    hash_row,
)
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
    """One connection per test, with its own transaction rolled back at the
    end so tests don't leak rows into each other."""
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


def _sample_rows() -> list[dict[str, object]]:
    return [
        {"open_time": "2026-01-01T00:00:00", "close": Decimal("100.50")},
        {"open_time": "2026-01-01T00:01:00", "close": Decimal("101.00")},
    ]


def test_dataset_version_repository_get_returns_the_exact_record_used_to_generate_the_hash(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-12 acceptance criterion, verbatim:
    "`DatasetVersionRepository.get(id)` returns the exact record used to
    generate a hash.\""""
    symbol_set = [uuid.uuid4(), uuid.uuid4()]
    rows = _sample_rows()

    created = create_dataset_version(
        conn,
        symbol_set=symbol_set,
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        row_count=len(rows),
        sample_hashes=[hash_row(r) for r in rows],
        now=lambda: datetime(2026, 2, 1, tzinfo=UTC),
    )

    repo = PostgresDatasetVersionRepository(conn)
    fetched = repo.get(created.id)

    assert fetched is not None
    assert fetched == created
    assert fetched.content_hash == created.content_hash
    assert set(fetched.symbol_set) == set(symbol_set)
    assert fetched.date_range_start == date(2026, 1, 1)
    assert fetched.date_range_end == date(2026, 1, 31)


def test_dataset_version_repository_get_returns_none_for_an_unknown_id(
    conn: sa.Connection,
) -> None:
    repo = PostgresDatasetVersionRepository(conn)
    assert repo.get(uuid.uuid4()) is None


def test_create_dataset_version_is_idempotent_for_identical_content(
    conn: sa.Connection,
) -> None:
    symbol_set = [uuid.uuid4()]
    rows = _sample_rows()
    kwargs = {
        "symbol_set": symbol_set,
        "date_range_start": date(2026, 3, 1),
        "date_range_end": date(2026, 3, 31),
        "row_count": len(rows),
        "sample_hashes": [hash_row(r) for r in rows],
    }

    first = create_dataset_version(conn, **kwargs)  # type: ignore[arg-type]
    second = create_dataset_version(conn, **kwargs)  # type: ignore[arg-type]

    assert first.id == second.id
    assert first.content_hash == second.content_hash

    count = conn.execute(
        sa.text("SELECT count(*) FROM dataset_version WHERE content_hash = :h"),
        {"h": first.content_hash},
    ).scalar_one()
    assert count == 1  # no duplicate row


def test_a_backtest_run_storing_dataset_version_id_can_later_retrieve_the_same_version_metadata(
    conn: sa.Connection,
) -> None:
    """TASKS.md T-P1-12 acceptance criterion, verbatim: "A backtest run
    storing `dataset_version_id` can later retrieve the same version
    metadata.\""""
    symbol_set = [uuid.uuid4()]
    rows = _sample_rows()

    version = create_dataset_version(
        conn,
        symbol_set=symbol_set,
        date_range_start=date(2026, 4, 1),
        date_range_end=date(2026, 4, 30),
        row_count=len(rows),
        sample_hashes=[hash_row(r) for r in rows],
    )

    strategy_id = _insert_strategy(conn)
    run_id = uuid.uuid4()
    conn.execute(
        sa.insert(backtest_run),
        {
            "id": run_id,
            "strategy_id": strategy_id,
            "code_hash": "abc123",
            "params": {},
            "dataset_version_id": version.id,
            "seed": 42,
            "git_sha": "deadbeef",
            "started_at": datetime(2026, 5, 1, tzinfo=UTC),
            "finished_at": None,
            "operator": "test-operator",
        },
    )

    stored_dataset_version_id = conn.execute(
        sa.select(backtest_run.c.dataset_version_id).where(backtest_run.c.id == run_id)
    ).scalar_one()

    repo = PostgresDatasetVersionRepository(conn)
    retrieved = repo.get(stored_dataset_version_id)

    assert retrieved is not None
    assert retrieved == version
    assert retrieved.content_hash == version.content_hash
