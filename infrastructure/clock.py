"""Clock port implementations — RealClock (live) and SimulatedClock
(backtest) (TASKS.md T-P2-01).

"Implement `SimulatedClock` as the `Clock` port. The clock advances only
when `advance_to(ts: datetime)` is called by the backtest loop. `now()`
returns the current simulated time. The clock must be injected —
strategies never call `datetime.now()` directly (enforced by the lint
rule from T-P0-01). A `RealClock` implementation also provided for live
use."

Design decisions, and why:

- **Both classes live here, in `infrastructure/`, not `domain/`.**
  ARCHITECTURE.md §9 names `RealClock` and `SimulatedClock` explicitly
  as the "swapped adapters" that differ between live and backtest,
  alongside `WebSocketFeed`/`HistoricalFeed` and
  `BinanceAdapter`/`SimulatedVenue` — all infrastructure-layer concrete
  implementations of a domain port (`Clock`, `domain/ports/clock.py`,
  T-P0-07), exactly like every other port/adapter pair already
  established in this codebase (e.g.
  `DatasetVersionRepository`/`PostgresDatasetVersionRepository`,
  T-P1-12). Neither class is referenced as implementing the `Clock`
  `Protocol` via an explicit base class or a typed assignment in this
  file, matching this codebase's existing convention for concrete
  adapters (structural typing only; conformance is proven by
  `isinstance()` checks in tests, not by importing the port here).
- **No new custom lint rule.** T-P2-01's own description says the
  clock-injection requirement is "(enforced by the lint rule from
  T-P0-01)" — but T-P0-01's own acceptance criteria (docs/TASKS.md)
  name only the import-linter's domain/infrastructure boundary check;
  no "ban `datetime.now()` in domain/application" checker exists
  anywhere in this repo's `scripts/` (only `check_no_float.py` and
  `check_no_env_secrets.py` exist, from earlier tasks). This is a
  forward/optimistic reference in the task's own prose, not a literal,
  testable requirement of *this* task — none of T-P2-01's five
  acceptance criteria mention a lint rule; all five are statements
  about `SimulatedClock`/`RealClock`'s own runtime behavior. Adding a
  new custom AST checker now would be scope beyond what's asked and
  beyond what any acceptance criterion tests.
- **`SimulatedClock` starts uninitialized — no constructor parameter for
  an initial timestamp.** AC2 requires `now()` to raise
  `ClockNotInitialized` "before any `advance_to()`" — an initial-time
  constructor argument would make that state unreachable for a normally
  constructed clock, contradicting the literal acceptance criterion.
- **`advance_to()` rejects only a strictly earlier timestamp, not an
  equal one.** AC3's own wording is "a time earlier than current," not
  "earlier than or equal to." A backtest merging multiple symbols/
  timeframes (T-P2-03's own job) can legitimately have several events
  share the exact same timestamp; advancing to the *same* instant
  repeatedly must not be treated as a regression.
- **`ClockNotInitialized`/`ClockRegressionError` share a `ClockError`
  base**, mirroring this codebase's established exception-hierarchy
  convention (e.g. `BinanceAPIError` in
  `infrastructure/venues/binance/errors.py`) so a caller can catch
  either uniformly with `except ClockError:`, without changing either
  acceptance criterion's own exact type name.
- **No timezone-awareness validation on `advance_to(ts)`.** Every other
  domain timestamp in this codebase is timezone-aware by convention,
  but — unlike ARCHITECTURE.md §3.3's explicit, named, lint-and-type-
  checker-enforced float ban — no equally explicit rule requires
  `SimulatedClock` itself to reject naive datetimes, and none of
  T-P2-01's five acceptance criteria test this. Adding such validation
  now would be unrequested scope; the caller (T-P2-04's backtest loop)
  is responsible for supplying timezone-aware timestamps, the same way
  every other domain type in this codebase already assumes of its own
  callers.
"""

from __future__ import annotations

from datetime import UTC, datetime


class ClockError(Exception):
    """Base class for every error a `Clock` port implementation raises."""


class ClockNotInitialized(ClockError):  # noqa: N818 — name fixed by docs/TASKS.md T-P2-01
    """Raised by `SimulatedClock.now()` before `advance_to()` has ever
    been called — there is no simulated "current time" yet."""


class ClockRegressionError(ClockError):
    """Raised by `SimulatedClock.advance_to()` when `ts` is strictly
    earlier than the clock's current simulated time."""


class RealClock:
    """`Clock` port implementation for live trading: wall-clock time,
    always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class SimulatedClock:
    """`Clock` port implementation for backtesting: time advances only
    when `advance_to(ts)` is called by the backtest loop — never on its
    own, and never via a wall clock.
    """

    def __init__(self) -> None:
        self._current: datetime | None = None

    def now(self) -> datetime:
        if self._current is None:
            raise ClockNotInitialized("SimulatedClock.now() called before any advance_to()")
        return self._current

    def advance_to(self, ts: datetime) -> None:
        if self._current is not None and ts < self._current:
            raise ClockRegressionError(
                f"SimulatedClock cannot move backward: current={self._current!r}, "
                f"requested={ts!r}"
            )
        self._current = ts
