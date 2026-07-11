"""Tests for the Order aggregate and state machine (domain/order.py)."""

import itertools
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from domain.order import (
    InvalidOrderTransition,
    Order,
    OrderEvent,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

TS = datetime(2026, 1, 1, tzinfo=UTC)

# The complete legal transition graph, encoded independently of
# `domain.order._LEGAL_TRANSITIONS` (not imported) so this test verifies
# the module's actual behavior against the specification, rather than
# checking the module's private table against itself.
#
# Source: ARCHITECTURE.md §4.6 + TASKS.md T-P0-05, with two additions
# documented in domain/order.py's module docstring — PartiallyFilled's
# self-loop is explicit ("repeatable"); `-> Filled` and `-> PendingCancel`
# from PartiallyFilled are the minimal completion needed for the graph to
# be usable (otherwise a partially-filled order could never fully fill via
# further fills, nor ever be canceled).
EXPECTED_LEGAL_TRANSITIONS: set[tuple[OrderStatus, OrderStatus]] = {
    (OrderStatus.PENDING_NEW, OrderStatus.SENT),
    (OrderStatus.SENT, OrderStatus.ACKED),
    (OrderStatus.SENT, OrderStatus.REJECTED),
    (OrderStatus.SENT, OrderStatus.UNKNOWN),
    (OrderStatus.ACKED, OrderStatus.PARTIALLY_FILLED),
    (OrderStatus.ACKED, OrderStatus.FILLED),
    (OrderStatus.ACKED, OrderStatus.PENDING_CANCEL),
    (OrderStatus.ACKED, OrderStatus.EXPIRED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.EXPIRED),
    (OrderStatus.PENDING_CANCEL, OrderStatus.CANCELED),
    (OrderStatus.PENDING_CANCEL, OrderStatus.FILLED),
    (OrderStatus.PENDING_CANCEL, OrderStatus.PARTIALLY_FILLED),
}

TERMINAL_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
}


def _new_order() -> tuple[Order, OrderEvent]:
    return Order.new(
        id=uuid4(),
        client_order_id="strat-1|BTCUSDT|1|buy",
        venue_id=uuid4(),
        instrument_id=uuid4(),
        strategy_instance_id=uuid4(),
        risk_decision_id=uuid4(),
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("1.5"),
        tif=TimeInForce.GTC,
        ts=TS,
        event_id=uuid4(),
        limit_price=Decimal("50000"),
    )


def _order_in_status(status: OrderStatus) -> Order:
    """Force an order into an arbitrary status for exhaustive matrix testing.

    Bypasses `.transition()` deliberately — this constructs a *starting*
    fixture, it is not exercising the state machine itself.
    """
    order, _ = _new_order()
    return replace(order, status=status)


# --- Exhaustive legal/illegal transition matrix -----------------------------


@pytest.mark.parametrize(
    ("from_status", "to_status"), list(itertools.product(OrderStatus, OrderStatus))
)
def test_transition_matrix(from_status: OrderStatus, to_status: OrderStatus) -> None:
    order = _order_in_status(from_status)
    if (from_status, to_status) in EXPECTED_LEGAL_TRANSITIONS:
        new_order, event = order.transition(to_status, ts=TS, seq=2, event_id=uuid4())
        assert new_order.status == to_status
        assert event.seq == 2
        assert event.order_id == order.id
    else:
        with pytest.raises(InvalidOrderTransition) as exc_info:
            order.transition(to_status, ts=TS, seq=2, event_id=uuid4())
        assert exc_info.value.current == from_status
        assert exc_info.value.attempted == to_status


def test_every_expected_legal_transition_is_covered_by_the_matrix() -> None:
    """Guards the matrix itself: every entry in EXPECTED_LEGAL_TRANSITIONS
    names real OrderStatus members, so a typo there can't silently pass."""
    all_statuses = set(OrderStatus)
    for from_status, to_status in EXPECTED_LEGAL_TRANSITIONS:
        assert from_status in all_statuses
        assert to_status in all_statuses


# --- Named tests for the acceptance-criteria examples specifically --------


def test_pending_cancel_to_filled_is_legal_cancel_fill_race() -> None:
    order = _order_in_status(OrderStatus.PENDING_CANCEL)
    new_order, event = order.transition(OrderStatus.FILLED, ts=TS, seq=5, event_id=uuid4())
    assert new_order.status == OrderStatus.FILLED


def test_filled_to_sent_is_illegal() -> None:
    order = _order_in_status(OrderStatus.FILLED)
    with pytest.raises(InvalidOrderTransition):
        order.transition(OrderStatus.SENT, ts=TS, seq=2, event_id=uuid4())


@pytest.mark.parametrize("terminal_status", sorted(TERMINAL_STATUSES))
def test_terminal_states_have_no_legal_outgoing_transition(terminal_status: OrderStatus) -> None:
    assert terminal_status.is_terminal
    order = _order_in_status(terminal_status)
    for candidate in OrderStatus:
        with pytest.raises(InvalidOrderTransition):
            order.transition(candidate, ts=TS, seq=2, event_id=uuid4())


