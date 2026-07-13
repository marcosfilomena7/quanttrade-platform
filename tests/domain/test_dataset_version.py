"""Tests for domain/dataset_version.py (TASKS.md T-P1-12)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from domain.dataset_version import DatasetVersion

TS = datetime(2026, 1, 1, tzinfo=UTC)


def _make(**overrides: object) -> DatasetVersion:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "content_hash": "abc123",
        "symbol_set": (uuid4(),),
        "date_range_start": date(2026, 1, 1),
        "date_range_end": date(2026, 1, 31),
        "created_at": TS,
    }
    defaults.update(overrides)
    return DatasetVersion(**defaults)  # type: ignore[arg-type]


def test_a_well_formed_dataset_version_constructs_successfully() -> None:
    version = _make()
    assert version.content_hash == "abc123"


def test_empty_content_hash_is_rejected() -> None:
    with pytest.raises(ValueError, match="content_hash"):
        _make(content_hash="")


def test_empty_symbol_set_is_rejected() -> None:
    with pytest.raises(ValueError, match="symbol_set"):
        _make(symbol_set=())


def test_date_range_start_after_end_is_rejected() -> None:
    with pytest.raises(ValueError, match="date_range_start"):
        _make(date_range_start=date(2026, 2, 1), date_range_end=date(2026, 1, 1))


def test_date_range_start_equal_to_end_is_allowed() -> None:
    version = _make(date_range_start=date(2026, 1, 1), date_range_end=date(2026, 1, 1))
    assert version.date_range_start == version.date_range_end


def test_dataset_version_is_frozen() -> None:
    version = _make()
    with pytest.raises(AttributeError):
        version.content_hash = "different"  # type: ignore[misc]
