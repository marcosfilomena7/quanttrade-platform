# ADR-001: Primary Language — Python 3.12+

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-07-12 |
| Deciders | CTO, Lead Quant |
| Supersedes | — |
| Superseded-by | — |

## Context

Given the mid-frequency scope fixed by ADR-000 (10–500 ms decision
budget), the binding constraint on the platform's success is research
velocity — finding alpha — not execution latency — capturing it
(ARCHITECTURE.md §3.1). The same strategy code must run identically in
backtest and live. The team is small.

ARCHITECTURE.md §6.1 evaluates Python, Rust, C++, Go, Java/Kotlin, C#, and
TypeScript against latency, numerics ecosystem, research velocity, talent
pool, and safety. Python is the only option rated "Unmatched" on numerics
ecosystem (numpy, pandas, polars, scipy, statsmodels, sklearn, torch) and
"Highest" on research velocity; Go and TypeScript are disqualified
specifically for weak numerics.

## Decision

**Python 3.12+**, using `asyncio`, with `mypy --strict` enforced on the
domain and application layers. Rust, via PyO3, is adopted later **only**
for identified hot spots that profiling proves need it — realistically
the backtest inner loop and possibly L2 order-book maintenance, and
nothing else pre-emptively.

## Consequences

**Easier:** research iteration, hiring (largest talent pool in
quant/finance), library reuse, notebook-to-production parity.

**Harder:** true parallelism is constrained by the GIL (mitigated with
multiprocessing; largely irrelevant for this platform's I/O-bound
workload); runtime type errors (mitigated with `mypy --strict` plus
Pydantic v2 validation at all I/O boundaries); larger deployment
footprint than a compiled binary.

**The trade, stated honestly:** a ~10–100× single-threaded slowdown versus
Rust is accepted for a ~5× improvement in research iteration speed. This
trade is correct *because and only because* ADR-000 fixes the workload as
latency-tolerant — network RTT to the exchange (20–100 ms) already
dwarfs the entire Python compute budget.

**To revisit:** if p99 strategy-evaluation latency exceeds 20 ms in
practice, or if ADR-000 is ever superseded toward a latency-sensitive
strategy class — at which point this ADR is superseded, not amended, and
any rewrite is scoped to the execution path only.

## Reference

- ARCHITECTURE.md §6.1 ("ADR-001 — Primary Language" — full option
  comparison table, decision, and trade-off).
- ARCHITECTURE.md §6.5 ("Full Stack Summary" — "Language" row).
- Depends on [ADR-000](ADR-000-mid-frequency-scope-not-hft.md).
