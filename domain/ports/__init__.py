"""Port interfaces: what the domain needs from the outside world, expressed
as abstract shapes with zero concrete implementations.

ARCHITECTURE.md §4.2 draws "Ports (Interfaces)" inside the Application
layer's box in its layer diagram. TASKS.md T-P0-07 is nonetheless explicit
that these interfaces live "in `domain/ports/`", and does so consistently
elsewhere too (e.g. T-P2-07's `Strategy` port in `domain/strategy/`). The
two aren't in conflict once "layer" is read as a responsibility boundary
rather than a literal package map: §4.2's own dependency rule requires
domain to have "zero dependencies" on anything outward. If a domain-level
consumer (a future `Strategy`, `Position` update path, etc.) needs to
*type-reference* a `Clock` or `VenuePort`, that reference must resolve
without importing `application/` — which is only possible if the pure
interface definitions themselves live in `domain/`. Concrete adapters
(Binance, NATS, Postgres-backed repositories, ...) still belong to
`infrastructure/`, and choosing/wiring a specific adapter for a specific
port still belongs to `application/` — only the interface declarations
are pulled inward, which is what keeps domain's zero-dependency property
achievable at all once ports are referenced by domain-level code.

No implementations live here — see TASKS.md T-P0-07: "No implementations
— only interfaces." Stub implementations used to prove these shapes are
practically satisfiable live in `tests/domain/test_ports.py`.
"""

from domain.ports.clock import Clock
from domain.ports.dataset_version_repository import DatasetVersionRepository
from domain.ports.event_bus import EventBus
from domain.ports.market_data import MarketDataFeed, MarketDataView
from domain.ports.venue import VenuePort

__all__ = [
    "Clock",
    "DatasetVersionRepository",
    "EventBus",
    "MarketDataFeed",
    "MarketDataView",
    "VenuePort",
]
