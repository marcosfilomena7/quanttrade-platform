"""EnvSecretsClient — reads os.environ. Local development only, never in prod.

ARCHITECTURE.md §3.6 is explicit that keys must never be "in an
environment variable in prod." This implementation exists purely so a
developer's own machine can run the system without a running Vault
instance; it must never be wired into a production or staging
configuration — `VaultSecretsClient` (vault.py) is the one that belongs
there.

Fetched values are cached as a mutable `bytearray`, not held only as the
`str` returned to the caller: `str` is immutable in Python, so there is no
way to overwrite one in place. Keeping the client's own copy in a
`bytearray` is what makes `zeroize()` able to do a real, safe, in-place
overwrite of memory this object actually owns, rather than a no-op.
"""

from __future__ import annotations

import os

from infrastructure.secrets.client import SecretsClientError


class EnvSecretsClient:
    """Reads secrets from `os.environ`. For local dev only — never in prod."""

    def __init__(self) -> None:
        self._cache: dict[str, bytearray] = {}

    def get(self, key: str) -> str:
        if key not in self._cache:
            value = os.environ.get(key)
            if value is None:
                raise SecretsClientError(f"Environment variable {key!r} is not set")
            self._cache[key] = bytearray(value.encode("utf-8"))
        return self._cache[key].decode("utf-8")

    def zeroize(self) -> None:
        """Best-effort in Python: overwrite this client's own cached bytes.

        This cannot reach any copy the caller made of a previously
        returned `str` (Python strings are immutable and this client
        never held a reference to begin with), nor can it guarantee the
        original bytes aren't still resident elsewhere in memory. What it
        can guarantee is that the one copy this object is responsible for
        is genuinely, synchronously zeroed before the cache is dropped.
        """
        for buffer in self._cache.values():
            for i in range(len(buffer)):
                buffer[i] = 0
        self._cache.clear()
