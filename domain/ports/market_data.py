"""Market data ports: the live push feed and the backtest/live-symmetric read view.

`MarketDataFeed` is the live/streaming side: subscribe or unsubscribe to a
symbol/timeframe and receive closed candles as they arrive.

`MarketDataView` is what a strategy actually reads from (ARCHITECTURE.md
§9.1: `on_data(event, view)` — "`view` is a past-only `MarketDataView`").
T-P0-07's own description states its contract precisely: "`bars(symbol,
timeframe, n) → Sequence[Candle]` with the constraint that it cannot
return data past the simulated present (enforced structurally, not by
convention)." That enforcement — a cursor a real implementation cannot
index past — is itself T-P2-02's job ("MarketDataView — Lookahead
Prevention by Construction"), a separate, later, "High" complexity task.
This port only declares the shape every implementation (real or
backtest) must satisfy; it cannot, as a bare `Protocol`, prove the
constraint by itself — only a concrete implementation with real state can.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, runtime_checkable

from domain.candle import Candle


@runtime_checkable
class MarketDataFeed(Protocol):
    """Live candle subscriptions. Delivers only closed candles to `handler`."""

    async def subscribe(
        self, symbol: str, timeframe: str, handler: Callable[[Candle], Awaitable[None]]
    ) -> None: ...

    async def unsubscribe(self, symbol: str, timeframe: str) -> None: ...


@runtime_checkable
class MarketDataView(Protocol):
    """A read-only, past-only window onto candle history.

    Any conforming implementation must guarantee `bars()` never returns a
    candle at or after the caller's simulated present — see this module's
    docstring for why that guarantee is not (and cannot be) enforced here.
    """

    def bars(self, symbol: str, timeframe: str, n: int) -> Sequence[Candle]: ...
