"""Historical OHLCV Backfill Job (TASKS.md T-P1-04).

"Implement a backfill job: given a symbol and date range, fetch OHLCV
data from Binance REST in rate-limit-safe chunks (1000 bars per
request), upsert into the `candle` hypertable on `(instrument_id,
open_time)`, log progress to a checkpoint table so interrupted runs
resume from the last successful chunk. Run gap detection after each
chunk and alert on detected gaps."

Design decisions, and why:

- **The upsert conflict target is `(instrument_id, interval, open_time)`,
  not just `(instrument_id, open_time)`.** The task's own shorthand (and
  ARCHITECTURE.md §11.3's identical phrasing) omits `interval`, but
  `candle`'s actual primary key (T-P0-11, per DATABASE.md: "PK:
  (instrument_id, interval, open_time)") includes it — necessarily,
  since the same instrument has one row per interval at the same wall
  clock instant (a `1m` bar and a `1h` bar can share an `open_time`).
  A single backfill call fixes `interval` as a constant, so the two
  phrasings agree in practice; the actual `ON CONFLICT` clause must
  still name the real composite constraint.
- **Progress is checkpointed per chunk, in the same transaction as that
  chunk's candle upserts.** Both are committed together
  (`conn.commit()` once per chunk) — a crash before that commit rolls
  back the whole chunk (nothing partially applied); a crash after it
  leaves a fully consistent, resumable state. This is what makes
  "killing the process mid-backfill and restarting resumes from the
  last checkpoint" true without any special crash-recovery code: the
  next call simply re-reads the checkpoint and continues.
- **A checkpoint already marked `"completed"` short-circuits
  immediately** — no Binance call, no candle upsert. This is what makes
  "backfilling the same range twice produces no duplicates" not just
  true but *cheap*: a second call for a finished range does nothing at
  all, rather than re-fetching and idempotently re-upserting identical
  data (which would also be correct, just wasteful).
- **Gap detection compares consecutive bars' `open_time` deltas against
  the interval's expected step**, including the boundary between the
  last checkpointed bar (from a prior call/chunk) and the first bar of
  the current chunk — so a gap that happens to straddle a restart is
  still caught. A detected gap is a `structlog` warning (`alert`, per
  the task's own word) — no new Prometheus metric was added, since none
  of T-P1-04's four acceptance criteria name one (unlike T-P1-02's
  explicit `reference_data_changed` metric requirement).
- **`is_closed` is computed per bar, not assumed `True`.** DATABASE.md:
  "a partial candle is the #1 lookahead vector — must be explicit, not
  inferred." A backfill range whose upper bound is close to "now" can
  have its last bar still forming; `is_closed` is `True` only once the
  bar's own window (`open_time + interval`) has fully elapsed as of the
  injected `now`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from infrastructure.db.tables.backfill import candle_backfill_checkpoint
from infrastructure.db.tables.market_data import candle as candle_table
from infrastructure.venues.binance.client import BinanceRestClient
from infrastructure.venues.binance.models import Kline

_DEFAULT_SOURCE = "binance_rest_backfill"

_INTERVAL_TIMEDELTAS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "1w": timedelta(weeks=1),
}

_logger = structlog.get_logger()


def interval_to_timedelta(interval: str) -> timedelta:
    """The wall-clock step one bar of `interval` spans."""
    try:
        return _INTERVAL_TIMEDELTAS[interval]
    except KeyError:
        raise ValueError(f"unsupported candle interval: {interval!r}") from None


def find_gaps(
    open_times: Sequence[datetime], *, step: timedelta, previous_open_time: datetime | None
) -> list[tuple[datetime, datetime]]:
    """Return `(before, after)` pairs wherever two consecutive bars
    (including the boundary against `previous_open_time`, if given) are
    more than one `step` apart."""
    gaps: list[tuple[datetime, datetime]] = []
    prior = previous_open_time
    for open_time in open_times:
        if prior is not None and open_time - prior > step:
            gaps.append((prior, open_time))
        prior = open_time
    return gaps


@dataclass(frozen=True)
class BackfillResult:
    """Summary of one `backfill_candles` call."""

    upserted: int
    gaps_detected: int
    resumed_from: datetime
    completed: bool


def _load_checkpoint(
    conn: sa.Connection,
    *,
    venue_id: UUID,
    instrument_id: UUID,
    interval: str,
    range_start: datetime,
    range_end: datetime,
) -> sa.Row[tuple[datetime | None, str]] | None:
    return conn.execute(
        sa.select(
            candle_backfill_checkpoint.c.last_completed_open_time,
            candle_backfill_checkpoint.c.status,
        ).where(
            candle_backfill_checkpoint.c.venue_id == venue_id,
            candle_backfill_checkpoint.c.instrument_id == instrument_id,
            candle_backfill_checkpoint.c.interval == interval,
            candle_backfill_checkpoint.c.range_start == range_start,
            candle_backfill_checkpoint.c.range_end == range_end,
        )
    ).one_or_none()


def _save_checkpoint(
    conn: sa.Connection,
    *,
    venue_id: UUID,
    instrument_id: UUID,
    interval: str,
    range_start: datetime,
    range_end: datetime,
    last_completed_open_time: datetime,
    status: str,
    ts: datetime,
) -> None:
    stmt = pg_insert(candle_backfill_checkpoint).values(
        venue_id=venue_id,
        instrument_id=instrument_id,
        interval=interval,
        range_start=range_start,
        range_end=range_end,
        last_completed_open_time=last_completed_open_time,
        status=status,
        updated_at=ts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["venue_id", "instrument_id", "interval", "range_start", "range_end"],
        set_={
            "last_completed_open_time": stmt.excluded.last_completed_open_time,
            "status": stmt.excluded.status,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    conn.execute(stmt)


def _upsert_candles(
    conn: sa.Connection,
    *,
    instrument_id: UUID,
    interval: str,
    klines: Sequence[Kline],
    source: str,
    now: datetime,
) -> None:
    step = interval_to_timedelta(interval)
    rows = [
        {
            "instrument_id": instrument_id,
            "interval": interval,
            "open_time": k.open_time,
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
            "trade_count": k.trade_count,
            "is_closed": k.open_time + step <= now,
            "source": source,
        }
        for k in klines
    ]
    stmt = pg_insert(candle_table).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "interval", "open_time"],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "trade_count": stmt.excluded.trade_count,
            "is_closed": stmt.excluded.is_closed,
            "source": stmt.excluded.source,
        },
    )
    conn.execute(stmt)


def backfill_candles(
    *,
    rest_client: BinanceRestClient,
    conn: sa.Connection,
    venue_id: UUID,
    instrument_id: UUID,
    symbol: str,
    interval: str,
    range_start: datetime,
    range_end: datetime,
    chunk_size: int = 1000,
    source: str = _DEFAULT_SOURCE,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BackfillResult:
    """Backfill `[range_start, range_end]` of `interval` candles for
    `symbol`, resuming from `candle_backfill_checkpoint` if a prior call
    for this exact `(venue_id, instrument_id, interval, range_start,
    range_end)` was interrupted.
    """
    step = interval_to_timedelta(interval)
    ts = now()

    checkpoint = _load_checkpoint(
        conn,
        venue_id=venue_id,
        instrument_id=instrument_id,
        interval=interval,
        range_start=range_start,
        range_end=range_end,
    )

    if checkpoint is not None and checkpoint.status == "completed":
        return BackfillResult(
            upserted=0, gaps_detected=0, resumed_from=range_start, completed=True
        )

    fetch_from = (
        checkpoint.last_completed_open_time + step
        if checkpoint is not None and checkpoint.last_completed_open_time is not None
        else range_start
    )
    resumed_from = fetch_from
    previous_open_time = checkpoint.last_completed_open_time if checkpoint is not None else None

    total_upserted = 0
    total_gaps = 0

    while fetch_from <= range_end:
        klines = rest_client.get_klines(
            symbol=symbol,
            interval=interval,
            start_time=fetch_from,
            end_time=range_end,
            limit=chunk_size,
        )
        if not klines:
            break

        gaps = find_gaps(
            [k.open_time for k in klines], step=step, previous_open_time=previous_open_time
        )
        for gap_start, gap_end in gaps:
            _logger.warning(
                "candle_gap_detected",
                symbol=symbol,
                interval=interval,
                gap_start=gap_start.isoformat(),
                gap_end=gap_end.isoformat(),
            )
        total_gaps += len(gaps)

        _upsert_candles(
            conn,
            instrument_id=instrument_id,
            interval=interval,
            klines=klines,
            source=source,
            now=ts,
        )
        total_upserted += len(klines)

        last_open_time = klines[-1].open_time
        previous_open_time = last_open_time
        fetch_from = last_open_time + step
        is_last_chunk = len(klines) < chunk_size or fetch_from > range_end

        _save_checkpoint(
            conn,
            venue_id=venue_id,
            instrument_id=instrument_id,
            interval=interval,
            range_start=range_start,
            range_end=range_end,
            last_completed_open_time=last_open_time,
            status="completed" if is_last_chunk else "in_progress",
            ts=ts,
        )
        conn.commit()

        if len(klines) < chunk_size:
            break

    return BackfillResult(
        upserted=total_upserted,
        gaps_detected=total_gaps,
        resumed_from=resumed_from,
        completed=fetch_from > range_end,
    )
