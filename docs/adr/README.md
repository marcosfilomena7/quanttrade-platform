# Architecture Decision Records

This directory holds the platform's Architecture Decision Records (ADRs) —
one file per significant, hard-to-reverse technical decision, each cited
from `docs/ARCHITECTURE.md`.

## The one rule: append-only

**ADR files are never deleted, renamed out of this directory, or edited to
reverse their decision.** A decision that changes gets a **new** ADR
number. The new ADR's `Supersedes` field points at the old one; the old
ADR's `Superseded-by` field is then updated to point forward at the new
one. The old file's `Context`/`Decision`/`Consequences` stay as they were
written — a historical record of what was decided and why, at the time.

This is enforced mechanically, not just by convention: CI
(`scripts/check_adr_not_deleted.py`, wired in via `make adr-check`) fails
any push or pull request that deletes or moves a file out of `docs/adr/`.
Editing an existing ADR's text (e.g. fixing a typo, adding a missing
reference) is not itself banned by that check — only removing the file is.

## Template

Every ADR follows the same shape:

```markdown
# ADR-NNN: <Title>

| Field | Value |
|---|---|
| Status | Proposed \| Accepted \| Superseded |
| Date | YYYY-MM-DD |
| Deciders | ... |
| Supersedes | ADR-XXX or — |
| Superseded-by | ADR-YYY or — |

## Context
## Decision
## Consequences
## Reference
```

## Index

| ADR | Title |
|---|---|
| [ADR-000](ADR-000-mid-frequency-scope-not-hft.md) | Scope: Mid-Frequency System, Not HFT |
| [ADR-001](ADR-001-primary-language-python.md) | Primary Language: Python 3.12+ |
| [ADR-002](ADR-002-primary-database-postgres-timescaledb.md) | Primary Database: PostgreSQL + TimescaleDB |
| [ADR-003](ADR-003-message-bus-asyncio-to-nats.md) | Message Bus: In-Process asyncio → NATS/JetStream |
| [ADR-004](ADR-004-orm-sqlalchemy-core-no-domain-orm.md) | ORM: SQLAlchemy 2.0 Core, No ORM in the Domain Layer |
