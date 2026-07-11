"""Tests for EnvSecretsClient (infrastructure/secrets/env.py)."""

from __future__ import annotations

import pytest

from infrastructure.secrets.client import SecretsClientError
from infrastructure.secrets.env import EnvSecretsClient


def test_get_returns_value_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANTTRADE_TEST_SECRET", "s3cr3t-value")
    client = EnvSecretsClient()
    assert client.get("QUANTTRADE_TEST_SECRET") == "s3cr3t-value"


def test_get_raises_secrets_client_error_when_key_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QUANTTRADE_TEST_MISSING", raising=False)
    client = EnvSecretsClient()
    with pytest.raises(SecretsClientError, match="QUANTTRADE_TEST_MISSING"):
        client.get("QUANTTRADE_TEST_MISSING")


def test_get_caches_the_value_held_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARCHITECTURE.md §3.6: "Fetched at runtime, held in memory." A second
    `get()` for the same key must not silently pick up a later change to
    the underlying environment variable — it serves the cached copy."""
    monkeypatch.setenv("QUANTTRADE_TEST_SECRET", "original")
    client = EnvSecretsClient()
    assert client.get("QUANTTRADE_TEST_SECRET") == "original"

    monkeypatch.setenv("QUANTTRADE_TEST_SECRET", "changed-after-first-fetch")
    assert client.get("QUANTTRADE_TEST_SECRET") == "original"


def test_zeroize_overwrites_the_cached_bytes_with_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANTTRADE_TEST_SECRET", "s3cr3t-value")
    client = EnvSecretsClient()
    client.get("QUANTTRADE_TEST_SECRET")

    buffer = client._cache["QUANTTRADE_TEST_SECRET"]
    assert bytes(buffer) == b"s3cr3t-value"

    client.zeroize()

    assert bytes(buffer) == b"\x00" * len(b"s3cr3t-value")


def test_zeroize_clears_the_cache_so_a_later_get_re_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUANTTRADE_TEST_SECRET", "original")
    client = EnvSecretsClient()
    assert client.get("QUANTTRADE_TEST_SECRET") == "original"

    client.zeroize()
    monkeypatch.setenv("QUANTTRADE_TEST_SECRET", "fresh-value")

    assert client.get("QUANTTRADE_TEST_SECRET") == "fresh-value"


def test_zeroize_on_an_empty_client_is_a_safe_no_op() -> None:
    EnvSecretsClient().zeroize()
