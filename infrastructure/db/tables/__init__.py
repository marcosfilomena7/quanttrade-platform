"""SQLAlchemy Core table definitions for the baseline schema (TASKS.md T-P0-11)
plus later, explicitly-required additions (T-P1-04's backfill checkpoint,
T-P1-05's data quality event log).

26 tables: the 23 entities in `docs/DATABASE.md`, plus `trade_tick`
(T-P0-11's 24th — see `market_data.py`'s docstring for why), plus
`candle_backfill_checkpoint` (T-P1-04's 25th — see `backfill.py`'s
docstring), plus `data_quality_event` (T-P1-05's 26th — see
`data_quality.py`'s docstring). Grouped into modules that mirror
DATABASE.md's own A–G subsystem sections, plus `backfill` and
`data_quality` for the additions that aren't DATABASE.md entities at all.
Every module import below is required, not decorative — it is what
registers each module's tables onto the single shared `metadata` object
(see `_metadata.py`) that Alembic's `env.py` uses as `target_metadata`.

This is schema only: `sa.Table` objects, not an ORM mapping onto the
`domain/` aggregates. Repository/persistence code that maps `domain.Order`
etc. to and from these rows is a separate, later concern.
"""

from __future__ import annotations

from infrastructure.db.tables import (  # noqa: F401
    audit,
    backfill,
    backtest,
    data_quality,
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
    "backfill",
    "data_quality",
]
