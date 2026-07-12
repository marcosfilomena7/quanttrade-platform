"""SQLAlchemy Core table definitions for the baseline schema (TASKS.md T-P0-11).

24 tables: the 23 entities in `docs/DATABASE.md`, plus `trade_tick` (see
`market_data.py`'s docstring for why). Grouped into modules that mirror
DATABASE.md's own A–G subsystem sections. Every module import below is
required, not decorative — it is what registers each module's tables onto
the single shared `metadata` object (see `_metadata.py`) that Alembic's
`env.py` uses as `target_metadata`.

This is schema only: `sa.Table` objects, not an ORM mapping onto the
`domain/` aggregates. Repository/persistence code that maps `domain.Order`
etc. to and from these rows is a separate, later concern.
"""

from __future__ import annotations

from infrastructure.db.tables import (  # noqa: F401
    audit,
    backtest,
    execution,
    market_data,
    portfolio,
    reference,
    risk,
    strategy,
)
from infrastructure.db.tables._metadata import metadata

__all__ = [
    "metadata",
    "reference",
    "market_data",
    "strategy",
    "risk",
    "execution",
    "portfolio",
    "audit",
    "backtest",
]
