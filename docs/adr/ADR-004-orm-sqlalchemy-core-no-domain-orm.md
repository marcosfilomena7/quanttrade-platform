# ADR-004: ORM — SQLAlchemy 2.0 Core, No ORM in the Domain Layer

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-07-12 |
| Deciders | CTO / Lead Architect |
| Supersedes | — |
| Superseded-by | — |

## Context

ARCHITECTURE.md §6.4 evaluates SQLAlchemy 2.0 (Core, selectively ORM),
SQLModel, the Django ORM, Tortoise/Piccolo, and raw SQL only. SQLModel is
rejected specifically for conflating persistence with API schemas — a
boundary ARCHITECTURE.md's layering (§3.5, §4) requires kept clean; the
Django ORM is rejected for framework lock-in.

## Decision

**SQLAlchemy 2.0 Core** for repositories, with **raw SQL / `COPY`** for
bulk time-series ingestion where performance demands it, and **Alembic**
for migrations. **The domain layer never imports SQLAlchemy.**
Repositories map between domain objects and rows at the infrastructure
boundary — this is more upfront work, and it is what makes the domain
layer (and the risk engine specifically) unit-testable without a running
database, and what makes swapping the persistence store a repository
change rather than a domain rewrite.

This decision is already in force: `infrastructure/db/tables/` (T-P0-11)
defines all 24 baseline-schema tables as plain `sa.Table` Core objects —
not ORM-mapped classes — and the domain layer (`domain/`) has zero
dependency on SQLAlchemy, enforced continuously by the `import-linter`
layering contract.

## Consequences

**Easier:** the domain layer, including the risk engine, is testable in
isolation with no database at all; raw-SQL/`COPY` performance is
available for the hot time-series ingestion path without abandoning
Core's query-building for everything else; swapping or upgrading the
persistence store touches repositories, not domain logic.

**Harder:** repository code must explicitly map between `sa.Table` rows
and domain objects — there is no automatic object-relational mapping to
lean on, so this boundary code must be written and tested for every
aggregate.

**To revisit:** if repository boilerplate becomes a measured, significant
drag on velocity at a scale where a thin, carefully-bounded ORM layer
would pay for itself without reintroducing the SQLModel-style boundary
conflation this ADR explicitly rejects.

## Reference

- ARCHITECTURE.md §6.4 ("ADR-004 — ORM" — full option comparison table
  and decision).
- ARCHITECTURE.md §6.5 ("Full Stack Summary" — "ORM" and "Migrations"
  rows).
- ARCHITECTURE.md §3.5 / §4 (layering: "the domain layer imports nothing
  from the outer layers").
- `infrastructure/db/tables/` and `alembic/` (T-P0-11's implementation of
  this decision).
