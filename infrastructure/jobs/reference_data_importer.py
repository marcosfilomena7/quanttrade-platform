"""Reference Data Importer (TASKS.md T-P1-02).

"Implement a job that calls Binance `GET /api/v3/exchangeInfo`, maps the
response to `Instrument` domain objects, and upserts into the
`instrument` table via `SQLAlchemy 2.0 Core`. Detect and alert on
tick-size or lot-size changes for existing instruments (silent spec
changes cause rejected orders). Schedule to run daily."

Design decisions, and why:

- **`venue_id` is a required parameter, not looked up or created here.**
  Nothing in T-P1-02's description or acceptance criteria makes this job
  responsible for registering a venue — DATABASE.md's `Venue` table is a
  separate concern with no seeding task defined anywhere in Phase 0/1.
  The caller (composition root) is expected to already know which
  `venue.id` row corresponds to Binance; this job only synchronizes that
  venue's instruments.
- **Upsert targets `(venue_id, symbol)`** — the exact unique constraint
  `uq_instrument_venue_id_symbol` (T-P0-11) already enforces. This is
  what makes running the importer twice idempotent: a second run with
  identical data lands on the same row via `ON CONFLICT`, never a
  duplicate.
- **`listed_at` is preserved, never overwritten, for an existing row.**
  Binance's exchangeInfo carries no "first listed" timestamp; on first
  sight of a symbol this job has no better source of truth than "now",
  but once a row exists, `listed_at` is that instrument's real history
  and must never be reset by a later sync.
- **`id` is preserved for an existing row, generated fresh only for a new
  one.** `domain.Instrument.id` is a required field with no default, so
  building a domain object at all requires deciding an id up front — the
  existing row's id if there is one (looked up first), a fresh `uuid4()`
  otherwise.
- **`max_order_size` is never set by this job.** It exists as a nullable
  column (T-P0-11) and on `domain.Instrument` (T-P0-04) it does not exist
  as a field at all; DATABASE.md gives no derivation rule for it from
  exchangeInfo, so this job leaves it untouched rather than guessing.
- **Only "TRADING" maps to `"trading"`; every other Binance status maps
  to `"halted"`, never `"delisted"`.** A symbol present in this response
  at all is, definitionally, not delisted. Detecting delisting requires
  noticing a *previously known* symbol's absence from a later response —
  a different job, not named by any T-P1-02 acceptance criterion.
- **Change detection is scoped to tick_size and lot_size**, matching the
  literal task description ("Detect and alert on tick-size or lot-size
  changes"). `min_notional` is still synced and stored correctly (its own
  acceptance criterion is about `NUMERIC`/`Decimal` storage, not change
  alerting), just not compared for drift.
- **A missing required filter skips that one symbol (logged, counted),
  never crashes the whole batch.** One malformed entry in a
  multi-hundred-symbol response should not prevent every other symbol
  from being synchronized.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domain.instrument import Instrument, InstrumentStatus, Spot
from infrastructure.db.tables.reference import instrument as instrument_table
from infrastructure.observability.metrics import reference_data_changed
from infrastructure.venues.binance.client import BinanceRestClient
from infrastructure.venues.binance.models import ExchangeSymbol

_NOTIONAL_FILTER_TYPES = ("MIN_NOTIONAL", "NOTIONAL")
_CHANGE_DETECTED_FIELDS = ("tick_size", "lot_size")

_logger = structlog.get_logger()


class ReferenceDataError(Exception):
    """Raised when a symbol's exchangeInfo entry is missing a required filter."""


@dataclass(frozen=True)
class ReferenceDataImportResult:
    """Summary of one `import_reference_data` run."""

    inserted: int
    updated: int
    changed_fields: int
    skipped: int


def extract_trading_rules(symbol: ExchangeSymbol) -> tuple[Decimal, Decimal, Decimal]:
    """Pull `(tick_size, lot_size, min_notional)` out of a symbol's `filters`.

    Raises `ReferenceDataError` if any of the three is absent — a
    malformed or unexpected exchangeInfo entry is skipped by the caller,
    never silently defaulted or guessed at.
    """
    tick_size: Decimal | None = None
    lot_size: Decimal | None = None
    min_notional: Decimal | None = None

    for f in symbol.filters:
        if f.filter_type == "PRICE_FILTER" and f.tick_size is not None:
            tick_size = f.tick_size
        elif f.filter_type == "LOT_SIZE" and f.step_size is not None:
            lot_size = f.step_size
        elif f.filter_type in _NOTIONAL_FILTER_TYPES and f.min_notional is not None:
            min_notional = f.min_notional

    missing = [
        name
        for name, value in (
            ("PRICE_FILTER.tickSize", tick_size),
            ("LOT_SIZE.stepSize", lot_size),
            ("MIN_NOTIONAL/NOTIONAL.minNotional", min_notional),
        )
        if value is None
    ]
    if missing:
        raise ReferenceDataError(
            f"{symbol.symbol} is missing required filter field(s): {', '.join(missing)}"
        )

    assert tick_size is not None
    assert lot_size is not None
    assert min_notional is not None
    return tick_size, lot_size, min_notional


