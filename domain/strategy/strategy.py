"""Strategy ABC — the shape every trading strategy must implement (TASKS.md T-P2-07).

"Define the `Strategy` ABC in `domain/strategy/`: `subscriptions()`,
`warmup_period()`, `params_schema()`, `on_start(context)`,
`on_data(event, view)`, `on_fill(fill)`, `on_stop()`, `state()`,
`restore(state)`. ... No network, no database, no wall clock in any
strategy method — enforced by the port contract and lint."

Design decisions, and why:

- **An `abc.ABC`, not a `Protocol`.** The task's own words are explicit:
  "Define the `Strategy` ABC." Every other domain port built so far
  (`Clock`, `EventBus`, `MarketDataFeed`, `MarketDataView`, `VenuePort`,
  `DatasetVersionRepository`) is a structural `Protocol`; `Strategy` is
  deliberately different, matching `StrategyRegistry`'s own "discovers
  strategy classes... instantiates" framing — subclass discovery via
  `issubclass()` and a single, inherited, concrete `__init__` are both
  natural fits for a real base class, not a bare structural shape.
- **`params_schema()` is a `classmethod`; every other method is an
  instance method.** `StrategyRegistry`'s own literal step order is
  "validates parameter schema against provided params, instantiates" —
  validation happens *before* an instance exists, so the schema must be
  queryable directly from the class. `subscriptions()`/
  `warmup_period()` are left as instance methods since a real strategy
  may reasonably derive either from its own (already-validated,
  already-set) params — e.g. a strategy whose "lookback" param
  determines its own warmup length.
- **The base `__init__` accepts and stores `params: Mapping[str,
  object]` directly; `on_start(context)` receives only runtime
  collaborators (currently just `clock`).** `StrategyRegistry
  .instantiate()` needs one consistent way to hand every subclass its
  already-validated params at construction time; `on_start` is a
  separate, later lifecycle hook (called by whoever actually runs the
  strategy — a later task's own concern) for collaborators that aren't
  known until a specific run starts. Concrete subclasses may still
  override `__init__` for their own additional setup, as long as they
  call `super().__init__(params=params)`.
- **`StrategyContext` carries only `clock: Clock`.** ARCHITECTURE.md
  §4.7: "The strategy cannot access a wall clock. It receives time from
  the injected `Clock` port." This is the *only* sanctioned way a
  strategy may ever learn "now" — never `datetime.now()` directly, which
  `scripts/check_no_wall_clock_in_strategy.py` (this same task)
  statically forbids inside any Strategy subclass's own methods.
- **`params_schema()` returns `Mapping[str, ParamSpec]`, a minimal
  "typed bounds" shape** — DATABASE.md's own literal words for
  `Strategy.params_schema` ("typed bounds; doubles as the optimizer's
  search space"). Only a `type` plus optional numeric `minimum`/
  `maximum` bounds are modeled; the optimizer that will actually consume
  this as a "search space" is a separate, much later concern (no such
  task is a listed dependency here) — this is the minimal shape both of
  T-P2-07's own testable acceptance criteria (valid params succeed;
  invalid ones raise) actually need. Bounds are typed `Decimal | int`,
  never `float` — ARCHITECTURE.md §3.5's blanket float ban applies to
  all of `domain/`, and nothing about a parameter bound is exempt from
  it; a strategy wanting a fractional bound uses `Decimal`.
- **`on_data`'s `view` parameter is typed as the domain port
  `MarketDataView` (T-P0-07), never the concrete `CursorMarketDataView`
  (T-P2-02).** `domain/strategy/` cannot import `infrastructure/` (the
  Dependency Rule) even though T-P2-02 is a listed dependency; exactly
  like T-P2-04's own `BacktestStrategy.on_data`, the concrete
  implementation is supplied by whoever actually runs a strategy (tests
  are free to construct a real `CursorMarketDataView`, since tests sit
  outside the three-layer stack).
- **`state()`/`restore(state)` are abstract, with no default
  (`{}`-returning) implementation.** AC4's own round-trip requirement
  ("a strategy stopped mid-run and restored produces the same next
  signal as a strategy run continuously") can only hold if every
  concrete strategy actually implements them meaningfully; a silent
  default that returns nothing would make the round-trip trivially (and
  wrongly) "pass" for a stateful strategy that never overrides it.
- **This module is distinct from T-P2-04's `application/backtest
  /loop.py::BacktestStrategy`.** That Protocol was explicitly scoped, at
  the time it was built, as "not the full Strategy port — T-P2-07's
  job"; it is left completely unmodified here. Adapting a real
  `Strategy` into something `run_backtest` can drive is a future task's
  concern — nothing in T-P2-07's own four acceptance criteria asks for
  that adapter.
- **Unexpected params (not named in the schema) are rejected, not
  silently ignored.** A schema defines the valid shape of a strategy's
  configuration; treating anything outside it as invalid matches this
  codebase's established "trust nothing, validate everything" posture
  (e.g. `Money`'s currency checks, `FeeSchedule`'s tier validation).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from domain.candle import Candle
from domain.fill import Fill
from domain.ports import Clock, MarketDataView


class InvalidStrategyParams(ValueError):  # noqa: N818 — name fixed by docs/TASKS.md T-P2-07
    """Raised when params given to `StrategyRegistry.instantiate()` (or
    directly to `validate_params`) fail validation against a strategy
    class's own `params_schema()`."""


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One parameter's type and optional numeric bounds — DATABASE.md's
    own "typed bounds" framing for `Strategy.params_schema`."""

    type: type[object]
    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Runtime collaborators handed to a strategy at `on_start` — never
    at construction time. ARCHITECTURE.md §4.7: the injected `Clock` is
    the only sanctioned source of "now" a strategy may ever consult."""

    clock: Clock


