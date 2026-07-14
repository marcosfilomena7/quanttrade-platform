"""Unit tests for infrastructure/backtest/run_registry.py (TASKS.md T-P2-12).

These exercise everything that doesn't require a real database: the
`BacktestRegistryRequired` gate (checked *before* any DB call —
`log_backtest_run`'s very first statement), `BacktestRunRegistry`'s
context-manager/decorator behavior, and the private git-SHA/code-hash
helpers. Tests that need a real `backtest_run`/`backtest_metrics` round
trip (AC2, AC3, AC4) live in `test_run_registry_integration.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from infrastructure.backtest.run_registry import (
    BacktestRegistryRequired,
    BacktestRunRegistry,
    _current_git_sha,
    _decimal_or_none,
    _strategy_code_hash,
    log_backtest_run,
)


class _SampleStrategyA:
    """A fixture class whose own source is hashed — distinct from
    `_SampleStrategyB` below purely by differing body content."""

    def value(self) -> int:
        return 1


class _SampleStrategyB:
    def value(self) -> int:
        return 2


def _tearsheet() -> dict[str, str | None]:
    return {
        "total_return": "0.1",
        "cagr": "0.2",
        "max_drawdown": "0.05",
        "drawdown_duration_days": "3",
        "sharpe": "1.5",
        "sortino": "2.0",
        "calmar": "4.0",
        "omega": "1.2",
        "win_rate": "0.6",
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


# --- acceptance criterion 1: BacktestRegistryRequired without the registry ----------


def test_log_backtest_run_raises_without_an_active_registry() -> None:
    """TASKS.md T-P2-12 acceptance criterion, verbatim: "Running any
    backtest without the registry decorator causes a
    `BacktestRegistryRequired` error." The check happens before any
    database access, so `conn=None` never actually gets touched."""
    with pytest.raises(BacktestRegistryRequired):
        log_backtest_run(
            None,  # type: ignore[arg-type]
            strategy_id=uuid4(),
            strategy_cls=_SampleStrategyA,
            params={},
            dataset_version_id=uuid4(),
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),  # type: ignore[arg-type]
            operator="tester",
        )


def test_backtest_run_registry_as_a_context_manager_permits_the_call() -> None:
    """Inside `with BacktestRunRegistry():`, the registry check passes —
    `conn=None` then reaches the (mocked-out-by-absence) database call,
    which is exactly the point where a stub `conn` would need to behave
    like a real one; here we only assert the *gate* itself opens by
    checking the raised error is a *different*, later failure (an
    `AttributeError` from calling `.execute()` on `None`), not
    `BacktestRegistryRequired`."""
    with (
        BacktestRunRegistry(),
        pytest.raises(AttributeError),
    ):
        log_backtest_run(
            None,  # type: ignore[arg-type]
            strategy_id=uuid4(),
            strategy_cls=_SampleStrategyA,
            params={},
            dataset_version_id=uuid4(),
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),  # type: ignore[arg-type]
            operator="tester",
        )


def test_backtest_run_registry_as_a_decorator_permits_the_call() -> None:
    @BacktestRunRegistry()
    def run() -> None:
        log_backtest_run(
            None,  # type: ignore[arg-type]
            strategy_id=uuid4(),
            strategy_cls=_SampleStrategyA,
            params={},
            dataset_version_id=uuid4(),
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),  # type: ignore[arg-type]
            operator="tester",
        )

    with pytest.raises(AttributeError):
        run()


def test_the_registry_gate_closes_again_after_the_context_exits() -> None:
    with BacktestRunRegistry():
        pass

    with pytest.raises(BacktestRegistryRequired):
        log_backtest_run(
            None,  # type: ignore[arg-type]
            strategy_id=uuid4(),
            strategy_cls=_SampleStrategyA,
            params={},
            dataset_version_id=uuid4(),
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),  # type: ignore[arg-type]
            operator="tester",
        )


def test_the_registry_gate_closes_again_even_if_the_wrapped_call_raises() -> None:
    with pytest.raises(AttributeError), BacktestRunRegistry():
        log_backtest_run(
            None,  # type: ignore[arg-type]
            strategy_id=uuid4(),
            strategy_cls=_SampleStrategyA,
            params={},
            dataset_version_id=uuid4(),
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),  # type: ignore[arg-type]
            operator="tester",
        )

    with pytest.raises(BacktestRegistryRequired):
        log_backtest_run(
            None,  # type: ignore[arg-type]
            strategy_id=uuid4(),
            strategy_cls=_SampleStrategyA,
            params={},
            dataset_version_id=uuid4(),
            seed=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            tearsheet=_tearsheet(),  # type: ignore[arg-type]
            operator="tester",
        )


# --- private helpers -----------------------------------------------------------------


def test_strategy_code_hash_is_deterministic_for_the_same_class() -> None:
    assert _strategy_code_hash(_SampleStrategyA) == _strategy_code_hash(_SampleStrategyA)


def test_strategy_code_hash_differs_for_classes_with_different_source() -> None:
    assert _strategy_code_hash(_SampleStrategyA) != _strategy_code_hash(_SampleStrategyB)


def test_current_git_sha_returns_a_40_character_hex_string() -> None:
    """This repository is itself a git repo throughout this whole
    session, so `git rev-parse HEAD` is expected to succeed for real."""
    sha = _current_git_sha()
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_decimal_or_none_converts_a_string_to_decimal() -> None:
    assert _decimal_or_none("1.5") == Decimal("1.5")


def test_decimal_or_none_passes_none_through() -> None:
    assert _decimal_or_none(None) is None
