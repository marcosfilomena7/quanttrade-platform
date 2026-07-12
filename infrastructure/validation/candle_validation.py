"""Candle Data Validation Suite (TASKS.md T-P1-05).

"Implement a validation pipeline that runs on ingested data: (1) OHLC
invariants ...; (2) timestamps strictly monotonic and aligned to the
interval boundary; (3) volume >= 0; zero-volume streaks flagged after N
consecutive bars; (4) price move > N sigma (rolling window) flagged, not
dropped; (5) no missing intervals in a trading window. Violations write
to a `data_quality_event` log table and emit metrics. Data is
quarantined, never silently dropped."

Design decisions, and why:

- **Two severities, not five ad hoc rules.** Every check is either
  `"quarantined"` (the candle must never reach `candle`) or `"flagged"`
  (the candle is retained; only the fact of the anomaly is recorded).
  TASKS.md's own acceptance criteria draw exactly this line: "high <
  close produces a quarantine record" (blocked) vs. "a 20 sigma price
  spike is flagged but the candle is retained" (kept).
- **OHLC invariants, `volume >= 0`, and timestamp alignment are
  `"quarantined"` — checkable from a single bar, with no neighbors.**
  Two of the three are not just policy here: `candle`'s own CHECK
  constraints (T-P0-11 — `ck_candle_high_vs_open_close`,
  `ck_candle_low_vs_open_close`, `ck_candle_high_vs_low`,
  `ck_candle_volume_nonneg`) would reject such a row outright, so a bad
  bar physically *cannot* reach `candle` even if this suite were
  bypassed. Timestamp alignment has no equivalent DB constraint, but is
  the same kind of "this bar is not well-formed" defect, so it gets the
  same treatment. Validating in Python *before* attempting to persist
  (rather than catching the resulting `IntegrityError`) keeps this
  suite's behavior independent of exact constraint wording and fully
  unit-testable with no database at all.
- **Monotonic timestamps, zero-volume streaks, price-move-sigma, and
  missing intervals are `"flagged"` — they need surrounding bars to
  judge at all**, and none of them means the bar's *own* data is
  malformed. A non-monotonic arrival is a delivery-order anomaly, not
  proof the bar itself is wrong; a 20 sigma move might be a real crash
  (ARCHITECTURE.md §11.4: "Flag; never silently drop... it might be a
  real crash"). Sequence checks run over the *original* batch order,
  including any quarantined bar's position, so a quarantined bar being
  excluded never masquerades as a data gap.
- **Two entry points, matching "run at ingestion, and again as a
  nightly batch" (ARCHITECTURE.md §11.4) exactly:**
  `validate_and_ingest_candles` (fresh data, gates what gets upserted)
  and `run_validation_suite` (re-reads an already-stored range and
  re-checks it — by construction this can only ever find the sequence
  checks, since quarantine-worthy bars can never have been stored).
- **Rolling price-move stddev uses `Decimal` arithmetic throughout**,
  never `float` — ARCHITECTURE.md §3.3's Decimal rule applies (this is a
  code path touching price), and mixing `Decimal` with a `float`
  threshold would raise `TypeError` at runtime regardless.
- **Nothing in `infrastructure/jobs/ohlcv_backfill_job.py` (T-P1-04) is
  modified.** This suite is additive; wiring it into that job's write
  path is a design change to already-completed, working behavior that
  no T-P1-05 acceptance criterion asks for.
- **Only the five checks TASKS.md's own description names are
  implemented.** ARCHITECTURE.md §11.4's fuller table also lists
  "cross-source price agreement" and "bid <= ask", which need
  multi-venue quotes and order-book data this platform does not have
  yet — out of scope here, not merely deferred by oversight.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from infrastructure.db.tables.data_quality import data_quality_event
from infrastructure.db.tables.market_data import candle as candle_table
from infrastructure.jobs.ohlcv_backfill_job import interval_to_timedelta
from infrastructure.observability.metrics import data_quality_violations_total

CheckName = Literal[
    "ohlc_invariant",
    "volume_nonneg",
    "timestamp_alignment",
    "timestamp_monotonic",
    "zero_volume_streak",
    "price_move_sigma",
    "missing_interval",
]
Severity = Literal["quarantined", "flagged"]

_DEFAULT_ZERO_VOLUME_STREAK_THRESHOLD = 5
_DEFAULT_PRICE_MOVE_WINDOW = 20
_DEFAULT_PRICE_MOVE_SIGMA_THRESHOLD = Decimal("10")


@dataclass(frozen=True)
class CandleRecord:
    """The minimal candle shape this suite validates.

    Deliberately not `domain.Candle` or `infrastructure.venues.binance
    .models.Kline`: this suite must construct instances both from
    freshly-fetched venue data and from rows already sitting in
    `candle`, so it depends on neither.
    """

    instrument_id: UUID
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Violation:
    """One detected data-quality issue, ready to become a
    `data_quality_event` row."""

    instrument_id: UUID
    interval: str
    check: CheckName
    severity: Severity
    open_time: datetime
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of validating one batch: which violations were found,
    and which candles are safe to persist."""

    quarantined: list[Violation]
    flagged: list[Violation]
    retained_candles: list[CandleRecord]

    @property
    def violations(self) -> list[Violation]:
        return [*self.quarantined, *self.flagged]


