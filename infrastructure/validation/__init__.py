"""Data validation — quality gates for ingested market data.

ARCHITECTURE.md §11.4: "Run at ingestion, and again as a nightly batch."
This package is deliberately independent of `infrastructure/jobs/`: it
validates candle-shaped data regardless of where it came from (a fresh
venue fetch, or rows already sitting in the `candle` table), and nothing
in `infrastructure/jobs/` is modified to call into it — T-P1-05 adds a
new, standalone capability rather than changing how T-P1-04's backfill
job already writes candles.
"""
