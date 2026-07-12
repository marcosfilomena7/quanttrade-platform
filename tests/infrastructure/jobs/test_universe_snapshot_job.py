"""Unit tests for infrastructure/jobs/universe_snapshot_job.py's pure
`is_tradeable` helper — no database required.

The end-to-end capture/idempotency/point-in-time-query behavior against a
real Postgres is covered separately by
test_universe_snapshot_job_integration.py (TASKS.md T-P1-03's acceptance
criteria are all database-state assertions).
"""

from __future__ import annotations

import pytest

from infrastructure.jobs.universe_snapshot_job import is_tradeable


def test_trading_is_tradeable() -> None:
    assert is_tradeable("trading") is True


@pytest.mark.parametrize("status", ["halted", "delisted"])
def test_every_other_status_is_not_tradeable(status: str) -> None:
    assert is_tradeable(status) is False  # type: ignore[arg-type]
