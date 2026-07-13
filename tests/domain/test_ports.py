"""Tests for domain/ports/ — proves each port's shape is practically
implementable and that the package holds no infrastructure imports and no
concrete implementations.

Stub implementations live here, not in domain/ports/, because T-P0-07's
own description is explicit: domain/ports/ contains "No implementations —
only interfaces." These stubs do no real work; they exist to prove:

  (a) via the module-level `Protocol`-typed assignments below, that mypy
      accepts each stub as satisfying its port with zero warnings — this
      is verified with a one-time direct run (documented in the delivery
      report) of `mypy tests/domain/test_ports.py`, since `tests/` is not
      part of the project's continuous `mypy --strict` scope (see
      pyproject.toml's `[tool.mypy] files`); and
  (b) via runtime `isinstance()` checks (only possible because every port
      is `@runtime_checkable`), continuously verified by `make test`/CI.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from domain.candle import Candle
from domain.dataset_version import DatasetVersion
from domain.fill import Fill
from domain.order import Order, OrderSide, OrderType, TimeInForce
from domain.ports import (
    Clock,
    DatasetVersionRepository,
    EventBus,
    MarketDataFeed,
    MarketDataView,
    VenuePort,
)
from domain.position import Position

TS = datetime(2026, 1, 1, tzinfo=UTC)


class StubClock:
    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed


class StubEventBus:
    async def publish(self, topic: str, event: object) -> None:
        return None

    async def subscribe(self, topic: str, handler: Callable[[object], Awaitable[None]]) -> None:
        return None


class StubMarketDataFeed:
    async def subscribe(
        self, symbol: str, timeframe: str, handler: Callable[[Candle], Awaitable[None]]
    ) -> None:
        return None

    async def unsubscribe(self, symbol: str, timeframe: str) -> None:
        return None


class StubMarketDataView:
    def bars(self, symbol: str, timeframe: str, n: int) -> Sequence[Candle]:
        return []


class StubVenuePort:
    async def submit(self, order: Order) -> Order:
        return order

    async def cancel(self, order: Order) -> None:
        return None

    async def cancel_all(self) -> None:
        return None

    async def get_open_orders(self) -> Sequence[Order]:
        return []

    async def get_positions(self) -> Sequence[Position]:
        return []

    async def get_balances(self) -> Mapping[str, Decimal]:
        return {}

    async def get_fills_since(self, venue_fill_id: str | None) -> Sequence[Fill]:
        return []

    async def subscribe_user_stream(
        self, handler: Callable[[object], Awaitable[None]]
    ) -> None:
        return None

    def capabilities(self) -> frozenset[str]:
        return frozenset({"post_only", "reduce_only"})


class StubDatasetVersionRepository:
    def __init__(self, record: DatasetVersion | None) -> None:
        self._record = record

    def get(self, dataset_version_id: UUID) -> DatasetVersion | None:
        return self._record


# --- Static type-check proof --------------------------------------------
# Verified separately with: .venv/Scripts/mypy.exe tests/domain/test_ports.py
# If a stub's shape ever drifts from its port, that command fails here.

_clock_typed: Clock = StubClock(TS)
_event_bus_typed: EventBus = StubEventBus()
_market_data_feed_typed: MarketDataFeed = StubMarketDataFeed()
_market_data_view_typed: MarketDataView = StubMarketDataView()
_venue_typed: VenuePort = StubVenuePort()
_dataset_version_repository_typed: DatasetVersionRepository = StubDatasetVersionRepository(None)


# --- Runtime isinstance() checks (continuously verified by `make test`) ----


def test_stub_clock_satisfies_clock_protocol_at_runtime() -> None:
    assert isinstance(StubClock(TS), Clock)


def test_stub_event_bus_satisfies_event_bus_protocol_at_runtime() -> None:
    assert isinstance(StubEventBus(), EventBus)


def test_stub_market_data_feed_satisfies_protocol_at_runtime() -> None:
    assert isinstance(StubMarketDataFeed(), MarketDataFeed)


def test_stub_market_data_view_satisfies_protocol_at_runtime() -> None:
    assert isinstance(StubMarketDataView(), MarketDataView)


def test_stub_venue_port_satisfies_protocol_at_runtime() -> None:
    assert isinstance(StubVenuePort(), VenuePort)


def test_stub_dataset_version_repository_satisfies_protocol_at_runtime() -> None:
    assert isinstance(StubDatasetVersionRepository(None), DatasetVersionRepository)


# --- Behavioral sanity: the stubs actually run -------------------------------


def test_clock_now_returns_fixed_time() -> None:
    assert StubClock(TS).now() == TS


def test_venue_capabilities_returns_frozenset_of_str() -> None:
    caps = StubVenuePort().capabilities()
    assert isinstance(caps, frozenset)
    assert all(isinstance(c, str) for c in caps)


def test_event_bus_publish_and_subscribe_are_awaitable() -> None:
    async def handler(event: object) -> None:
        return None

    async def run() -> None:
        bus = StubEventBus()
        await bus.publish("topic", {"x": 1})
        await bus.subscribe("topic", handler)

    asyncio.run(run())


def test_market_data_feed_subscribe_and_unsubscribe_are_awaitable() -> None:
    async def handler(candle: Candle) -> None:
        return None

    async def run() -> None:
        feed = StubMarketDataFeed()
        await feed.subscribe("BTC/USDT", "1m", handler)
        await feed.unsubscribe("BTC/USDT", "1m")

    asyncio.run(run())


def test_market_data_view_bars_returns_sequence_of_candle() -> None:
    result = StubMarketDataView().bars("BTC/USDT", "1m", 10)
    assert isinstance(result, list)


def test_venue_port_submit_cancel_and_queries_are_awaitable() -> None:
    order, _ = Order.new(
        id=uuid4(),
        client_order_id="c1",
        venue_id=uuid4(),
        instrument_id=uuid4(),
        strategy_instance_id=uuid4(),
        risk_decision_id=uuid4(),
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("1"),
        tif=TimeInForce.GTC,
        ts=TS,
        event_id=uuid4(),
    )

    async def run() -> None:
        venue = StubVenuePort()
        submitted = await venue.submit(order)
        assert submitted == order
        await venue.cancel(order)
        await venue.cancel_all()
        assert await venue.get_open_orders() == []
        assert await venue.get_positions() == []
        assert await venue.get_balances() == {}
        assert await venue.get_fills_since(None) == []

        async def user_stream_handler(event: object) -> None:
            return None

        await venue.subscribe_user_stream(user_stream_handler)

    asyncio.run(run())


def test_dataset_version_repository_get_returns_the_stored_record() -> None:
    record = DatasetVersion(
        id=uuid4(),
        content_hash="abc123",
        symbol_set=(uuid4(),),
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        created_at=TS,
    )
    repo = StubDatasetVersionRepository(record)
    assert repo.get(record.id) == record


def test_dataset_version_repository_get_returns_none_when_absent() -> None:
    assert StubDatasetVersionRepository(None).get(uuid4()) is None


# --- All ports importable from domain/ports/ with no infrastructure imports -


def test_all_six_ports_are_importable_from_domain_ports_package() -> None:
    from domain.ports import Clock as ImportedClock
    from domain.ports import DatasetVersionRepository as ImportedDatasetVersionRepository
    from domain.ports import EventBus as ImportedEventBus
    from domain.ports import MarketDataFeed as ImportedMarketDataFeed
    from domain.ports import MarketDataView as ImportedMarketDataView
    from domain.ports import VenuePort as ImportedVenuePort

    assert ImportedClock is Clock
    assert ImportedDatasetVersionRepository is DatasetVersionRepository
    assert ImportedEventBus is EventBus
    assert ImportedMarketDataFeed is MarketDataFeed
    assert ImportedMarketDataView is MarketDataView
    assert ImportedVenuePort is VenuePort


def test_domain_ports_source_contains_no_infrastructure_import() -> None:
    """Checks actual import statements via AST, not a raw substring search —
    a docstring explaining *why* infrastructure isn't imported would itself
    contain the word "infrastructure" and shouldn't fail this check."""
    ports_dir = Path("domain/ports")
    for path in ports_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("infrastructure"), (
                        f"{path} imports {alias.name!r}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith("infrastructure"), (
                    f"{path} imports from {node.module!r}"
                )


def test_domain_ports_contains_no_implementations() -> None:
    """T-P0-07: "No implementations — only interfaces." Every class defined
    directly under domain/ports/ must be a Protocol, not a concrete class."""
    ports_dir = Path("domain/ports")
    for path in ports_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                base_names = [base.id for base in node.bases if isinstance(base, ast.Name)]
                assert "Protocol" in base_names, f"{path}:{node.name} is not a Protocol subclass"
