"""Prometheus metrics: counters and histograms only, never gauges.

ARCHITECTURE.md §3.7 lists domain metrics including "data staleness
(seconds since last tick per symbol)." TASKS.md T-P0-08 names five
concrete families and is explicit about the type constraint: "All are
counters or histograms — no gauges that could reflect stale state
silently." A gauge holds its last value forever if nothing updates it
again, which looks indistinguishable from "everything is fine" during an
outage. A histogram's `_count` only increases when something actually
observes a value, so a stalled reporting pipeline is visible as a
flatlined count rather than a frozen-but-plausible number — which is
exactly why `data_staleness_seconds`, despite reading like a natural
gauge, is a `Histogram` here: each call to `.observe(...)` records the
staleness measured *at that moment*, and the absence of new observations
is itself the signal that something has stopped reporting.

A dedicated `CollectorRegistry` is used rather than the implicit global
default, so these metrics can be scraped, tested, and reasoned about
independently of anything else that happens to import `prometheus_client`
in the same process.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()

order_submissions_total = Counter(
    "order_submissions_total",
    "Total number of orders submitted to a venue.",
    registry=REGISTRY,
)

order_rejections_total = Counter(
    "order_rejections_total",
    "Total number of orders rejected by the risk engine or a venue, by reason.",
    ["reason"],
    registry=REGISTRY,
)

risk_decisions_total = Counter(
    "risk_decisions_total",
    "Total number of risk engine decisions, by outcome.",
    ["outcome"],
    registry=REGISTRY,
)

fill_processing_seconds = Histogram(
    "fill_processing_seconds",
    "Time spent processing a single fill end-to-end.",
    registry=REGISTRY,
)

data_staleness_seconds = Histogram(
    "data_staleness_seconds",
    "Observed seconds-since-last-tick for a symbol, by symbol.",
    ["symbol"],
    registry=REGISTRY,
)
