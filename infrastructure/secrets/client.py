"""SecretsClient — the interface config loading uses to fetch credentials.

ARCHITECTURE.md §3.6: "Keys in HashiCorp Vault or cloud KMS... Fetched at
runtime, held in memory, zeroized on shutdown." Config loading must go
through this abstraction and never read `os.environ` directly for a
credential — enforced by `scripts/check_no_env_secrets.py`, wired into
`make lint`.

`Protocol`, matching the style already established for `domain/ports/`
(T-P0-07): a structural interface, `@runtime_checkable` so conformance is
checkable at runtime, with no forced inheritance on either implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class SecretsClientError(Exception):
    """Raised when a secret cannot be retrieved."""


@runtime_checkable
class SecretsClient(Protocol):
    """Fetch a secret by key; forget it, best-effort, when told to."""

    def get(self, key: str) -> str: ...

    def zeroize(self) -> None: ...
