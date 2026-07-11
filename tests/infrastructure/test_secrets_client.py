"""Tests for the SecretsClient Protocol and SecretsClientError
(infrastructure/secrets/client.py)."""

from __future__ import annotations

import pytest

from infrastructure.secrets.client import SecretsClient, SecretsClientError


class _StubSecretsClient:
    def get(self, key: str) -> str:
        return f"stub-{key}"

    def zeroize(self) -> None:
        return None


def test_conforming_stub_satisfies_the_protocol_at_runtime() -> None:
    assert isinstance(_StubSecretsClient(), SecretsClient)


def test_non_conforming_object_does_not_satisfy_the_protocol() -> None:
    class _NotASecretsClient:
        pass

    assert not isinstance(_NotASecretsClient(), SecretsClient)


def test_secrets_client_error_is_a_plain_exception_with_a_clear_message() -> None:
    with pytest.raises(SecretsClientError, match="something went wrong"):
        raise SecretsClientError("something went wrong")
