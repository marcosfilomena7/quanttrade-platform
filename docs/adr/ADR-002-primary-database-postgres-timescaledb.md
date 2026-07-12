# ADR-002: Primary Database — PostgreSQL 16 + TimescaleDB (Two-Tier Hot/Cold)

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-07-12 |
| Deciders | CTO / Lead Architect |
| Supersedes | — |
| Superseded-by | — |

## Context

The platform must serve two access patterns that pull in opposite
directions: high-frequency time-series appends with range scans
(candles, ticks — analytical), and transactional order/position state
requiring strong consistency (orders, fills, positions, the event log —
operational). ARCHITECTURE.md §6.2 evaluates PostgreSQL+TimescaleDB,
InfluxDB, ClickHouse, MongoDB, vanilla Postgres, QuestDB, kdb+, and
DuckDB+Parquet against both patterns plus operational burden and
ecosystem maturity.

## Decision

A **two-tier design**:

- **Hot / operational:** PostgreSQL 16 + TimescaleDB. Orders, positions,
  fills, the event store, and recent candles (~90 days). Full ACID where
  money and state are concerned.
- **Cold / research:** Parquet on object storage (S3/R2), queried via
  DuckDB or Polars. Full candle and tick history — cheap, columnar,
  fast for backtests, trivially versionable.
- **Deferred:** ClickHouse, adopted only once tick data exceeds roughly
  1 TB and DuckDB scans on the cold tier become the bottleneck.

This decision is already partially in force: `docs/DATABASE.md`'s
24-table baseline schema and the Alembic migration in
`infrastructure/db/` (T-P0-11) implement the hot/operational tier
described here, including the three TimescaleDB hypertables (`candle`,
`trade_tick`, `equity_snapshot`).

## Consequences

**Easier:** a single database engine to operate for both patterns;
mature, battle-tested tooling; full transactional guarantees exactly
where money is concerned.

**Harder:** very large analytical scans on the hot tier will not perform
as well as a dedicated columnar store would; disciplined archival from
hot to cold is required as the hot tier grows.

**To revisit:** at roughly 1 TB of hot data, or if continuous-aggregate
refresh becomes a measured bottleneck — at which point ClickHouse is
introduced as an additional analytical tier, not a replacement for the
operational tier.

## Reference

- ARCHITECTURE.md §6.2 ("ADR-002 — Primary Database" — full option
  comparison table, two-tier decision, and trade-off).
- ARCHITECTURE.md §6.5 ("Full Stack Summary" — "DB" row).
- ARCHITECTURE.md §7 ("Database Design").
- `docs/DATABASE.md` (the 24-entity schema this decision underwrites) and
  `infrastructure/db/tables/` + `alembic/versions/` (T-P0-11's
  implementation of the hot/operational tier).
