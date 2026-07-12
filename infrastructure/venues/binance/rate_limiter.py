"""Client-side tracking of Binance's weight-based rate limit.

TASKS.md T-P1-01: "Enforce Binance's weight-based rate limit
client-side: track remaining weight from response headers, back off
when approaching the limit, and reset on the 1-minute boundary." Binance
reports the trailing-1-minute used weight on every response via the
`X-MBX-USED-WEIGHT-1M` header. This tracker remembers the most recently
observed value and treats it as stale — resetting to zero — once 60
seconds have passed since it was first recorded, so a client that stops
sending requests for a while doesn't stay locked out on a number the
venue itself would no longer be reporting.

This is deliberately much simpler than T-P6-02's later "Rate-Limit
Budget Manager" (per-endpoint weight costs, a `venue_rate_limit_headroom_pct`
metric, queueing order submissions): T-P1-01 has no order-submission
concept at all yet. It only needs enough tracking to answer one
question — "should this client hold off before sending another
request?" — for the data-fetching endpoints it exposes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

_WINDOW = timedelta(minutes=1)


class RateLimitTracker:
    """Tracks Binance's `X-MBX-USED-WEIGHT-1M` header across requests.

    `backoff_ratio` and the internal window are plain configuration
    (not price, quantity, or balance data — ARCHITECTURE.md §3.3's
    Decimal rule does not apply here), so `float` is used the same way
    `time.sleep()`'s argument is: a duration/ratio, not money.
    """

    def __init__(self, *, weight_limit: int = 1200, backoff_ratio: float = 0.9) -> None:
        if weight_limit <= 0:
            raise ValueError("weight_limit must be positive")
        if not 0 < backoff_ratio <= 1:
            raise ValueError("backoff_ratio must be in (0, 1]")
        self._weight_limit = weight_limit
        self._backoff_threshold = int(weight_limit * backoff_ratio)
        self._used_weight = 0
        self._window_started_at: datetime | None = None

    def observe(self, used_weight_header: str | None, now: datetime) -> None:
        """Record the weight Binance reported on the most recent response."""
        self._expire_if_stale(now)
        if used_weight_header is None:
            return
        try:
            used_weight = int(used_weight_header)
        except ValueError:
            return
        self._used_weight = used_weight
        if self._window_started_at is None:
            self._window_started_at = now

    def should_back_off(self, now: datetime) -> bool:
        """True once tracked usage has reached the configured threshold."""
        self._expire_if_stale(now)
        return self._used_weight >= self._backoff_threshold

    @property
    def used_weight(self) -> int:
        return self._used_weight

    @property
    def weight_limit(self) -> int:
        return self._weight_limit

    def _expire_if_stale(self, now: datetime) -> None:
        if self._window_started_at is not None and now - self._window_started_at >= _WINDOW:
            self._used_weight = 0
            self._window_started_at = None
