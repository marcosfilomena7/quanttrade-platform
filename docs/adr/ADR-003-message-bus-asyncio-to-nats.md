# ADR-003: Message Bus — In-Process asyncio Queues, Migrating to NATS + JetStream

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-07-12 |
| Deciders | CTO / Lead Architect |
| Supersedes | — |
| Superseded-by | — |

## Context

ARCHITECTURE.md §6.3 evaluates in-process asyncio queues, NATS +
JetStream, Redis Streams, Kafka, and RabbitMQ against latency,
durability, and operational burden. The platform starts as a modular
monolith (a single process), so the message bus's transport is, for now,
an internal implementation detail — but the *interface* it sits behind
must not be, since the platform will eventually split into multiple
processes.

## Decision

Start with **in-process async queues** behind an `EventBus` port. Move to
**NATS + JetStream** only when processes actually split. Kafka is
explicitly not adopted now — its operational cost is real and paid daily,
while its benefit (sustained throughput beyond ~100k msg/s) is
hypothetical and not yet reached; it is deferred indefinitely, "a Year-3
conversation, if ever."

The interface this decision depends on already exists:
`domain/ports/event_bus.py` defines the `EventBus` Protocol that
producers and consumers code against, independent of the transport
behind it.

## Consequences

**Easier:** zero operational burden and microsecond latency in Phase 1,
since there is no separate broker process to run, monitor, or upgrade.

**Harder:** no cross-process durability or fanout until the migration to
NATS + JetStream happens; that migration must be planned for, not
discovered as an emergency, once processes split.

**The important part is the port, not the transport.** Because
`EventBus` is coded against from day one, swapping the in-process
implementation for a NATS-backed one is a configuration and adapter
change, not a rewrite of every producer and consumer.

**To revisit:** when the modular monolith is first split into multiple
processes (see ARCHITECTURE.md's phased roadmap), or if message volume
approaches a scale where in-process queues no longer suffice.

## Reference

- ARCHITECTURE.md §6.3 ("ADR-003 — Message Bus" — full option comparison
  table and decision).
- ARCHITECTURE.md §6.5 ("Full Stack Summary" — "Bus" row).
- `domain/ports/event_bus.py` (the `EventBus` port this decision is
  implemented behind).
