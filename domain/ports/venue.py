"""VenuePort — the single interface the OMS depends on to talk to any exchange.

ARCHITECTURE.md §8.6: "The OMS depends only on a `Venue` port (interface):
`submit`, `cancel`, `cancel_all`, `get_open_orders`, `get_positions`,
`get_balances`, `get_fills_since`, `subscribe_user_stream`,
`capabilities`. Adapters — Binance, Bybit, Coinbase, IBKR (future),
Simulated (backtest), Paper (live data, fake fills) — all implement this
same port. The domain layer knows nothing about any specific venue."

"Every adapter declares a `capabilities` set. The OMS queries capabilities
before constructing an order" — hence the acceptance criterion that
`capabilities()` returns a `frozenset[str]`, checkable before any order is
built, not discovered as a runtime surprise mid-submission.

Every method but `capabilities()` is `async`: even `Simulated`/`Paper`
adapters that do no real I/O implement this same port (§8.6, §4.7) —
making it async everywhere means one interface serves both live network
calls and instant in-memory simulation without a special case for either.
`capabilities()` is a fixed, precomputed fact about the adapter and needs
no I/O to answer, so it stays synchronous, matching the acceptance
criterion's literal signature.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal
from typing import Protocol, runtime_checkable

from domain.fill import Fill
from domain.order import Order
from domain.position import Position


@runtime_checkable
class VenuePort(Protocol):
    """Everything the OMS needs from an exchange, and nothing venue-specific."""

    async def submit(self, order: Order) -> Order: ...

    async def cancel(self, order: Order) -> None: ...

    async def cancel_all(self) -> None: ...

    async def get_open_orders(self) -> Sequence[Order]: ...

    async def get_positions(self) -> Sequence[Position]: ...

    async def get_balances(self) -> Mapping[str, Decimal]: ...

    async def get_fills_since(self, venue_fill_id: str | None) -> Sequence[Fill]: ...

    async def subscribe_user_stream(
        self, handler: Callable[[object], Awaitable[None]]
    ) -> None: ...

    def capabilities(self) -> frozenset[str]: ...
