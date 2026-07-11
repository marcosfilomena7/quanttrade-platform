"""Tests for structured logging (infrastructure/observability/logging.py).

`capsys` captures real process stdout, proving what actually gets printed
by the configured pipeline — not a mocked/bypassed version of it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import structlog

from infrastructure.observability.correlation import CorrelationContext
from infrastructure.observability.logging import configure_logging


@pytest.fixture(autouse=True)
def _configure(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    capsys.readouterr()  # discard anything printed during configuration itself


def _parse_lines(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_log_line_has_level_event_and_ts_fields(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.get_logger().info("something happened")
    records = _parse_lines(capsys.readouterr().out)
    assert len(records) == 1
    record = records[0]
    assert record["level"] == "info"
    assert record["event"] == "something happened"
    assert "ts" in record and isinstance(record["ts"], str) and record["ts"]


@pytest.mark.parametrize("method", ["debug", "info", "warning", "error"])
def test_every_log_level_still_carries_all_three_required_fields(
    method: str, capsys: pytest.CaptureFixture[str]
) -> None:
    getattr(structlog.get_logger(), method)("x")
    records = _parse_lines(capsys.readouterr().out)
    assert len(records) == 1
    for required_field in ("level", "event", "ts"):
        assert required_field in records[0]


def test_log_line_in_coroutine_with_correlation_context_includes_all_ids(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def emit() -> None:
        with CorrelationContext(
            signal_id="sig-1", intent_id="int-1", order_id="ord-1", fill_id="fil-1"
        ):
            structlog.get_logger().info("order acked")

    asyncio.run(emit())

    records = _parse_lines(capsys.readouterr().out)
    assert len(records) == 1
    record = records[0]
    assert record["signal_id"] == "sig-1"
    assert record["intent_id"] == "int-1"
    assert record["order_id"] == "ord-1"
    assert record["fill_id"] == "fil-1"
    assert record["level"] == "info"
    assert record["event"] == "order acked"
    assert "ts" in record


def test_log_line_outside_any_correlation_context_has_no_correlation_ids(
    capsys: pytest.CaptureFixture[str],
) -> None:
    structlog.get_logger().info("no context here")
    record = _parse_lines(capsys.readouterr().out)[0]
    for key in ("signal_id", "intent_id", "order_id", "fill_id"):
        assert key not in record


def test_extra_bound_fields_are_preserved_alongside_required_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    structlog.get_logger().info("fill received", venue="binance", qty="1.5")
    record = _parse_lines(capsys.readouterr().out)[0]
    assert record["venue"] == "binance"
    assert record["qty"] == "1.5"
    assert record["level"] == "info"
    assert record["event"] == "fill received"
    assert "ts" in record
