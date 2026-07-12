# ADR-000: Scope — Mid-Frequency System, Not High-Frequency Trading

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-07-12 |
| Deciders | CTO / Lead Architect, Founder |
| Supersedes | — |
| Superseded-by | — |

## Context

ARCHITECTURE.md §1.1 states this in the strongest possible terms: *"The
frequency decision is the most consequential thing in this section.
Everything downstream — language, architecture, hosting, cost, team —
follows from it."* The document draws a hard line between two kinds of
system:

| It Is | It Is Not |
|---|---|
| A mid-frequency system (seconds to days holding periods) | A high-frequency or latency-arbitrage system |

and commits to a specific latency budget: **10–500 milliseconds
decision latency, holding periods of minutes to weeks.**

This is also the first blocking open question in §16.1 (Q1): *"What is
the actual latency requirement? Mid-frequency (this document) or
HFT/market making? ... This document is invalid for HFT."* The
recommendation given there — and the one this ADR formally accepts — is
mid-frequency.

Every other ADR in this set (ADR-001 through ADR-004) is a direct
consequence of this one. ADR-001 in particular states its trade-off
"is correct **because and only because** the workload is latency-tolerant"
— i.e., because of the decision recorded here.

## Decision

The platform is built, from Phase 0 onward, as a **mid-frequency
systematic trading system**: a decision latency budget of 10–500 ms and
holding periods of minutes to weeks. It explicitly does **not** pursue
market making, latency arbitrage, or any sub-millisecond strategy class.

## Consequences

**Easier:** Python becomes viable as the primary language (ADR-001) —
network round-trip time to the exchange (20–100 ms) already exceeds the
platform's own compute budget by an order of magnitude, so Python's
~10–100× single-threaded slowdown relative to Rust/C++ costs comparatively
little. No kernel-bypass networking, FPGA acceleration, or colocation is
required.

**Harder / foreclosed:** this decision — and every architectural choice
built on top of it — is invalid for HFT. A future push into market making
or latency arbitrage cannot be accommodated by amending this ADR; it
requires a **new, superseding** ADR and a rewrite scoped to the execution
path only (mirroring the escape hatch ADR-001 already describes for
itself).

**To revisit:** if a latency-sensitive strategy class is later adopted, or
if p99 decision latency requirements tighten below the current 10–500 ms
budget (ADR-001's own revisit trigger: p99 strategy-evaluation latency
exceeding 20 ms).

## Reference

- ARCHITECTURE.md §1.1 ("What This Platform Is" — the mid-frequency vs.
  HFT table and "the frequency decision is the most consequential thing
  in this section").
- ARCHITECTURE.md §16.1, Q1 (blocking open question; recommendation:
  mid-frequency; "This document is invalid for HFT").