def validate_candle(candle: CandleRecord) -> list[Violation]:
    """Per-candle structural checks — hard invariants a single bar can
    violate on its own. Every violation here is `"quarantined"`."""
    violations: list[Violation] = []

    if (
        candle.high < max(candle.open, candle.close)
        or candle.low > min(candle.open, candle.close)
        or candle.high < candle.low
    ):
        violations.append(
            Violation(
                instrument_id=candle.instrument_id,
                interval=candle.interval,
                check="ohlc_invariant",
                severity="quarantined",
                open_time=candle.open_time,
                details={
                    "open": str(candle.open),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "close": str(candle.close),
                },
            )
        )

    if candle.volume < 0:
        violations.append(
            Violation(
                instrument_id=candle.instrument_id,
                interval=candle.interval,
                check="volume_nonneg",
                severity="quarantined",
                open_time=candle.open_time,
                details={"volume": str(candle.volume)},
            )
        )

    step = interval_to_timedelta(candle.interval)
    if not _is_aligned(candle.open_time, step):
        violations.append(
            Violation(
                instrument_id=candle.instrument_id,
                interval=candle.interval,
                check="timestamp_alignment",
                severity="quarantined",
                open_time=candle.open_time,
                details={"interval": candle.interval},
            )
        )

    return violations


def _is_aligned(open_time: datetime, step: timedelta) -> bool:
    epoch = datetime(1970, 1, 1, tzinfo=open_time.tzinfo)
    elapsed_seconds = (open_time - epoch).total_seconds()
    return elapsed_seconds % step.total_seconds() == 0


def validate_candle_sequence(
    candles: Sequence[CandleRecord],
    *,
    zero_volume_streak_threshold: int = _DEFAULT_ZERO_VOLUME_STREAK_THRESHOLD,
    price_move_window: int = _DEFAULT_PRICE_MOVE_WINDOW,
    price_move_sigma_threshold: Decimal = _DEFAULT_PRICE_MOVE_SIGMA_THRESHOLD,
) -> list[Violation]:
    """Sequence-level checks needing surrounding context: strictly
    monotonic timestamps, zero-volume streaks, missing intervals, and
    price moves beyond a rolling N-sigma threshold. `candles` is assumed
    already ordered by `open_time` (the natural order of both a fresh
    venue fetch and a `SELECT ... ORDER BY open_time`) — this function
    does not re-sort, so a genuinely out-of-order sequence is itself
    flagged as non-monotonic rather than silently corrected.
    """
    if len(candles) < 2:
        return []

    step = interval_to_timedelta(candles[0].interval)
    violations: list[Violation] = []

    zero_streak = 0
    for candle in candles:
        if candle.volume == 0:
            zero_streak += 1
        else:
            if zero_streak >= zero_volume_streak_threshold:
                violations.append(
                    Violation(
                        instrument_id=candle.instrument_id,
                        interval=candle.interval,
                        check="zero_volume_streak",
                        severity="flagged",
                        open_time=candle.open_time,
                        details={"streak_length": zero_streak},
                    )
                )
            zero_streak = 0
    if zero_streak >= zero_volume_streak_threshold:
        last = candles[-1]
        violations.append(
            Violation(
                instrument_id=last.instrument_id,
                interval=last.interval,
                check="zero_volume_streak",
                severity="flagged",
                open_time=last.open_time,
                details={"streak_length": zero_streak},
            )
        )

    for previous, current in zip(candles, candles[1:], strict=False):
        delta = current.open_time - previous.open_time
        if delta <= timedelta(0):
            violations.append(
                Violation(
                    instrument_id=current.instrument_id,
                    interval=current.interval,
                    check="timestamp_monotonic",
                    severity="flagged",
                    open_time=current.open_time,
                    details={"previous_open_time": previous.open_time.isoformat()},
                )
            )
        elif delta > step:
            violations.append(
                Violation(
                    instrument_id=current.instrument_id,
                    interval=current.interval,
                    check="missing_interval",
                    severity="flagged",
                    open_time=current.open_time,
                    details={
                        "gap_start": previous.open_time.isoformat(),
                        "gap_end": current.open_time.isoformat(),
                    },
                )
            )

    violations.extend(
        _price_move_violations(
            candles, window=price_move_window, sigma_threshold=price_move_sigma_threshold
        )
    )

    return violations


