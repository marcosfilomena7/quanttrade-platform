"""Tests for domain/strategy/ (TASKS.md T-P2-07)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from domain.candle import Candle
from domain.fill import Fill
from domain.strategy import (
    InvalidStrategyParams,
    ParamSpec,
    Strategy,
    StrategyContext,
    StrategyRegistry,
    validate_params,
)

_INSTRUMENT_ID = uuid4()


def _candle(i: int, *, base: datetime) -> Candle:
    return Candle(
        instrument_id=_INSTRUMENT_ID,
        interval="1m",
        open_time=base + i * timedelta(minutes=1),
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close=Decimal("100.00") + i,
        volume=Decimal("10"),
        is_closed=True,
    )


class _StubView:
    """A minimal stand-in for MarketDataView — the sample strategies
    below never actually read from it."""

    def bars(self, symbol: str, timeframe: str, n: int) -> Sequence[Candle]:
        return ()


class _SampleStrategy(Strategy):
    """A stateless fixture strategy exercising the base contract, used
    to test `StrategyRegistry` discovery/validation/instantiation."""

    @classmethod
    def params_schema(cls) -> Mapping[str, ParamSpec]:
        return {"threshold": ParamSpec(type=Decimal, minimum=Decimal("0"))}

    def subscriptions(self) -> Sequence[tuple[str, str]]:
        return [("BTC/USDT", "1m")]

    def warmup_period(self) -> int:
        return 10

    def on_start(self, context: StrategyContext) -> None:
        return None

    def on_data(self, event: Candle, view: object) -> object | None:
        return None

    def on_fill(self, fill: Fill) -> None:
        return None

    def on_stop(self) -> None:
        return None

    def state(self) -> Mapping[str, object]:
        return {}

    def restore(self, state: Mapping[str, object]) -> None:
        return None


class _RunningSumStrategy(Strategy):
    """A stateful fixture strategy: emits the running sum of every
    bar's close plus the bar count, letting `state()`/`restore()`
    round-tripping be checked against a real "next signal" (AC4)."""

    def __init__(self, *, params: Mapping[str, object]) -> None:
        super().__init__(params=params)
        self._running_sum = Decimal("0")
        self._bars_seen = 0

    @classmethod
    def params_schema(cls) -> Mapping[str, ParamSpec]:
        return {}

    def subscriptions(self) -> Sequence[tuple[str, str]]:
        return [("BTC/USDT", "1m")]

    def warmup_period(self) -> int:
        return 0

    def on_start(self, context: StrategyContext) -> None:
        return None

    def on_data(self, event: Candle, view: object) -> object | None:
        self._running_sum += event.close
        self._bars_seen += 1
        return {"running_sum": self._running_sum, "bars_seen": self._bars_seen}

    def on_fill(self, fill: Fill) -> None:
        return None

    def on_stop(self) -> None:
        return None

    def state(self) -> Mapping[str, object]:
        return {"running_sum": self._running_sum, "bars_seen": self._bars_seen}

    def restore(self, state: Mapping[str, object]) -> None:
        self._running_sum = state["running_sum"]  # type: ignore[assignment]
        self._bars_seen = state["bars_seen"]  # type: ignore[assignment]


# --- acceptance criterion 1: register + instantiate with valid params succeeds --


def test_registering_a_strategy_class_and_instantiating_with_valid_params_succeeds() -> None:
    """TASKS.md T-P2-07 acceptance criterion, verbatim: "Registering a
    strategy class and instantiating it with valid params succeeds.\""""
    registry = StrategyRegistry([__name__])

    assert "_SampleStrategy" in registry.names()

    strategy = registry.instantiate(name="_SampleStrategy", params={"threshold": Decimal("1")})

    assert isinstance(strategy, Strategy)
    assert isinstance(strategy, _SampleStrategy)
    assert strategy.params == {"threshold": Decimal("1")}


def test_registry_discovers_only_classes_defined_in_the_given_module() -> None:
    """`Strategy` itself is imported into this test module's namespace
    (for subclassing) but must never be registered as a strategy."""
    registry = StrategyRegistry([__name__])

    assert "Strategy" not in registry.names()


def test_instantiate_raises_for_an_unknown_strategy_name() -> None:
    registry = StrategyRegistry([__name__])

    with pytest.raises(ValueError, match="No registered strategy"):
        registry.instantiate(name="NotARealStrategy", params={})


# --- acceptance criterion 2: invalid params raise InvalidStrategyParams ---------


def test_instantiating_with_a_param_that_fails_schema_validation_raises() -> None:
    """TASKS.md T-P2-07 acceptance criterion, verbatim: "Instantiating
    with a param that fails schema validation raises
    `InvalidStrategyParams`.\""""
    registry = StrategyRegistry([__name__])

    with pytest.raises(InvalidStrategyParams, match="below minimum"):
        registry.instantiate(name="_SampleStrategy", params={"threshold": Decimal("-1")})


def test_instantiating_with_a_wrong_typed_param_raises() -> None:
    registry = StrategyRegistry([__name__])

    with pytest.raises(InvalidStrategyParams, match="must be Decimal"):
        registry.instantiate(name="_SampleStrategy", params={"threshold": "not-a-decimal"})


def test_instantiating_with_a_missing_required_param_raises() -> None:
    registry = StrategyRegistry([__name__])

    with pytest.raises(InvalidStrategyParams, match="Missing required param"):
        registry.instantiate(name="_SampleStrategy", params={})


def test_instantiating_with_an_unexpected_param_raises() -> None:
    registry = StrategyRegistry([__name__])

    with pytest.raises(InvalidStrategyParams, match="Unexpected params"):
        registry.instantiate(
            name="_SampleStrategy", params={"threshold": Decimal("1"), "extra": Decimal("2")}
        )


def test_validate_params_directly_accepts_a_value_within_bounds() -> None:
    schema = {"x": ParamSpec(type=Decimal, minimum=Decimal("0"), maximum=Decimal("10"))}
    validate_params(schema, {"x": Decimal("5")})  # does not raise


def test_validate_params_directly_rejects_a_value_above_maximum() -> None:
    schema = {"x": ParamSpec(type=Decimal, maximum=Decimal("10"))}
    with pytest.raises(InvalidStrategyParams, match="above maximum"):
        validate_params(schema, {"x": Decimal("11")})


# --- acceptance criterion 4: state()/restore() round-trip -----------------------


def test_state_restore_round_trip_produces_the_same_next_signal() -> None:
    """TASKS.md T-P2-07 acceptance criterion, verbatim: "`state()` /
    `restore(state)` round-trip: a strategy stopped mid-run and
    restored produces the same next signal as a strategy run
    continuously.\""""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [_candle(i, base=base) for i in range(5)]
    view = _StubView()

    continuous = _RunningSumStrategy(params={})
    last_signal_continuous: object = None
    for candle in candles:
        last_signal_continuous = continuous.on_data(candle, view)

    stopped = _RunningSumStrategy(params={})
    for candle in candles[:3]:
        stopped.on_data(candle, view)
    saved_state = stopped.state()

    restored = _RunningSumStrategy(params={})
    restored.restore(saved_state)
    last_signal_restored: object = None
    for candle in candles[3:]:
        last_signal_restored = restored.on_data(candle, view)

    assert last_signal_restored == last_signal_continuous


def test_a_freshly_restored_strategy_has_not_yet_processed_any_new_bars() -> None:
    saved_state = {"running_sum": Decimal("42"), "bars_seen": 3}
    restored = _RunningSumStrategy(params={})

    restored.restore(saved_state)

    assert restored.state() == saved_state


# --- structural sanity -----------------------------------------------------------


def test_strategy_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError, match="abstract"):
        Strategy(params={})  # type: ignore[abstract]


def test_strategy_context_carries_only_the_injected_clock() -> None:
    class _FakeClock:
        def now(self) -> datetime:
            return datetime(2026, 1, 1, tzinfo=UTC)

    context = StrategyContext(clock=_FakeClock())
    assert context.clock.now() == datetime(2026, 1, 1, tzinfo=UTC)
