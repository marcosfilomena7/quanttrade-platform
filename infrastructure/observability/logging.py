"""Structured logging: structlog configured to emit one JSON line per event to stdout.

ARCHITECTURE.md §3.7: "Logs — structured JSON, correlation ID threading
`signal -> intent -> order -> fill`." TASKS.md T-P0-08's acceptance
criterion is precise: "No log line is emitted without a `level`, `event`,
and `ts` field." The processor chain below guarantees all three on every
call, regardless of what the caller logs, and merges in whatever
correlation ids `correlation.CorrelationContext` currently has bound.

Call `configure_logging()` once, at process startup, before any logger is
obtained via `structlog.get_logger()`.
"""

from __future__ import annotations

import logging

import structlog


def configure_logging() -> None:
    """Configure structlog to render every log call as one JSON line to stdout."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
