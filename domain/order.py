"""Order aggregate and state machine.

ARCHITECTURE.md §4.6: "States: `PendingNew → Sent → {Acked | Rejected |
Unknown}`. From `Acked`: `PartiallyFilled` (repeatable) or `Filled`; or
`PendingCancel`, which itself can race to `Canceled`, `Filled`, or
`PartiallyFilled`. `Acked` can also transition to `Expired` on TIF elapse.
`Filled`, `Canceled`, `Rejected`, `Expired` are terminal." And: "Note also
`PENDING_CANCEL → FILLED`. A cancel and a fill cross in flight. Any state
machine that treats this as an error will, on a volatile day, hit it
dozens of times."

Two deliberate scoping decisions, both documented at the point they apply
below:

1. `PartiallyFilled` additionally allows `-> Filled`, `-> PendingCancel`,
   and a `-> PartiallyFilled` self-loop. Only the self-loop is explicit in
   the architecture text ("repeatable"); the other two are the minimal
   completion required for the graph to be internally consistent — without
   them there is no legal path from a partially-filled order to `Filled`
   via further fills, nor any way to ever cancel the remainder of one.

2. Transitions *out of* `Unknown` (`Unknown -> Acked`, `Unknown ->
   Rejected`) are intentionally NOT modeled here. Resolving `Unknown`
   requires querying the venue — I/O that has no place in this module —
   and is the explicit subject of a later task (T-P4-07, "Order State
   Machine (Complete, Including UNKNOWN State)"). Within this module,
   `Unknown` is a dead end; T-P4-07 extends the transition table.

This module contains no wall-clock reads and no I/O of any kind. Every
timestamp, event id, and sequence number is supplied by the caller —
determinism here is what makes crash recovery (replaying `OrderEvent`s to
rebuild an `Order`) produce the exact same state as the live system that
produced them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(StrEnum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(StrEnum):
    """DATABASE.md Order.status: pending_new, sent, acked, partially_filled,
    filled, pending_cancel, canceled, rejected, expired, unknown."""

    PENDING_NEW = "pending_new"
    SENT = "sent"
    ACKED = "acked"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    PENDING_CANCEL = "pending_cancel"
    CANCELED = "canceled"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)


class OrderEventType(StrEnum):
    """DATABASE.md OrderEvent.event_type, minus `adopted` (reconciliation /
    orphan-order adoption — a later, M9 concern with no single well-defined
    target status, out of scope here), plus `unknown`.

    `unknown` is not in DATABASE.md's literal enum list. It is required for
    the `Sent -> Unknown` transition mandated by ARCHITECTURE.md §4.6 and
    TASKS.md T-P0-05; the persistence-layer enum (built in T-P0-11, not yet
    implemented) needs to be reconciled with this when that task lands.
    """

    CREATED = "created"
    SENT = "sent"
    ACKED = "acked"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    EXPIRED = "expired"


# The complete legal transition graph. Keys absent or mapping to an empty
# frozenset have no legal outgoing transition (terminal states, and
# `Unknown` — see module docstring point 2).
_LEGAL_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_NEW: frozenset({OrderStatus.SENT}),
    OrderStatus.SENT: frozenset(
        {OrderStatus.ACKED, OrderStatus.REJECTED, OrderStatus.UNKNOWN}
    ),
    OrderStatus.ACKED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PENDING_CANCEL: frozenset(
        {OrderStatus.CANCELED, OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}
    ),
    OrderStatus.UNKNOWN: frozenset(),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}

# The event_type recorded on the OrderEvent produced by a transition into
# a given status. PENDING_NEW has no entry: it is only ever the initial
# status (see `Order.new`), never a `.transition()` target. Note this is
# NOT a simple string-value correspondence — `PENDING_CANCEL` ("pending_
# cancel") is recorded as `CANCEL_REQUESTED` ("cancel_requested") — so a
# separate reverse mapping (`_STATUS_FOR_EVENT_TYPE` below) drives replay.
_EVENT_TYPE_FOR_STATUS: dict[OrderStatus, OrderEventType] = {
    OrderStatus.SENT: OrderEventType.SENT,
    OrderStatus.ACKED: OrderEventType.ACKED,
    OrderStatus.REJECTED: OrderEventType.REJECTED,
    OrderStatus.UNKNOWN: OrderEventType.UNKNOWN,
    OrderStatus.PARTIALLY_FILLED: OrderEventType.PARTIALLY_FILLED,
    OrderStatus.FILLED: OrderEventType.FILLED,
    OrderStatus.PENDING_CANCEL: OrderEventType.CANCEL_REQUESTED,
    OrderStatus.CANCELED: OrderEventType.CANCELED,
    OrderStatus.EXPIRED: OrderEventType.EXPIRED,
}

_STATUS_FOR_EVENT_TYPE: dict[OrderEventType, OrderStatus] = {
    event_type: status for status, event_type in _EVENT_TYPE_FOR_STATUS.items()
}


class InvalidOrderTransition(ValueError):  # noqa: N818 — name fixed by docs/TASKS.md T-P0-05
    """Raised when a transition is attempted that the state machine forbids.

    Never a silent no-op — every illegal transition attempt is a bug
    somewhere upstream and must surface loudly (ARCHITECTURE.md §4.6, M7).
    """

    def __init__(self, current: OrderStatus, attempted: OrderStatus) -> None:
        self.current = current
        self.attempted = attempted
        super().__init__(
            f"Cannot transition order from {current.value!r} to {attempted.value!r}"
        )


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """Append-only record of a single state transition (DATABASE.md OrderEvent).

    `Order.status` is a materialized projection; this is the actual source
    of truth — the order aggregate must be rebuildable from a sequence of
    these alone (see `Order.from_events`).
    """

    id: UUID
    order_id: UUID
    seq: int
    event_type: OrderEventType
    ts: datetime
    payload: MappingProxyType[str, object] = field(default_factory=lambda: MappingProxyType({}))


def _require[T](payload: MappingProxyType[str, object], key: str, expected_type: type[T]) -> T:
    if key not in payload:
        raise ValueError(f"OrderEvent payload is missing required field {key!r}")
    value = payload[key]
    if not isinstance(value, expected_type):
        raise ValueError(
            f"OrderEvent payload field {key!r} has type {type(value).__name__}, "
            f"expected {expected_type.__name__}"
        )
    return value


def _require_optional[T](
    payload: MappingProxyType[str, object], key: str, expected_type: type[T]
) -> T | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, expected_type):
        raise ValueError(
            f"OrderEvent payload field {key!r} has type {type(value).__name__}, "
            f"expected {expected_type.__name__} or None"
        )
    return value


@dataclass(frozen=True, slots=True)
class Order:
    """The order aggregate: system of record for one order, birth to terminal state.

    Structural fields mirror DATABASE.md's Order entity. Behavior is
    limited to identity, status, and the transition state machine — fill
    accounting (`filled_qty`, `avg_fill_price`) and venue-ack population
    (`venue_order_id`) are carried as plain data here but are not computed
    by this module; that belongs to later tasks (Fill/Position domain
    models, OMS wiring).
    """

    id: UUID
    client_order_id: str
    venue_id: UUID
    instrument_id: UUID
    strategy_instance_id: UUID
    risk_decision_id: UUID
    side: OrderSide
    order_type: OrderType
    qty: Decimal
    tif: TimeInForce
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    venue_order_id: str | None = None
    limit_price: Decimal | None = None
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    parent_order_id: UUID | None = None

    @classmethod
    def new(
        cls,
        *,
        id: UUID,
        client_order_id: str,
        venue_id: UUID,
        instrument_id: UUID,
        strategy_instance_id: UUID,
        risk_decision_id: UUID,
        side: OrderSide,
        order_type: OrderType,
        qty: Decimal,
        tif: TimeInForce,
        ts: datetime,
        event_id: UUID,
        limit_price: Decimal | None = None,
        parent_order_id: UUID | None = None,
    ) -> tuple[Order, OrderEvent]:
        """Create a new order in `PendingNew`, paired with its `created` event.

        `ts` and `event_id` are supplied by the caller — this module never
        reads a wall clock or generates its own identifiers.
        """
        order = cls(
            id=id,
            client_order_id=client_order_id,
            venue_id=venue_id,
            instrument_id=instrument_id,
            strategy_instance_id=strategy_instance_id,
            risk_decision_id=risk_decision_id,
            side=side,
            order_type=order_type,
            qty=qty,
            tif=tif,
            status=OrderStatus.PENDING_NEW,
            created_at=ts,
            updated_at=ts,
            limit_price=limit_price,
            parent_order_id=parent_order_id,
        )
        created_event = OrderEvent(
            id=event_id,
            order_id=id,
            seq=1,
            event_type=OrderEventType.CREATED,
            ts=ts,
            payload=MappingProxyType(
                {
                    "client_order_id": client_order_id,
                    "venue_id": venue_id,
                    "instrument_id": instrument_id,
                    "strategy_instance_id": strategy_instance_id,
                    "risk_decision_id": risk_decision_id,
                    "side": side,
                    "order_type": order_type,
                    "qty": qty,
                    "tif": tif,
                    "limit_price": limit_price,
                    "parent_order_id": parent_order_id,
                }
            ),
        )
        return order, created_event

    def transition(
        self,
        new_status: OrderStatus,
        *,
        ts: datetime,
        seq: int,
        event_id: UUID,
        payload: MappingProxyType[str, object] | None = None,
    ) -> tuple[Order, OrderEvent]:
        """Move to `new_status`, or raise `InvalidOrderTransition`.

        Returns the new `Order` and the `OrderEvent` recording the move.
        Never mutates `self` — `Order` is immutable, matching every other
        value/entity type in `domain/`.
        """
        legal_targets = _LEGAL_TRANSITIONS.get(self.status, frozenset())
        if new_status not in legal_targets:
            raise InvalidOrderTransition(self.status, new_status)

        new_order = replace(self, status=new_status, updated_at=ts)
        event = OrderEvent(
            id=event_id,
            order_id=self.id,
            seq=seq,
            event_type=_EVENT_TYPE_FOR_STATUS[new_status],
            ts=ts,
            payload=payload if payload is not None else MappingProxyType({}),
        )
        return new_order, event

    @classmethod
    def from_events(cls, events: list[OrderEvent]) -> Order:
        """Rebuild an `Order` by folding over its full `OrderEvent` history.

        The first event must be `created`; its payload carries every field
        fixed at order birth. Each subsequent event is replayed through the
        same transition legality check `.transition()` uses, so a corrupt
        or out-of-order event log fails loudly rather than silently
        producing a wrong state.
        """
        if not events:
            raise ValueError("Cannot rebuild an Order from an empty event sequence")

        first = events[0]
        if first.event_type is not OrderEventType.CREATED:
            raise ValueError(
                f"First OrderEvent must be {OrderEventType.CREATED!r}, "
                f"got {first.event_type!r}"
            )
        if first.seq != 1:
            raise ValueError(f"First OrderEvent must have seq=1, got {first.seq}")

        payload = first.payload
        order, _ = cls.new(
            id=first.order_id,
            client_order_id=_require(payload, "client_order_id", str),
            venue_id=_require(payload, "venue_id", UUID),
            instrument_id=_require(payload, "instrument_id", UUID),
            strategy_instance_id=_require(payload, "strategy_instance_id", UUID),
            risk_decision_id=_require(payload, "risk_decision_id", UUID),
            side=_require(payload, "side", OrderSide),
            order_type=_require(payload, "order_type", OrderType),
            qty=_require(payload, "qty", Decimal),
            tif=_require(payload, "tif", TimeInForce),
            ts=first.ts,
            event_id=first.id,
            limit_price=_require_optional(payload, "limit_price", Decimal),
            parent_order_id=_require_optional(payload, "parent_order_id", UUID),
        )

        expected_seq = first.seq
        for event in events[1:]:
            expected_seq += 1
            if event.seq != expected_seq:
                raise ValueError(
                    f"OrderEvent sequence is not monotonic: expected seq={expected_seq}, "
                    f"got seq={event.seq}"
                )
            target_status = _STATUS_FOR_EVENT_TYPE[event.event_type]
            order, _ = order.transition(
                target_status,
                ts=event.ts,
                seq=event.seq,
                event_id=event.id,
                payload=event.payload,
            )

        return order
