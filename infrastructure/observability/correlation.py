"""CorrelationContext — threads signal_id -> intent_id -> order_id -> fill_id
through every structured log record automatically.

ARCHITECTURE.md §3.7: "Logs — structured JSON, correlation ID threading
`signal -> intent -> order -> fill`." Built on structlog's own
`contextvars` helpers (`bind_contextvars` / `reset_contextvars` /
`get_contextvars`) rather than raw `contextvars.ContextVar`s: that is the
exact mechanism `logging.configure_logging`'s `merge_contextvars`
processor already reads from, so binding through this class requires no
extra wiring on the logging side, and it works correctly across `await`
points because `contextvars` (unlike thread-locals) is asyncio-aware.

Nestable by construction: binding `intent_id` inside a block that already
bound `signal_id` leaves `signal_id` bound too — `bind_contextvars` only
touches the keys it's given, and each instance's `__exit__` resets only
the tokens *it* created. This matches the natural signal -> intent ->
order -> fill chain, where each stage adds one more identifier without
discarding the ones established upstream.
"""

from __future__ import annotations

import contextvars
from typing import Any

import structlog.contextvars


class CorrelationContext:
    """Bind any subset of the four correlation ids for a `with` block's duration."""

    def __init__(
        self,
        *,
        signal_id: str | None = None,
        intent_id: str | None = None,
        order_id: str | None = None,
        fill_id: str | None = None,
    ) -> None:
        self._ids: dict[str, str] = {
            key: value
            for key, value in (
                ("signal_id", signal_id),
                ("intent_id", intent_id),
                ("order_id", order_id),
                ("fill_id", fill_id),
            )
            if value is not None
        }
        self._tokens: dict[str, contextvars.Token[Any]] = {}

    def __enter__(self) -> CorrelationContext:
        self._tokens = dict(structlog.contextvars.bind_contextvars(**self._ids))
        return self

    def __exit__(self, *exc_info: object) -> None:
        structlog.contextvars.reset_contextvars(**self._tokens)

    @staticmethod
    def current() -> dict[str, str]:
        """Every correlation id currently bound, from any enclosing scope."""
        return dict(structlog.contextvars.get_contextvars())
