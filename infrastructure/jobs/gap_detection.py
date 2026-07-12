"""Gap Detection and Auto-Backfill Scheduler (TASKS.md T-P1-06).

"Implement a gap detector: scan the `candle` table for missing
`open_time` intervals within trading windows (for crypto: 24/7, so every
minute must have a row). Automatically trigger the backfill job for
detected gaps. Run on startup and on a nightly schedule. Log all
detected gaps to `data_quality_event`."

Design decisions, and why:

- **Exhaustive expected-vs-actual diff, not boundary detection.**
  T-P1-05's `validate_candle_sequence` already has a `missing_interval`
  check, but it only detects the *boundary* between two existing bars
  that are further apart than expected — it never enumerates individual
  missing timestamps. T-P1-06's acceptance criterion ("identifies all 5
  missing intervals") requires knowing exactly which open_times are
  absent, so this module independently enumerates every expected
  interval-aligned slot in the window and diffs it against what's
  actually stored. This is a different algorithm for a different
  question, not a duplication of T-P1-05's check — neither module is
  modified to call the other.
- **24/7 trading window, no calendar logic.** TASKS.md is explicit:
  "for crypto: 24/7, so every minute must have a row." Every
  `interval`-aligned slot between `window_start` and `window_end` is
  expected, with no market-hours/holiday exceptions — that complexity
  belongs to a future equities asset class (§3.8.1's Phase 9 concern),
  not this one.
- **No new table or migration.** "Log all detected gaps to
  `data_quality_event`" names the exact table T-P1-05 already created;
  gaps reuse `check_name="missing_interval"` (the same name T-P1-05's
  own sequence check uses — both report the same *kind* of finding,
  just via different detection mechanisms) with `severity="flagged"`
  (a gap is retained/backfilled, never a reason to quarantine anything).
- **Auto-backfill targets each gap's own narrow range, not the original
  scan window.** T-P1-04's `backfill_candles` (unmodified) keys its
  resumability checkpoint on the exact `(venue_id, instrument_id,
  interval, range_start, range_end)` tuple; calling it again with the
  *same* wide range that already completed would short-circuit
  immediately and backfill nothing, even though rows are now missing
  inside it. Calling it once per detected gap, scoped to exactly
  `[gap.start, gap.end]`, is a range T-P1-04 has never checkpointed
  before, so it genuinely re-fetches — working with T-P1-04's existing
  idempotency design instead of needing to change it.
- **Maintenance-window gaps are classified but not auto-backfilled.**
  "Not a data error" means there is nothing to fix: a real exchange
  maintenance window has no trades to fetch, so attempting a backfill
  would be a wasted venue call that returns nothing. Classification is
  carried in the `data_quality_event.details` JSONB
  (`"classification": "maintenance"` vs. `"unexplained"`), not a new
  table or a new `severity` enum value — DATABASE.md/ARCHITECTURE.md
  define no "exchange maintenance window" concept anywhere, so known
  windows are supplied by the caller (an operational/config concern),
  not looked up from a schema this task would otherwise have to invent.
- **"Run on startup and on a nightly schedule" is an operational
  concern**, exactly as for T-P1-02/03/04's "schedule to run daily" —
  no scheduler library is introduced; this module implements what the
  job does once invoked, external to this codebase's runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from infrastructure.db.tables.data_quality import data_quality_event
from infrastructure.db.tables.market_data import candle as candle_table
from infrastructure.jobs.ohlcv_backfill_job import backfill_candles, interval_to_timedelta
from infrastructure.observability.metrics import data_quality_violations_total
from infrastructure.venues.binance.client import BinanceRestClient

GapClassification = Literal["unexplained", "maintenance"]


@dataclass(frozen=True)
class MaintenanceWindow:
    """A known, pre-announced exchange maintenance window. Supplied by
    the caller — this platform has no schema for discovering these on
    its own."""

    start: datetime
    end: datetime
    reason: str


@dataclass(frozen=True)
class DetectedGap:
    """One contiguous run of missing `open_time` slots."""

    start: datetime
    end: datetime
    missing_open_times: list[datetime]
    classification: GapClassification


@dataclass(frozen=True)
class GapScanResult:
    """The outcome of one `detect_gaps` scan."""

    gaps: list[DetectedGap]

    @property
    def missing_count(self) -> int:
        return sum(len(gap.missing_open_times) for gap in self.gaps)


def expected_open_times(
    window_start: datetime, window_end: datetime, step: timedelta
) -> list[datetime]:
    """Every interval-aligned `open_time` a 24/7 crypto market is
    expected to have a candle for, from `window_start` to `window_end`
    inclusive."""
    times: list[datetime] = []
    t = window_start
    while t <= window_end:
        times.append(t)
        t += step
    return times


def _classify(
    gap_start: datetime, gap_end: datetime, maintenance_windows: Sequence[MaintenanceWindow]
) -> GapClassification:
    for window in maintenance_windows:
        if gap_start <= window.end and gap_end >= window.start:
            return "maintenance"
    return "unexplained"


def gaps_from_expected_and_actual(
    expected: Sequence[datetime],
    actual: set[datetime],
    *,
    step: timedelta,
    maintenance_windows: Sequence[MaintenanceWindow] = (),
) -> GapScanResult:
    """The pure core of gap detection: diff `expected` against `actual`,
    group the missing timestamps into contiguous runs, and classify each
    run. Deliberately separated from `detect_gaps`'s database query so
    this logic is fully unit-testable with no connection at all.
    """
    missing = sorted(t for t in expected if t not in actual)
    if not missing:
        return GapScanResult(gaps=[])

    runs: list[list[datetime]] = [[missing[0]]]
    for open_time in missing[1:]:
        if open_time - runs[-1][-1] == step:
            runs[-1].append(open_time)
        else:
            runs.append([open_time])

    gaps = [
        DetectedGap(
            start=run[0],
            end=run[-1],
            missing_open_times=run,
            classification=_classify(run[0], run[-1], maintenance_windows),
        )
        for run in runs
    ]
    return GapScanResult(gaps=gaps)


def detect_gaps(
    conn: sa.Connection,
    *,
    instrument_id: UUID,
    interval: str,
    window_start: datetime,
    window_end: datetime,
    maintenance_windows: Sequence[MaintenanceWindow] = (),
) -> GapScanResult:
    """Scan `[window_start, window_end]` for missing `candle` rows.

    Every missing `open_time` is found (an exhaustive expected-vs-actual
    diff, not just gap boundaries), then grouped into contiguous runs and
    classified against `maintenance_windows`. Read-only: never modifies
    `candle` or `data_quality_event`.
    """
    step = interval_to_timedelta(interval)
    expected = expected_open_times(window_start, window_end, step)

    actual = {
        row.open_time
        for row in conn.execute(
            sa.select(candle_table.c.open_time).where(
                candle_table.c.instrument_id == instrument_id,
                candle_table.c.interval == interval,
                candle_table.c.open_time >= window_start,
                candle_table.c.open_time <= window_end,
            )
        )
    }

    return gaps_from_expected_and_actual(
        expected, actual, step=step, maintenance_windows=maintenance_windows
    )


def record_gaps(
    conn: sa.Connection,
    result: GapScanResult,
    *,
    instrument_id: UUID,
    interval: str,
    detected_at: datetime,
) -> None:
    """Write every detected gap to `data_quality_event` and increment
    `data_quality_violations_total`. A no-op, not an error, if there are
    no gaps."""
    if not result.gaps:
        return

    conn.execute(
        sa.insert(data_quality_event),
        [
            {
                "instrument_id": instrument_id,
                "interval": interval,
                "check_name": "missing_interval",
                "severity": "flagged",
                "open_time": gap.start,
                "details": {
                    "gap_start": gap.start.isoformat(),
                    "gap_end": gap.end.isoformat(),
                    "missing_count": len(gap.missing_open_times),
                    "classification": gap.classification,
                    "detected_by": "gap_scanner",
                },
                "detected_at": detected_at,
            }
            for gap in result.gaps
        ],
    )
    for _gap in result.gaps:
        data_quality_violations_total.labels(check="missing_interval", severity="flagged").inc()


def run_gap_scan_and_backfill(
    conn: sa.Connection,
    *,
    rest_client: BinanceRestClient,
    venue_id: UUID,
    instrument_id: UUID,
    symbol: str,
    interval: str,
    window_start: datetime,
    window_end: datetime,
    maintenance_windows: Sequence[MaintenanceWindow] = (),
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> GapScanResult:
    """Scan `[window_start, window_end]`, log every detected gap, and
    trigger T-P1-04's `backfill_candles` (unmodified) for every
    `"unexplained"` gap — never for a `"maintenance"`-classified one,
    since there is nothing to fetch. Intended to be invoked on process
    startup and again on a nightly schedule (an external, operational
    concern; see this module's docstring).

    Returns the scan result *as found*, before any backfill ran — call
    `detect_gaps` again afterward to confirm the gaps are gone.
    """
    ts = now()
    result = detect_gaps(
        conn,
        instrument_id=instrument_id,
        interval=interval,
        window_start=window_start,
        window_end=window_end,
        maintenance_windows=maintenance_windows,
    )
    record_gaps(conn, result, instrument_id=instrument_id, interval=interval, detected_at=ts)
    conn.commit()

    for gap in result.gaps:
        if gap.classification == "unexplained":
            backfill_candles(
                rest_client=rest_client,
                conn=conn,
                venue_id=venue_id,
                instrument_id=instrument_id,
                symbol=symbol,
                interval=interval,
                range_start=gap.start,
                range_end=gap.end,
                now=now,
            )

    return result