def _price_move_violations(
    candles: Sequence[CandleRecord], *, window: int, sigma_threshold: Decimal
) -> list[Violation]:
    """`returns[k]` is the close-to-close return from `candles[k]` to
    `candles[k + 1]` — it "belongs" to `candles[k + 1]`, the bar the
    move happened on. Each return is compared against the rolling
    standard deviation of the `window` returns strictly *before* it, so
    the anomalous move itself never inflates its own baseline."""
    returns: list[Decimal] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        if previous.close == 0:
            returns.append(Decimal(0))
        else:
            returns.append((current.close - previous.close) / previous.close)

    violations: list[Violation] = []
    for k in range(window, len(returns)):
        baseline = returns[k - window : k]
        mean = sum(baseline, start=Decimal(0)) / len(baseline)
        variance = sum(((r - mean) ** 2 for r in baseline), start=Decimal(0)) / len(baseline)
        if variance <= 0:
            continue
        stddev = variance.sqrt()

        current_return = returns[k]
        if abs(current_return) > sigma_threshold * stddev:
            candle = candles[k + 1]
            violations.append(
                Violation(
                    instrument_id=candle.instrument_id,
                    interval=candle.interval,
                    check="price_move_sigma",
                    severity="flagged",
                    open_time=candle.open_time,
                    details={
                        "return": str(current_return),
                        "stddev": str(stddev),
                        "sigma_multiple": str(abs(current_return) / stddev),
                    },
                )
            )
    return violations


def validate_candles(
    candles: Sequence[CandleRecord], **sequence_kwargs: object
) -> ValidationResult:
    """Run both per-candle and sequence-level checks over an in-memory
    batch. See `validate_candle_sequence` for `**sequence_kwargs`."""
    quarantined: list[Violation] = []
    retained: list[CandleRecord] = []
    for candle in candles:
        per_candle_violations = validate_candle(candle)
        if per_candle_violations:
            quarantined.extend(per_candle_violations)
        else:
            retained.append(candle)

    flagged = validate_candle_sequence(candles, **sequence_kwargs)  # type: ignore[arg-type]

    return ValidationResult(quarantined=quarantined, flagged=flagged, retained_candles=retained)


def record_violations(
    conn: sa.Connection, violations: Sequence[Violation], *, detected_at: datetime
) -> None:
    """Write every violation to `data_quality_event` and increment
    `data_quality_violations_total`. A no-op, not an error, if `violations`
    is empty."""
    if not violations:
        return

    conn.execute(
        sa.insert(data_quality_event),
        [
            {
                "instrument_id": v.instrument_id,
                "interval": v.interval,
                "check_name": v.check,
                "severity": v.severity,
                "open_time": v.open_time,
                "details": v.details,
                "detected_at": detected_at,
            }
            for v in violations
        ],
    )
    for v in violations:
        data_quality_violations_total.labels(check=v.check, severity=v.severity).inc()


def _upsert_retained_candles(
    conn: sa.Connection, candles: Sequence[CandleRecord], *, source: str, now: datetime
) -> None:
    if not candles:
        return
    rows = [
        {
            "instrument_id": c.instrument_id,
            "interval": c.interval,
            "open_time": c.open_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "trade_count": 0,
            "is_closed": c.open_time + interval_to_timedelta(c.interval) <= now,
            "source": source,
        }
        for c in candles
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


def validate_and_ingest_candles(
    conn: sa.Connection,
    candles: Sequence[CandleRecord],
    *,
    source: str = "validated_ingest",
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    **sequence_kwargs: object,
) -> ValidationResult:
    """The ingestion-time entry point (ARCHITECTURE.md §11.4: "Run at
    ingestion"). Validates a freshly-fetched batch, records every
    violation, and upserts only the candles that passed every per-candle
    check into `candle` — a flagged-but-not-quarantined candle is still
    retained. Commits the transaction on the given connection.
    """
    result = validate_candles(candles, **sequence_kwargs)
    ts = now()
    record_violations(conn, result.violations, detected_at=ts)
    _upsert_retained_candles(conn, result.retained_candles, source=source, now=ts)
    conn.commit()
    return result


def run_validation_suite(
    conn: sa.Connection,
    *,
    instrument_id: UUID,
    interval: str,
    window_start: datetime,
    window_end: datetime,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    **sequence_kwargs: object,
) -> ValidationResult:
    """The nightly-batch entry point (ARCHITECTURE.md §11.4: "... and
    again as a nightly batch"). Re-reads already-stored `candle` rows for
    `[window_start, window_end]` and re-validates them. By construction
    this can only ever surface the sequence-level checks: a bar that
    would fail a per-candle check cannot have been stored in `candle` in
    the first place (see this module's docstring). Never modifies or
    deletes any `candle` row. Commits the transaction on the given
    connection.
    """
    rows = conn.execute(
        sa.select(
            candle_table.c.open_time,
            candle_table.c.open,
            candle_table.c.high,
            candle_table.c.low,
            candle_table.c.close,
            candle_table.c.volume,
        )
        .where(
            candle_table.c.instrument_id == instrument_id,
            candle_table.c.interval == interval,
            candle_table.c.open_time >= window_start,
            candle_table.c.open_time <= window_end,
        )
        .order_by(candle_table.c.open_time)
    ).all()

    candles = [
        CandleRecord(
            instrument_id=instrument_id,
            interval=interval,
            open_time=r.open_time,
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
        )
        for r in rows
    ]

    result = validate_candles(candles, **sequence_kwargs)
    ts = now()
    record_violations(conn, result.violations, detected_at=ts)
    conn.commit()
    return result