def validate_params(schema: Mapping[str, ParamSpec], params: Mapping[str, object]) -> None:
    """Validates `params` against `schema`, raising `InvalidStrategyParams`
    on any mismatch: a param not named in the schema, a missing required
    param, a wrong type, or a value outside its spec's numeric bounds.
    """
    extra = set(params) - set(schema)
    if extra:
        raise InvalidStrategyParams(f"Unexpected params not in schema: {sorted(extra)}")
    for name, spec in schema.items():
        if name not in params:
            raise InvalidStrategyParams(f"Missing required param {name!r}")
        value = params[name]
        if isinstance(value, bool) and spec.type is not bool:
            raise InvalidStrategyParams(f"Param {name!r} must be {spec.type.__name__}, got bool")
        if not isinstance(value, spec.type):
            raise InvalidStrategyParams(
                f"Param {name!r} must be {spec.type.__name__}, got {type(value).__name__}"
            )
        if spec.minimum is not None and value < spec.minimum:  # type: ignore[operator]
            raise InvalidStrategyParams(f"Param {name!r}={value!r} is below minimum {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:  # type: ignore[operator]
            raise InvalidStrategyParams(f"Param {name!r}={value!r} is above maximum {spec.maximum}")


class Strategy(ABC):
    """The contract every trading strategy implements — driven identically
    in backtest and live (ARCHITECTURE.md's "no forks" principle)."""

    def __init__(self, *, params: Mapping[str, object]) -> None:
        self._params = params

    @property
    def params(self) -> Mapping[str, object]:
        return self._params

    @classmethod
    @abstractmethod
    def params_schema(cls) -> Mapping[str, ParamSpec]:
        """This strategy class's own parameter shape — queryable before
        any instance exists, so `StrategyRegistry` can validate params
        prior to construction."""
        ...

    @abstractmethod
    def subscriptions(self) -> Sequence[tuple[str, str]]:
        """The `(symbol, timeframe)` pairs this strategy wants bars for."""
        ...

    @abstractmethod
    def warmup_period(self) -> int:
        """Bars to receive, but never signal on, before going live."""
        ...

    @abstractmethod
    def on_start(self, context: StrategyContext) -> None:
        """Called once before the first `on_data`, with runtime
        collaborators (the injected `Clock`) — never a wall clock."""
        ...

    @abstractmethod
    def on_data(self, event: Candle, view: MarketDataView) -> object | None:
        """Called once per closed bar. Returns a signal, or `None`."""
        ...

    @abstractmethod
    def on_fill(self, fill: Fill) -> None:
        """Called once per fill against an order this strategy placed."""
        ...

    @abstractmethod
    def on_stop(self) -> None:
        """Called once when the strategy is being shut down."""
        ...

    @abstractmethod
    def state(self) -> Mapping[str, object]:
        """This strategy's own serializable internal state, sufficient
        for `restore()` to resume producing identical future signals."""
        ...

    @abstractmethod
    def restore(self, state: Mapping[str, object]) -> None:
        """Restores internal state previously returned by `state()`."""
        ...
