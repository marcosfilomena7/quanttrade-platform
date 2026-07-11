"""Clock port — the only source of time a deterministic domain object may use.

ARCHITECTURE.md §4.7: "The strategy cannot access a wall clock. It
receives time from the injected `Clock` port. A strategy that calls
`datetime.now()` is nondeterministic and will produce a different backtest
on every run." `RealClock` and `SimulatedClock` (T-P2-01) both implement
this same shape; only `now()` is common to both — `SimulatedClock`'s
`advance_to()` is specific to that concrete class, not part of the port.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Returns the current time as understood by whoever holds this clock.

    Live: wall-clock time. Backtest: the simulated present. Callers must
    never know or care which.
    """

    def now(self) -> datetime: ...
