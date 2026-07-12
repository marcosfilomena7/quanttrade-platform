"""Tests for the Prometheus metric families (infrastructure/observability/metrics.py)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram, generate_latest
from prometheus_client.metrics import MetricWrapperBase

from infrastructure.observability import metrics as m


def _scrape() -> str:
    return generate_latest(m.REGISTRY).decode()


def _sample(collector: MetricWrapperBase, sample_name: str, labels: dict[str, str]) -> float:
    """Read a metric's current value via the public `collect()` API — used
    only for unlabeled metrics, whose base sample always exists from the
    moment the metric object is created (unlike a labeled child, which
    doesn't exist until `.labels(...)` is called with that exact label
    combination at least once)."""
    for family in collector.collect():
        for s in family.samples:
            if s.name == sample_name and s.labels == labels:
                return s.value
    raise AssertionError(f"no sample named {sample_name!r} with labels {labels!r}")


def test_five_metric_families_are_defined() -> None:
    assert isinstance(m.order_submissions_total, Counter)
    assert isinstance(m.order_rejections_total, Counter)
    assert isinstance(m.risk_decisions_total, Counter)
    assert isinstance(m.fill_processing_seconds, Histogram)
    assert isinstance(m.data_staleness_seconds, Histogram)


def test_no_metric_in_this_module_is_a_gauge() -> None:
    """TASKS.md T-P0-08: "All are counters or histograms — no gauges that
    could reflect stale state silently." Structural check over every
    public metric object this module defines, not just the five named
    ones, so a future addition can't accidentally slip a Gauge in.

    (prometheus_client itself attaches an auxiliary `_created` gauge-typed
    sample to every Counter/Histogram at scrape time — that is the
    library's own internal bookkeeping about *when* the metric was
    created, not a piece of mutable domain state this module chose to
    expose, so it is out of scope for this check.)
    """
    for name in dir(m):
        if name.startswith("_"):
            continue
        value = getattr(m, name)
        if isinstance(value, MetricWrapperBase):
            assert isinstance(value, Counter | Histogram), (
                f"{name} is a {type(value).__name__}, not a Counter or Histogram"
            )


def test_all_five_families_are_scrapeable_by_name() -> None:
    text = _scrape()
    assert "# TYPE order_submissions_total counter" in text
    assert "# TYPE order_rejections_total counter" in text
    assert "# TYPE risk_decisions_total counter" in text
    assert "# TYPE fill_processing_seconds histogram" in text
    assert "# TYPE data_staleness_seconds histogram" in text


def test_order_submissions_total_increments() -> None:
    before = _sample(m.order_submissions_total, "order_submissions_total", {})
    m.order_submissions_total.inc()
    after = _sample(m.order_submissions_total, "order_submissions_total", {})
    assert after == before + 1


def test_order_rejections_total_is_labeled_by_reason() -> None:
    m.order_rejections_total.labels(reason="BELOW_MIN_NOTIONAL_TEST_ONLY").inc()
    text = _scrape()
    assert 'order_rejections_total{reason="BELOW_MIN_NOTIONAL_TEST_ONLY"} 1.0' in text


def test_risk_decisions_total_is_labeled_by_outcome() -> None:
    m.risk_decisions_total.labels(outcome="approved_test_only").inc()
    text = _scrape()
    assert 'risk_decisions_total{outcome="approved_test_only"} 1.0' in text


def test_fill_processing_seconds_observes_a_duration() -> None:
    before_count = _sample(m.fill_processing_seconds, "fill_processing_seconds_count", {})
    before_sum = _sample(m.fill_processing_seconds, "fill_processing_seconds_sum", {})
    m.fill_processing_seconds.observe(0.042)
    after_count = _sample(m.fill_processing_seconds, "fill_processing_seconds_count", {})
    after_sum = _sample(m.fill_processing_seconds, "fill_processing_seconds_sum", {})
    assert after_count == before_count + 1
    assert round(after_sum - before_sum, 3) == 0.042


def test_reference_data_changed_metric_is_defined() -> None:
    """TASKS.md T-P1-02: "A simulated tick-size change logs a warning and
    emits a `reference_data_changed` metric.\""""
    assert isinstance(m.reference_data_changed, Counter)


def test_reference_data_changed_is_scrapeable_by_name() -> None:
    """`prometheus_client` exposes a `Counter` with the OpenMetrics `_total`
    suffix appended at scrape time when the declared name doesn't already
    end with it — the metric is still named `reference_data_changed` at
    the Python level (`m.reference_data_changed`), matching the
    acceptance criterion's literal name."""
    text = _scrape()
    assert "# TYPE reference_data_changed_total counter" in text


def test_reference_data_changed_increments_per_venue_symbol_field() -> None:
    m.reference_data_changed.labels(
        venue="binance_test_only", symbol="BTCUSDT_TEST_ONLY", field="tick_size"
    ).inc()
    text = _scrape()
    assert (
        'reference_data_changed_total{field="tick_size",symbol="BTCUSDT_TEST_ONLY",'
        'venue="binance_test_only"} 1.0' in text
    )


def test_data_quality_violations_total_metric_is_defined() -> None:
    """TASKS.md T-P1-05: "Violations write to a data_quality_event log
    table and emit metrics.\""""
    assert isinstance(m.data_quality_violations_total, Counter)


def test_data_quality_violations_total_is_scrapeable_by_name() -> None:
    text = _scrape()
    assert "# TYPE data_quality_violations_total counter" in text


def test_data_quality_violations_total_increments_per_check_and_severity() -> None:
    m.data_quality_violations_total.labels(
        check="ohlc_invariant_test_only", severity="quarantined"
    ).inc()
    text = _scrape()
    assert (
        'data_quality_violations_total{check="ohlc_invariant_test_only",'
        'severity="quarantined"} 1.0' in text
    )


def test_data_staleness_seconds_accepts_a_new_symbol_without_pre_declaration() -> None:
    """TASKS.md T-P0-08: "`data_staleness_seconds` can be updated per symbol
    without a metric per symbol being pre-declared." A brand-new label
    value that has never been observed before must just work — nothing
    needs to be registered or declared for it ahead of time."""
    brand_new_symbol = "ZZZ_BRAND_NEW_SYMBOL_NEVER_SEEN_BEFORE_USDT"
    assert brand_new_symbol not in _scrape()

    m.data_staleness_seconds.labels(symbol=brand_new_symbol).observe(1.5)

    text = _scrape()
    assert f'data_staleness_seconds_count{{symbol="{brand_new_symbol}"}} 1.0' in text
    assert f'data_staleness_seconds_sum{{symbol="{brand_new_symbol}"}} 1.5' in text
