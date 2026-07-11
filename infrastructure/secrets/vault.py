"""VaultSecretsClient — HashiCorp Vault KV v2 secrets backend.

ARCHITECTURE.md §3.6: "Keys in HashiCorp Vault or cloud KMS... Fetched at
runtime, held in memory, zeroized on shutdown." This is the client
intended for production and staging; `EnvSecretsClient` (env.py) is
strictly for local development.

KV v2 stores secret data one level below the configured mount, under
`data/` (https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v2):
reading `<mount>/data/<path>` returns `{"data": {"data": {<field>:
<value>, ...}}}`. This client is constructed against one fixed Vault path
(typically all credentials for one venue or one purpose, e.g.
`"trading/binance"`), and `get(key)` reads one named field from within
that single secret document.

The `httpx.Client` is required, not defaulted internally: callers own its
construction (base URL, `X-Vault-Token` header, timeouts, retries), and
tests inject an `httpx.MockTransport`-backed client instead of talking to
a real Vault server — matching TASKS.md T-P0-09's acceptance criterion
that unit tests mock this client's HTTP dependency and confirm `get()`
still returns the correct value end-to-end.
"""

from __future__ import annotations

import httpx

from infrastructure.secrets.client import SecretsClientError


class VaultSecretsClient:
    """Reads named fields out of one HashiCorp Vault KV v2 secret document."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        secret_path: str,
        mount_path: str = "secret",
    ) -> None:
        self._http = http_client
        self._secret_path = secret_path.strip("/")
        self._mount_path = mount_path.strip("/")
        self._cache: dict[str, bytearray] = {}

    def get(self, key: str) -> str:
        if key not in self._cache:
            value = self._fetch_field(key)
            self._cache[key] = bytearray(value.encode("utf-8"))
        return self._cache[key].decode("utf-8")

    def _fetch_field(self, key: str) -> str:
        path = f"/v1/{self._mount_path}/data/{self._secret_path}"
        try:
            response = self._http.get(path)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SecretsClientError(f"Vault request to {path!r} failed: {exc}") from exc

        try:
            fields = response.json()["data"]["data"]
        except (KeyError, TypeError, ValueError) as exc:
            raise SecretsClientError(
                f"Vault response from {path!r} did not have the expected "
                f"KV v2 shape (data.data)"
            ) from exc

        if not isinstance(fields, dict) or key not in fields:
            raise SecretsClientError(f"Vault secret at {path!r} has no field {key!r}")
        return str(fields[key])

    def zeroize(self) -> None:
        """Best-effort in Python — see EnvSecretsClient.zeroize()'s docstring
        for exactly what this can and cannot guarantee."""
        for buffer in self._cache.values():
            for i in range(len(buffer)):
                buffer[i] = 0
        self._cache.clear()
