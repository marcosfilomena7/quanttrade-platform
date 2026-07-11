"""Tests for CorrelationContext (infrastructure/observability/correlation.py)."""

from infrastructure.observability.correlation import CorrelationContext


def test_current_is_empty_outside_any_context() -> None:
    assert CorrelationContext.current() == {}


def test_binding_all_four_ids_makes_them_all_current() -> None:
    with CorrelationContext(
        signal_id="sig-1", intent_id="int-1", order_id="ord-1", fill_id="fil-1"
    ):
        assert CorrelationContext.current() == {
            "signal_id": "sig-1",
            "intent_id": "int-1",
            "order_id": "ord-1",
            "fill_id": "fil-1",
        }
    assert CorrelationContext.current() == {}


def test_binding_a_subset_leaves_others_absent() -> None:
    with CorrelationContext(signal_id="sig-1"):
        assert CorrelationContext.current() == {"signal_id": "sig-1"}


def test_nested_contexts_accumulate_the_full_chain() -> None:
    with CorrelationContext(signal_id="sig-1"):
        assert CorrelationContext.current() == {"signal_id": "sig-1"}
        with CorrelationContext(intent_id="int-1"):
            assert CorrelationContext.current() == {
                "signal_id": "sig-1",
                "intent_id": "int-1",
            }
            with CorrelationContext(order_id="ord-1"):
                assert CorrelationContext.current() == {
                    "signal_id": "sig-1",
                    "intent_id": "int-1",
                    "order_id": "ord-1",
                }
                with CorrelationContext(fill_id="fil-1"):
                    assert CorrelationContext.current() == {
                        "signal_id": "sig-1",
                        "intent_id": "int-1",
                        "order_id": "ord-1",
                        "fill_id": "fil-1",
                    }
                # fill_id gone, everything else still bound
                assert CorrelationContext.current() == {
                    "signal_id": "sig-1",
                    "intent_id": "int-1",
                    "order_id": "ord-1",
                }
            assert CorrelationContext.current() == {
                "signal_id": "sig-1",
                "intent_id": "int-1",
            }
        assert CorrelationContext.current() == {"signal_id": "sig-1"}
    assert CorrelationContext.current() == {}


def test_exiting_an_inner_context_does_not_clobber_outer_bindings() -> None:
    """An inner context's __exit__ resets only the tokens it created —
    it must never reset a key it didn't itself bind."""
    with CorrelationContext(signal_id="outer"):
        with CorrelationContext(signal_id="inner", intent_id="int-1"):
            assert CorrelationContext.current() == {
                "signal_id": "inner",
                "intent_id": "int-1",
            }
        # signal_id reverts to the outer value; intent_id is gone entirely.
        assert CorrelationContext.current() == {"signal_id": "outer"}