def test_unknown_is_not_terminal_but_has_no_legal_transition_in_this_module() -> None:
    """Unknown resolution requires venue I/O — deferred to T-P4-07."""
    assert not OrderStatus.UNKNOWN.is_terminal
    order = _order_in_status(OrderStatus.UNKNOWN)
    for candidate in OrderStatus:
        with pytest.raises(InvalidOrderTransition):
            order.transition(candidate, ts=TS, seq=2, event_id=uuid4())


# --- Order.new() ----------------------------------------------------------


def test_new_order_starts_pending_new_with_seq_one_created_event() -> None:
    order, event = _new_order()
    assert order.status == OrderStatus.PENDING_NEW
    assert order.created_at == TS
    assert order.updated_at == TS
    assert order.filled_qty == Decimal("0")
    assert order.avg_fill_price is None
    assert order.venue_order_id is None
    assert event.seq == 1
    assert event.order_id == order.id


# --- Rebuilding from OrderEvent sequence -----------------------------------


def test_rebuild_from_events_matches_live_application_full_lifecycle() -> None:
    order, created = _new_order()
    events = [created]

    order, ev = order.transition(OrderStatus.SENT, ts=TS, seq=2, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.ACKED, ts=TS, seq=3, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.PARTIALLY_FILLED, ts=TS, seq=4, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.PARTIALLY_FILLED, ts=TS, seq=5, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.FILLED, ts=TS, seq=6, event_id=uuid4())
    events.append(ev)

    rebuilt = Order.from_events(events)
    assert rebuilt == order


def test_rebuild_from_events_matches_live_application_cancel_fill_race() -> None:
    order, created = _new_order()
    events = [created]

    order, ev = order.transition(OrderStatus.SENT, ts=TS, seq=2, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.ACKED, ts=TS, seq=3, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.PENDING_CANCEL, ts=TS, seq=4, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.FILLED, ts=TS, seq=5, event_id=uuid4())
    events.append(ev)

    rebuilt = Order.from_events(events)
    assert rebuilt == order
    assert rebuilt.status == OrderStatus.FILLED


def test_rebuild_from_events_matches_live_application_rejection() -> None:
    order, created = _new_order()
    events = [created]

    order, ev = order.transition(OrderStatus.SENT, ts=TS, seq=2, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.REJECTED, ts=TS, seq=3, event_id=uuid4())
    events.append(ev)

    rebuilt = Order.from_events(events)
    assert rebuilt == order
    assert rebuilt.status == OrderStatus.REJECTED


def test_rebuild_from_events_matches_live_application_expiry() -> None:
    order, created = _new_order()
    events = [created]

    order, ev = order.transition(OrderStatus.SENT, ts=TS, seq=2, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.ACKED, ts=TS, seq=3, event_id=uuid4())
    events.append(ev)
    order, ev = order.transition(OrderStatus.EXPIRED, ts=TS, seq=4, event_id=uuid4())
    events.append(ev)

    rebuilt = Order.from_events(events)
    assert rebuilt == order
    assert rebuilt.status == OrderStatus.EXPIRED


def test_rebuild_preserves_identity_fields_not_just_status() -> None:
    order, created = _new_order()
    events = [created]
    order, ev = order.transition(OrderStatus.SENT, ts=TS, seq=2, event_id=uuid4())
    events.append(ev)

    rebuilt = Order.from_events(events)
    assert rebuilt.id == order.id
    assert rebuilt.client_order_id == order.client_order_id
    assert rebuilt.venue_id == order.venue_id
    assert rebuilt.instrument_id == order.instrument_id
    assert rebuilt.strategy_instance_id == order.strategy_instance_id
    assert rebuilt.risk_decision_id == order.risk_decision_id
    assert rebuilt.side == order.side
    assert rebuilt.order_type == order.order_type
    assert rebuilt.qty == order.qty
    assert rebuilt.tif == order.tif
    assert rebuilt.limit_price == order.limit_price


def test_rebuild_from_empty_sequence_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        Order.from_events([])


def test_rebuild_requires_first_event_to_be_created() -> None:
    order, _ = _new_order()
    _, sent_event = order.transition(OrderStatus.SENT, ts=TS, seq=1, event_id=uuid4())
    with pytest.raises(ValueError, match="created"):
        Order.from_events([sent_event])


def test_rebuild_requires_first_event_seq_to_be_one() -> None:
    _, created = _new_order()
    bad_first = replace(created, seq=2)
    with pytest.raises(ValueError, match="seq=1"):
        Order.from_events([bad_first])


def test_rebuild_requires_monotonic_seq() -> None:
    order, created = _new_order()
    _, sent_event = order.transition(OrderStatus.SENT, ts=TS, seq=5, event_id=uuid4())
    with pytest.raises(ValueError, match="monotonic"):
        Order.from_events([created, sent_event])


# --- No wall clock, no I/O --------------------------------------------------


def test_module_reads_no_wall_clock_and_performs_no_io() -> None:
    source = Path("domain/order.py").read_text(encoding="utf-8")
    forbidden = ["datetime.now(", "time.time(", "open(", "requests.", "httpx.", "socket."]
    for token in forbidden:
        assert token not in source, f"domain/order.py must not contain {token!r}"


# --- Decimal, never float ---------------------------------------------------


def test_qty_and_limit_price_are_decimal_not_float() -> None:
    order, _ = _new_order()
    assert isinstance(order.qty, Decimal)
    assert isinstance(order.limit_price, Decimal)
    assert isinstance(order.filled_qty, Decimal)
