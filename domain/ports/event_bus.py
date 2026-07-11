"""EventBus port — decouples producers from consumers.

ARCHITECTURE.md ADR-003: "Start with in-process async queues behind an
`EventBus` port. Move to NATS + JetStream when processes split... The
important part is the port. Code against the interface from day one, and
the transport becomes a configuration change rather than a refactor."

Only `publish` and `subscribe` are part of this port, matching T-P0-07's
literal scope — durability semantics (lossy market data vs. durable
order/fill channels, per ARCHITECTURE.md §4.4/M10) are a property of a
given *implementation* (in-process queues now, NATS/JetStream later,
T-P7-05), not of this abstract shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class EventBus(Protocol):
    """Publish/subscribe over named topics. Transport-agnostic by design."""

    async def publish(self, topic: str, event: object) -> None: ...

    async def subscribe(
        self, topic: str, handler: Callable[[object], Awaitable[None]]
    ) -> None: ...
