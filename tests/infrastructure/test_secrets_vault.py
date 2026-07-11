"""Tests for VaultSecretsClient (infrastructure/secrets/vault.py).

TASKS.md T-P0-09: "Unit tests mock `VaultSecretsClient` and confirm
`get()` returns the correct value." Here that means mocking the one
dependency `VaultSecretsClient` actually has — the HTTP transport — via
`httpx.MockTransport`, then exercising the real `get()` end to end. That
proves this module's own Vault KV v2 response parsing is correct, which a
test that mocked the whole class away could never do.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from infrastructure.secrets.client import SecretsClientError
from infrastructure.secrets.vault import VaultSecretsClient


def _client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> VaultSecretsClient:
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://vault.internal"
    )
    return VaultSecretsClient(http_client=http_client, secret_path="trading/binance")


def test_get_returns_correct_value_via_mocked_http_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/secret/data/trading/binance"
        return httpx.Response(200, json={"data": {"data": {"api_key": "abc123"}}})

    client = _client_with_handler(handler)
    assert client.get("api_key") == "abc123"


def test_get_uses_configured_mount_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/kv/data/trading/binance"
        return httpx.Response(200, json={"data": {"data": {"api_key": "abc123"}}})

    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://vault.internal")
    client = VaultSecretsClient(
        http_client=http_client, secret_path="trading/binance", mount_path="kv"
    )
    assert client.get("api_key") == "abc123"


def test_get_caches_value_and_does_not_reissue_an_http_request() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"data": {"data": {"api_key": "abc123"}}})

    client = _client_with_handler(handler)
    assert client.get("api_key") == "abc123"
    assert client.get("api_key") == "abc123"
    assert call_count == 1


def test_get_raises_secrets_client_error_on_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": ["not found"]})

    client = _client_with_handler(handler)
    with pytest.raises(SecretsClientError):
        client.get("api_key")


def test_get_raises_secrets_client_error_on_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = _client_with_handler(handler)
    with pytest.raises(SecretsClientError, match="data.data"):
        client.get("api_key")


def test_get_raises_secrets_client_error_when_field_is_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": {"other_field": "x"}}})

    client = _client_with_handler(handler)
    with pytest.raises(SecretsClientError, match="api_key"):
        client.get("api_key")


def test_zeroize_overwrites_the_cached_bytes_with_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": {"api_key": "abc123"}}})

    client = _client_with_handler(handler)
    client.get("api_key")

    buffer = client._cache["api_key"]
    assert bytes(buffer) == b"abc123"

    client.zeroize()

    assert bytes(buffer) == b"\x00" * len(b"abc123")


def test_zeroize_clears_the_cache_so_a_later_get_re_fetches() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"data": {"data": {"api_key": "abc123"}}})

    client = _client_with_handler(handler)
    client.get("api_key")
    client.zeroize()
    client.get("api_key")

    assert call_count == 2