def map_status(binance_status: str) -> InstrumentStatus:
    """Binance's exchangeInfo `status` -> our closed `InstrumentStatus`.

    See this module's docstring for why every non-"TRADING" status maps
    to `"halted"`, and none ever maps to `"delisted"`.
    """
    return "trading" if binance_status == "TRADING" else "halted"


def build_instrument(
    symbol: ExchangeSymbol, *, instrument_id: UUID, venue_id: UUID
) -> Instrument:
    """Map one Binance exchangeInfo symbol entry to an `Instrument` domain object."""
    tick_size, lot_size, min_notional = extract_trading_rules(symbol)
    return Instrument(
        id=instrument_id,
        venue_id=venue_id,
        symbol=symbol.symbol,
        base_currency=symbol.base_asset,
        quote_currency=symbol.quote_asset,
        tick_size=tick_size,
        lot_size=lot_size,
        min_notional=min_notional,
        status=map_status(symbol.status),
        details=Spot(),
    )


def import_reference_data(
    *,
    rest_client: BinanceRestClient,
    conn: sa.Connection,
    venue_id: UUID,
    venue_name: str = "binance",
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ReferenceDataImportResult:
    """Fetch Binance's exchangeInfo and upsert it into the `instrument` table.

    Idempotent on `(venue_id, symbol)`: running this twice with identical
    upstream data updates the same rows rather than inserting duplicates.
    Detects a tick-size or lot-size change on any already-known
    instrument, logging a `reference_data_changed` warning and
    incrementing the `reference_data_changed` metric for each changed
    field. Commits the transaction on the given connection before
    returning.
    """
    exchange_info = rest_client.get_exchange_info()
    inserted = updated = changed_fields = skipped = 0
    ts = now()

    for symbol in exchange_info.symbols:
        existing = conn.execute(
            sa.select(
                instrument_table.c.id,
                instrument_table.c.tick_size,
                instrument_table.c.lot_size,
                instrument_table.c.listed_at,
            ).where(
                instrument_table.c.venue_id == venue_id,
                instrument_table.c.symbol == symbol.symbol,
            )
        ).one_or_none()

        try:
            instrument_id = existing.id if existing is not None else uuid4()
            instrument = build_instrument(symbol, instrument_id=instrument_id, venue_id=venue_id)
        except ReferenceDataError as exc:
            _logger.warning("reference_data_import_skipped", venue=venue_name, reason=str(exc))
            skipped += 1
            continue

        if existing is not None:
            new_values = {"tick_size": instrument.tick_size, "lot_size": instrument.lot_size}
            for field in _CHANGE_DETECTED_FIELDS:
                old_value = getattr(existing, field)
                new_value = new_values[field]
                if old_value != new_value:
                    _logger.warning(
                        "reference_data_changed",
                        venue=venue_name,
                        symbol=instrument.symbol,
                        field=field,
                        old=str(old_value),
                        new=str(new_value),
                    )
                    reference_data_changed.labels(
                        venue=venue_name, symbol=instrument.symbol, field=field
                    ).inc()
                    changed_fields += 1

        listed_at = existing.listed_at if existing is not None else ts
        stmt = pg_insert(instrument_table).values(
            id=instrument.id,
            venue_id=instrument.venue_id,
            symbol=instrument.symbol,
            asset_class=instrument.asset_class,
            base_currency=instrument.base_currency,
            quote_currency=instrument.quote_currency,
            tick_size=instrument.tick_size,
            lot_size=instrument.lot_size,
            min_notional=instrument.min_notional,
            status=instrument.status,
            listed_at=listed_at,
            updated_at=ts,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["venue_id", "symbol"],
            set_={
                "asset_class": stmt.excluded.asset_class,
                "base_currency": stmt.excluded.base_currency,
                "quote_currency": stmt.excluded.quote_currency,
                "tick_size": stmt.excluded.tick_size,
                "lot_size": stmt.excluded.lot_size,
                "min_notional": stmt.excluded.min_notional,
                "status": stmt.excluded.status,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        conn.execute(stmt)

        if existing is None:
            inserted += 1
        else:
            updated += 1

    conn.commit()
    return ReferenceDataImportResult(
        inserted=inserted, updated=updated, changed_fields=changed_fields, skipped=skipped
    )
