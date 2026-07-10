# Software Architecture Document
## Institutional Quantitative Trading Platform
### Codename: **QuantTrade Platform**

| Field | Value |
|---|---|
| Document Status | **Draft for Approval** — Planning Phase, pre-implementation |
| Version | 0.1 |
| Date | 2026-07-10 |
| Author | CTO / Lead Architect |
| Audience | Founder, Engineering, Quant Research, Risk |
| Classification | Internal — Confidential |
| Supersedes | — |

---

## Table of Contents

1. [Product Vision](#1-product-vision)
2. [Functional Requirements](#2-functional-requirements)
3. [Non-Functional Requirements](#3-non-functional-requirements)
4. [Architecture](#4-architecture)
5. [Modules](#5-modules)
6. [Technology Decisions](#6-technology-decisions)
7. [Database Design](#7-database-design)
8. [Trading Engine](#8-trading-engine)
9. [Strategy Engine](#9-strategy-engine)
10. [Risk Engine](#10-risk-engine)
11. [Data Engine](#11-data-engine)
12. [Backtesting](#12-backtesting)
13. [Dashboard](#13-dashboard)
14. [Development Roadmap](#14-development-roadmap)
15. [Risks](#15-risks)
16. [Open Questions Requiring Your Approval](#16-open-questions-requiring-your-approval)

---

# 1. Product Vision

## 1.1 What This Platform Is

QuantTrade Platform is a **systematic trading system**: software that ingests market data, evaluates it against a library of quantitative strategies, produces trade intentions, filters those intentions through a risk mandate, executes the survivors on external venues, and maintains an auditable record of everything that happened and why.

It is defined equally by what it is not:

| It Is | It Is Not |
|---|---|
| A systematic execution and research platform | A discretionary trading terminal (no manual charting-and-clicking workflow) |
| A single-tenant system for one trading entity | A multi-tenant SaaS product for external customers |
| A mid-frequency system (seconds to days holding periods) | A high-frequency or latency-arbitrage system |
| A risk-first system where risk can always veto | A signal-first system where risk is a report |
| An auditable system where every decision is reconstructable | A black box |

**The frequency decision is the most consequential thing in this section.** Everything downstream — language, architecture, hosting, cost, team — follows from it. This document designs for a **mid-frequency system: decision latency budget of 10–500 milliseconds, holding periods of minutes to weeks.** If the intent is to compete on latency, the correct document is a different one recommending C++/Rust, kernel bypass networking, colocation, and a team that does not currently exist. This is confirmed in §16.

## 1.2 Who Will Use It

| Persona | Volume | Primary Need | Interface |
|---|---|---|---|
| **Quant Researcher** | 1–5 | Rapidly hypothesize, backtest, and validate strategies without touching production plumbing | Notebooks, research SDK, backtest CLI |
| **Trading Operator** | 1–3 | Observe live state, intervene, halt, adjust limits | Dashboard, kill switch |
| **Risk Officer** | 0–1 (initially the same person) | Set and enforce mandates; independently verify exposure | Dashboard (read-only), risk config, alerts |
| **Platform Engineer** | 1–3 | Keep the thing running, add venues, add data sources | Code, runbooks, observability stack |
| **Auditor / Regulator** | 0 initially | Reconstruct any decision after the fact | Immutable event log, reports |
| **Capital Allocator / LP** | 0 initially, later N | Understand performance and risk | Periodic reports, tearsheets |

Note that in the early phase, **all of these personas are the same person or two.** This matters enormously: it means separation of duties is a *design property you preserve for later*, not an org chart you have today. Build the seams; don't staff them yet.

## 1.3 Primary Objectives (Year 1)

1. **Do not lose money to software bugs.** This is objective zero and it dominates. A strategy that loses money is a research failure and is expected. A system that double-submits an order, or fills at a price it didn't intend, or fails to reconcile after a reconnect, is an engineering failure and is not acceptable at any capital level.
2. **Establish trustworthy backtesting.** A backtest whose results do not survive contact with live markets is worse than no backtest, because it manufactures false confidence and licenses larger position sizes.
3. **Achieve a closed research→production loop** where a strategy validated in backtest runs in production *with the same code path*, without reimplementation.
4. **Run one strategy, on one venue, with real capital, profitably, for one quarter.** Not ten strategies. One.
5. **Make every live decision auditable** — reconstruct, from persisted events alone, why any order existed.

## 1.4 Future Objectives (Years 2–3)

- Multi-strategy portfolio with **capital allocation** across strategies (risk parity, Kelly-fractional, meta-allocation).
- Multi-venue execution with **smart order routing** and cross-venue netting.
- Additional asset classes: equities, futures, FX, ETFs — in that order of decreasing similarity to what already exists.
- **Transaction cost analysis (TCA)** and execution algorithms (TWAP, VWAP, implementation shortfall, POV).
- Performance and **risk attribution** (factor exposure decomposition).
- Formal **compliance layer** if external capital is ever accepted.

## 1.5 Possible Expansion

| Direction | Attractiveness | Difficulty | Note |
|---|---|---|---|
| More crypto venues (CEX) | High | Low | Highest ROI per unit of work. Same abstraction, more adapters. |
| Crypto derivatives (perps, options) | High | Medium | Funding rates, mark price, liquidation logic. Genuinely different risk model. |
| DeFi / on-chain execution | Medium | **Very High** | MEV, gas, mempool, non-atomic settlement, key custody. A separate product. Do not conflate. |
| US Equities | Medium | High | Regulatory (PDT, Reg-T, wash sales), corporate actions, market hours, halts, SEC/FINRA. |
| Futures | Medium | Medium | Contract rolls, expiry, margin, exchange fee structure. Cleanest institutional path. |
| FX | Low | Medium | No central book; you're trading against LP streams. Different data model entirely. |
| Options | Low | Very High | Greeks, vol surface, pin risk. This is a different firm. |
| **Selling the platform as SaaS** | — | — | **Strongly advised against.** Multi-tenancy in a trading system is a security and correctness catastrophe. Different product, different company. |

## 1.6 Success Criteria

These must be measurable or they are aspirations.

| # | Criterion | Target | Measured By |
|---|---|---|---|
| SC-1 | Order correctness | **Zero** unintended orders, duplicate fills, or orphaned positions across a full quarter | Reconciliation audit |
| SC-2 | Backtest fidelity | Live Sharpe within **±0.5** of walk-forward out-of-sample Sharpe over 3 months | Live vs. OOS comparison |
| SC-3 | Slippage model accuracy | Realized slippage within **±25%** of modeled slippage, p50 | TCA report |
| SC-4 | Data integrity | **<0.01%** gaps in stored candle data; 100% detected and backfilled | Data quality checks |
| SC-5 | Execution availability | **99.5%** of intended trading minutes (Year 1), 99.9% (Year 2) | Uptime monitor |
| SC-6 | Reconciliation | Position/balance drift vs. exchange = **0**, checked every 60s | Reconciliation loop |
| SC-7 | Risk enforcement | **100%** of orders pass pre-trade risk; zero limit breaches reach a venue | Risk audit log |
| SC-8 | Kill switch | Flat within **<30s** of trigger, verified monthly | Chaos drill |
| SC-9 | Research velocity | New strategy from hypothesis → backtest result in **<1 day** | Team observation |
| SC-10 | Auditability | Any order reconstructable from event log alone, **<5 min** to answer "why" | Spot audit |

> **Deliberately absent: a P&L or return target.** Return targets in a success criteria list corrupt engineering decisions. The system's job is correct, fast, cheap, auditable execution of whatever the research says. Whether the research is any good is a separate question, measured separately.

---

# 2. Functional Requirements

## 2.1 Module Map (textual)

```
External World: Exchanges/Venues, Data Vendors
        |
        v
Ingestion Layer: Market Data Gateway (M1), Reference Data (M2)
        |
        v
Trading Core: Strategy Engine (M4) -> Portfolio Manager (M5)
              -> Risk Engine (M6) -> Order Management (M7)
              -> Execution Management (M8) <-> Venues
              Reconciliation (M9) <-> Venues & OMS
        |
        v
Platform Services: Storage/TimeSeries (M3), Event Bus (M10),
                    Observability (M11), Secrets/Identity (M12)
        |
        v
Research Plane: Backtest Engine (M13) -> Optimizer (M14)
                -> Validation/Stats (M15)
                (shares strategy code with M4)
        |
        v
Presentation: Read API (M16) -> Dashboard (M17)
              Kill Switch (M18) -- independent path --> EMS & Risk
```

Flow summary: Venue and vendor data enters through the Market Data Gateway, is normalized and persisted, and published on the event bus. The Strategy Engine consumes it and emits Signals. The Portfolio Manager sizes them into Order Intents. The Risk Engine synchronously approves or rejects. Approved intents become Orders in the OMS, dispatched to venue-specific Execution Management processes. Fills flow back through OMS into the Portfolio Manager. Reconciliation runs independently, continuously comparing believed state against venue truth. The same Strategy Engine code is reused by the Backtest Engine against historical data. The Dashboard is a read-only projection off the event bus and storage, with one exception: the independently-wired Kill Switch.

## 2.2 Feature Register

Priority: **P0** = MVP, cannot trade without it. **P1** = required before meaningful capital. **P2** = scale/quality. **P3** = institutional maturity.

### Data

| ID | Feature | Pri | Depends On |
|---|---|---|---|
| D-01 | Historical OHLCV ingestion (backfill) from venue REST | P0 | — |
| D-02 | Time-series storage with gap detection | P0 | D-01 |
| D-03 | Realtime candle/trade stream via WebSocket | P0 | D-02 |
| D-04 | WebSocket auto-reconnect with gap backfill on reconnect | P0 | D-03 |
| D-05 | Data validation (gaps, spikes, zero volume, OHLC invariants, monotonic timestamps) | P0 | D-02 |
| D-06 | Symbol/instrument reference data & precision rules (tick size, lot size, min notional) | P0 | — |
| D-07 | Point-in-time universe (which symbols were listed when) — **anti-survivorship-bias** | P1 | D-06 |
| D-08 | L2 order book snapshots + deltas | P1 | D-03 |
| D-09 | Funding rate / mark price / open interest (perps) | P1 | D-03 |
| D-10 | Trade tape (individual prints) | P1 | D-03 |
| D-11 | Multi-source cross-validation (two vendors, disagreement alerts) | P2 | D-05 |
| D-12 | Alternative data connectors (sentiment, on-chain, macro) | P3 | D-02 |
| D-13 | Corporate actions & adjustment factors | P3 | *equities only* |

### Strategy

| ID | Feature | Pri | Depends On |
|---|---|---|---|
| S-01 | Strategy plugin interface & registry | P0 | — |
| S-02 | Strategy lifecycle (init → warmup → running → paused → stopped) | P0 | S-01 |
| S-03 | Deterministic, side-effect-free signal generation | P0 | S-01 |
| S-04 | Per-strategy parameter schema + validation | P0 | S-01 |
| S-05 | Indicator library (vectorized + streaming/incremental variants) | P0 | — |
| S-06 | Strategy state persistence & recovery (restart-safe) | P1 | S-02 |
| S-07 | Multi-timeframe strategies | P1 | S-05 |
| S-08 | Multi-symbol / cross-sectional strategies | P1 | S-03 |
| S-09 | Strategy hot-reload without process restart | P2 | S-02 |
| S-10 | Per-strategy capital allocation & isolated P&L | P2 | S-08 |
| S-11 | Meta-strategy (allocator over strategies) | P3 | S-10 |
| S-12 | ML/model-based strategies with feature store & model registry | P3 | S-05 |

### Risk

| ID | Feature | Pri | Depends On |
|---|---|---|---|
| R-01 | **Pre-trade** risk checks — synchronous, in critical path, fail-closed | P0 | — |
| R-02 | Position sizing (fixed fractional, volatility-targeted) | P0 | R-01 |
| R-03 | Max position size per instrument (notional + units) | P0 | R-01 |
| R-04 | Max gross & net exposure | P0 | R-01 |
| R-05 | Max daily loss → auto-halt | P0 | R-01 |
| R-06 | Max drawdown from peak → auto-halt | P0 | R-05 |
| R-07 | **Kill switch** (manual, out-of-band, cancels all + flattens) | P0 | — |
| R-08 | Fat-finger checks (price collar vs. last, notional ceiling, qty sanity) | P0 | R-01 |
| R-09 | Order rate limiting / throttle (self-protection & venue-protection) | P0 | R-01 |
| R-10 | Leverage & margin limits | P1 | R-04 |
| R-11 | Per-strategy risk budgets | P1 | S-10 |
| R-12 | Correlation-aware exposure (cluster limits) | P2 | R-04 |
| R-13 | VaR / CVaR / stress scenarios | P2 | R-12 |
| R-14 | Liquidity risk (position vs. ADV) | P2 | D-10 |
| R-15 | Concentration limits by sector/theme | P3 | R-12 |

### Execution

| ID | Feature | Pri | Depends On |
|---|---|---|---|
| E-01 | Venue adapter interface (uniform contract) | P0 | — |
| E-02 | Order lifecycle state machine | P0 | E-01 |
| E-03 | **Idempotent submission via deterministic client order ID** | P0 | E-02 |
| E-04 | Market & limit orders | P0 | E-02 |
| E-05 | Cancel / cancel-all | P0 | E-02 |
| E-06 | Fill handling incl. partial fills | P0 | E-02 |
| E-07 | **Position & balance reconciliation vs. venue (venue is truth)** | P0 | E-06 |
| E-08 | Reconnect & state resync on startup / disconnect | P0 | E-07 |
| E-09 | Rejection handling & classification (retryable vs. terminal) | P0 | E-02 |
| E-10 | Rate-limit budget manager per venue | P0 | E-01 |
| E-11 | Paper trading venue adapter (same interface as live) | P0 | E-01 |
| E-12 | Stop / stop-limit / trailing stop | P1 | E-04 |
| E-13 | Post-only, IOC, FOK, reduce-only flags | P1 | E-04 |
| E-14 | Retry with exponential backoff + jitter, bounded | P1 | E-09 |
| E-15 | Execution algorithms (TWAP, VWAP, POV, Iceberg) | P2 | E-04 |
| E-16 | Smart order routing across venues | P3 | E-15 |
| E-17 | Transaction cost analysis | P2 | E-06 |

### Portfolio & Accounting

| ID | Feature | Pri | Depends On |
|---|---|---|---|
| P-01 | Position tracking (qty, avg entry, realized/unrealized) | P0 | E-06 |
| P-02 | Cash / balance ledger, double-entry | P0 | E-06 |
| P-03 | Fee & funding accounting | P0 | P-02 |
| P-04 | Mark-to-market valuation | P0 | P-01 |
| P-05 | Equity curve & drawdown series | P0 | P-04 |
| P-06 | Performance metrics (Sharpe, Sortino, Calmar, hit rate, PF, exposure) | P0 | P-05 |
| P-07 | Trade blotter | P0 | E-06 |
| P-08 | Cross-venue netted portfolio view | P2 | P-01 |
| P-09 | Tax lot tracking (FIFO/LIFO/spec-ID) | P3 | P-02 |
| P-10 | Performance attribution by strategy/factor | P3 | S-10 |

### Backtesting & Research

| ID | Feature | Pri | Depends On |
|---|---|---|---|
| B-01 | Event-driven backtest engine (**shares strategy code with live**) | P0 | S-01, D-02 |
| B-02 | Realistic fills: fees, spread, slippage model, partial fills | P0 | B-01 |
| B-03 | **Lookahead-bias prevention by construction** (read-only past-data view) | P0 | B-01 |
| B-04 | Deterministic & reproducible runs (seeded, versioned, hash-pinned data) | P0 | B-01 |
| B-05 | Metrics & tearsheet output | P0 | P-06 |
| B-06 | Parameter sweep / grid search | P1 | B-01 |
| B-07 | Walk-forward analysis | P1 | B-06 |
| B-08 | Monte Carlo (trade resampling, path simulation) | P1 | B-05 |
| B-09 | **Backtest run registry (every run logged — combats multiple-testing)** | P1 | B-04 |
| B-10 | Overfitting diagnostics (Deflated Sharpe, PBO, CSCV) | P1 | B-09 |
| B-11 | Bayesian / evolutionary optimization | P2 | B-06 |
| B-12 | Market-impact model | P2 | B-02 |
| B-13 | Latency simulation | P2 | B-02 |
| B-14 | Multi-strategy portfolio backtest | P2 | B-01, S-10 |
| B-15 | Regime/stress replay (2020-03, 2022-05, 2022-11, flash crashes) | P2 | B-01 |

### Platform & Ops

| ID | Feature | Pri | Depends On |
|---|---|---|---|
| O-01 | Structured logging w/ correlation IDs | P0 | — |
| O-02 | Immutable append-only event log (audit) | P0 | — |
| O-03 | Metrics + dashboards | P0 | O-01 |
| O-04 | Alerting (critical → phone; warning → chat) | P0 | O-03 |
| O-05 | Secrets management (no keys on disk/env in prod) | P0 | — |
| O-06 | Config management, environment separation, versioned | P0 | — |
| O-07 | Health checks & heartbeat / watchdog | P0 | O-03 |
| O-08 | Graceful shutdown (drain, cancel, persist) | P0 | E-05 |
| O-09 | Crash recovery from event log | P1 | O-02 |
| O-10 | Distributed tracing | P2 | O-01 |
| O-11 | Blue/green or canary deploy | P2 | O-06 |
| O-12 | Chaos testing (kill connections, inject latency, drop fills) | P2 | — |

### Dashboard

| ID | Feature | Pri | Depends On |
|---|---|---|---|
| U-01 | Live positions, P&L, equity curve | P0 | P-04 |
| U-02 | Order & fill blotter | P0 | P-07 |
| U-03 | **Kill switch button (independently authenticated)** | P0 | R-07 |
| U-04 | Strategy status & controls (pause/resume) | P1 | S-02 |
| U-05 | Risk limit display & utilization gauges | P1 | R-01 |
| U-06 | Backtest results browser & tearsheets | P1 | B-05 |
| U-07 | System health panel | P1 | O-03 |
| U-08 | Charting with entry/exit overlays | P2 | D-02 |
| U-09 | Alert center | P2 | O-04 |
| U-10 | Role-based access control | P2 | — |
| U-11 | Manual order entry (**hedge/liquidate only**) | P2 | E-04 |

## 2.3 MVP Cut Line

**MVP = every P0.** Concretely: ingest and store candles for a handful of symbols on one exchange; run one strategy in a backtest and in live with the same code; pre-trade risk that can say no; submit and reconcile orders idempotently; a kill switch; a page showing what's happening; and a log that lets you reconstruct any of it.

Everything else is deferred. Note especially what is **not** in MVP: multiple venues, multiple strategies, order books, execution algorithms, VaR, optimization, hot-reload, RBAC. Each is defensible on its own; together they are a two-year detour before you learn whether you can trade profitably at all.

## 2.4 Dependency Critical Path

```
Reference Data (D-06)
      -> Historical Ingest (D-01)
      -> Storage + Validation (D-02/05)
      -> Backtest Engine (B-01/03)
      -> Strategy Interface (S-01)
      -> Fill Model (B-02)
      -> Metrics (B-05)
      -> [GATE] Strategy shows edge OOS?
             NO  -> loop back to Strategy Interface (S-01)
             YES -> Realtime Data (D-03/04)
                    -> Risk Engine (R-01)
                    -> OMS + Idempotency (E-02/03)
                    -> Paper Adapter (E-11)
                    -> Reconciliation (E-07)
                    -> Kill Switch (R-07)
                    -> [GATE] Paper matches backtest?
                          NO  -> loop back to Fill Model (B-02)
                          YES -> Live Adapter, Minimum Capital
```

The two gates are **gates, not checkpoints.** You do not pass them by deciding to. Note that a "no" at the paper-trading gate loops back to the *fill model*, not to the strategy — if paper diverges from backtest, the simulator is lying, and fixing the strategy would be fixing the wrong thing.

---

# 3. Non-Functional Requirements

## 3.1 Performance

Latency budgets are stated per stage, measured p99, for the mid-frequency target.

| Stage | Budget (p99) | Rationale |
|---|---|---|
| WS message → normalized internal event | 1 ms | Parsing and validation only |
| Event → strategy evaluation complete | 20 ms | Indicators are incremental, not recomputed |
| Signal → risk decision | 5 ms | In-memory limits, no I/O in the hot path |
| Risk approval → order on the wire | 10 ms | Serialization + HTTP/WS send |
| **Total: tick → order sent** | **< 50 ms** | Dominated by network to venue (20–100 ms), which you don't control |
| Reconciliation cycle | < 5 s | Every 60 s |
| Backtest throughput | ≥ 1 M bars/min/core | Otherwise research velocity collapses |

**The critical insight**: tick-to-order latency will be *dominated by geographic distance to the exchange*, not by code. Optimizing Python from 20 ms to 2 ms is worthless if the server is 80 ms from the matching engine. Colocate near the venue's cloud region (Binance → AWS Tokyo `ap-northeast-1`; Coinbase → AWS `us-east-1`) *before* optimizing a single line. This is why Python is acceptable and why a rewrite is not warranted until the numbers say so.

**Throughput targets**: 10k market-data messages/sec sustained (Year 1), 100k/sec (Year 3, all venues, L2). Order rate is bounded by venue limits, typically 5–50/sec — never the bottleneck.

## 3.2 Scalability

Three orthogonal axes, and they scale differently:

| Axis | MVP | Year 3 | Scaling Strategy |
|---|---|---|---|
| Symbols | 5–20 | 500+ | Horizontal shard by symbol; strategy evaluation is embarrassingly parallel |
| Strategies | 1 | 20+ | Process-per-strategy; isolation is worth the overhead |
| Venues | 1 | 5–10 | One adapter process per venue (fault + rate-limit isolation) |
| Data volume | ~GB | ~10s TB | Hot Postgres/Timescale → cold Parquet on object store |
| Capital | Small | Millions | **Does not scale linearly.** Market impact means strategy capacity is finite and must be *measured*. |

**The capital axis is the one people forget.** A strategy backtesting at 200% annualized on $10k may have negative expectancy at $5M because it eats the book. Strategy capacity must be an explicit, estimated, and monitored quantity — a `capacity_usd` field enforced by the risk engine, not a footnote.

**Vertical before horizontal.** A single well-tuned machine (32 cores, 128 GB RAM, NVMe) handles a shocking amount of mid-frequency trading. Distribute only when profiling proves you must. Every distribution boundary added introduces partial failure, and partial failure in a trading system means positions you don't know you have.

## 3.3 Reliability

| Property | Requirement |
|---|---|
| Order idempotency | Deterministic `client_order_id` derived from `(strategy_id, symbol, intent_seq, side)`. Resubmitting the same intent must never create a second order. |
| Exactly-once fill processing | Fills deduplicated by `(venue, venue_fill_id)`. Idempotent by unique constraint at the storage layer. |
| Source of truth | **The venue is always right.** Local state is a cache. Reconciliation is continuous, not a startup task. |
| Crash recovery | Rebuild in-memory state by replaying the event log; then reconcile against the venue; then and only then resume. |
| No silent failure | Every error is logged, counted, and classified. An unhandled exception in a strategy pauses that strategy — it never crashes the engine, and it never silently continues. |
| Fail-closed | If the risk engine is unavailable, unreachable, or uncertain → **reject the order**. Never fail open. |
| Money type | `Decimal` everywhere. **Float is banned** in any code path touching price, quantity, or balance. Enforced by lint rule and type checker, not by discipline. |

The fail-closed rule deserves emphasis. Every distributed-systems instinct from web engineering says "degrade gracefully, serve stale data, keep the request alive." In trading, the graceful degradation of a risk check is *rejecting the trade*. A missed opportunity costs an unrealized gain. A missed risk check costs the account.

## 3.4 Availability

| Component | Target | Approach |
|---|---|---|
| Market data ingestion | 99.9% | Redundant WS connections, auto-reconnect, REST backfill on gap |
| Trading core | 99.5% (Y1) → 99.9% (Y2) | Single active instance + fast restart; **active-active is dangerous** |
| Reconciliation | 99.99% | Runs independently; must survive core outage |
| Dashboard | 95% | Non-critical, read-only |
| **Kill switch** | **99.999%** | **Independent process, independent credentials, independent network path.** Must work when everything else is dead. |

**Rejected: active-active HA for the trading core.** Two instances that both think they're leader will double the position. The failure mode of leader election under network partition is exactly the scenario in which markets are most volatile. Accept a 30-second restart window. Use active-passive with a **single-writer lease** and, critically, an exchange-side guard: on startup, cancel all open orders before doing anything else. A trading system that is down loses opportunity; a trading system that is doubly-live loses capital.

## 3.5 Maintainability

- **Hexagonal architecture** (ports & adapters). Domain logic — order state machine, risk rules, position math — depends on *nothing*. Not on the database, not on the exchange SDK, not on the web framework. Those are adapters plugged into ports.
- **Dependency rule**: `domain` ← `application` ← `infrastructure`. Enforced mechanically by an import-linter in CI, because architectural rules that aren't enforced by tooling decay within a quarter.
- Strict typing, no untyped defs, `mypy --strict` on `domain/` and `application/`.
- Cyclomatic complexity ceiling per function. Module size ceiling.
- **ADRs for every significant decision**, in-repo, never deleted — superseded, with a link forward.
- Every module has a documented owner and a runbook.

## 3.6 Security

Trading systems are uniquely attractive targets: the attacker's payoff is immediate and liquid.

| Threat | Control |
|---|---|
| API key theft | Keys in HashiCorp Vault or cloud KMS. **Never** in `.env`, never in git, never in an environment variable in prod. Fetched at runtime, held in memory, zeroized on shutdown. |
| Key misuse | **Withdrawal permissions disabled** on every exchange key. Trade + read only. IP allowlist at the exchange. This makes key theft an annoyance, not a catastrophe. |
| Insider / accidental fat-finger | Two-person rule for changing risk limits in prod. Limits are config-as-code, reviewed, versioned. |
| Dashboard compromise | Dashboard is **read-only** except kill switch. Kill switch is *safe* to trigger. There is no "place order" button that an XSS can reach. |
| Dependency supply chain | Pinned lockfiles, hash verification, `pip-audit`/`osv-scanner` in CI, Dependabot, no unvetted transitive deps in the trading core. |
| Code injection via strategy plugins | Strategies are trusted code from the own repo. **Never load a strategy from an untrusted source.** If third-party strategies are ever allowed, they run in a separate sandboxed process with no network and no credentials — a different product. |
| Data at rest | Full-disk encryption; TDE on Postgres; encrypted object storage. |
| Data in transit | TLS 1.3 everywhere; certificate pinning for venue endpoints. |
| Audit | Append-only, tamper-evident event log (hash-chained). Separate write-once storage. |
| Time manipulation | NTP/chrony with monitored drift; alert on >50 ms. Signed exchange timestamps preferred over local clocks. |

> **The single highest-ROI security control in this entire document is one checkbox: disable withdrawals on exchange API keys.** It converts the worst outcome from "attacker drains the account" to "attacker can make bad trades," which risk limits already constrain. Do it before writing a line of code.

## 3.7 Observability

Three pillars plus one that's specific to trading.

1. **Logs** — structured JSON, correlation ID threading `signal → intent → order → fill`.
2. **Metrics** — RED (Rate/Errors/Duration) for services; plus domain metrics: signals/min, orders/min, rejection rate by reason, fill rate, realized slippage bps, exposure %, drawdown %, risk-limit utilization %, data staleness (seconds since last tick per symbol).
3. **Traces** — distributed tracing across the order path.
4. **Financial reconciliation as observability.** The most important monitor is not CPU. It is: *does the believed position equal the exchange's position?* Any drift is a P1 page, always, without exception.

**Alert taxonomy** (alert fatigue is a real risk in a system with a 24/7 market):

| Severity | Examples | Route | SLA |
|---|---|---|---|
| **P1 — Page** | Position drift; kill switch fired; risk breach; auth failure; data feed down >60 s | Phone, wake the human | 5 min |
| **P2 — Urgent** | Order rejection rate >5%; slippage >2× model; strategy exception | Chat + email | 1 hr |
| **P3 — Info** | Backfill complete; strategy paused; deploy done | Chat | Next day |

## 3.8 Extensibility

The extension points, in order of how often they'll be used:

1. **New strategy** — drop a class implementing the strategy port; register; configure. **No core changes.**
2. **New venue** — implement the venue port. **No core changes.**
3. **New data source** — implement the data-source port.
4. **New risk rule** — implement the rule port; add to the chain.
5. **New execution algo** — implement the execution-algo port.
6. **New asset class** — *this one does require core changes.* See §3.8.1.

### 3.8.1 The Asset-Class Abstraction — Where the Seam Actually Goes

The naive design makes `Instrument` a single class with nullable fields (`expiry: date | None`, `strike: Decimal | None`, `underlying: str | None`) and every consumer branches on `asset_class`. This is the design that rots. Within a year the risk engine has fourteen `if asset_class ==` branches and nobody can safely change any of them.

The correct seam:

- **`Instrument` is a small, closed core**: identity, venue, base/quote currency, tick size, lot size, min notional, trading calendar. Facts every tradeable thing has.
- **Asset-class specifics live in a sealed variant hierarchy**: `Spot`, `PerpetualSwap` (funding rate, mark price), `DatedFuture` (expiry, roll rules), `Equity` (corporate actions, halts), `Option` (strike, greeks). Consumers **pattern-match exhaustively**, so the type checker flags every place that needs updating when a variant is added. The compiler becomes the migration checklist.
- **Anything that cannot be expressed uniformly gets its own port.** Corporate-action handling is an equities port that simply does not exist for crypto. Do not invent a no-op `CorporateActionHandler` for Binance to satisfy an interface. That is abstraction for its own sake.

The rule: **abstract over the things that are genuinely the same (an order has a side, a size, a price, a state) and refuse to abstract over the things that merely rhyme.** A perpetual swap and a share of AAPL are both "things you can be long," and the resemblance stops almost immediately after that.

## 3.9 Testing

| Layer | Coverage Target | Tooling | Notes |
|---|---|---|---|
| Domain unit tests | **≥95%** | pytest | Pure functions, fast, no I/O. This is where the money is. |
| Property-based tests | Critical invariants | Hypothesis | Position math, order state machine, risk rules |
| Integration | ≥80% | pytest + testcontainers | Real Postgres, real Redis |
| Venue contract tests | 100% of adapters | pytest + VCR / recorded fixtures | Replay real exchange responses, incl. every error shape |
| Backtest regression | Golden-file | pytest | A known strategy on known data must produce a bit-identical result. Catches silent numeric drift. |
| Paper-trade soak | Continuous | — | 30 days minimum before live |
| Chaos | Monthly | Custom harness | Kill WS mid-order; return 500s; return duplicate fills; return a fill for an order never sent; skew the clock |
| Load | Pre-release | Locust / custom | 10× expected message rate |

**Invariants worth property-testing** (these are the ones that catch real bugs):
- Sum of fills for an order ≤ order quantity, always.
- Position quantity = Σ(signed fill quantities), always. No exceptions, no rounding drift.
- Cash after a trade = cash before − notional − fees, exactly, in `Decimal`.
- No order transitions out of a terminal state (`FILLED`, `CANCELED`, `REJECTED`, `EXPIRED`).
- Realized + unrealized P&L is invariant under FIFO vs. LIFO lot ordering for *total* (though not per-lot).
- Applying the same fill event twice produces the same state as applying it once.

That last one is the property that saves you at 3 a.m. during an exchange outage.

## 3.10 Documentation

Every module: purpose, ports, invariants, failure modes, runbook. ADRs, in-repo, immutable. Strategy specs (hypothesis, economic rationale, expected regime, expected capacity, decay indicators — *what would make this strategy stop being traded?*, written **before** the backtest, so it can't be rationalized afterward). Incident postmortems, blameless, permanent. And an onboarding doc measured by a hard target: a new engineer runs a backtest on day one.

## 3.11 Deployment

- Docker images, multi-stage, distroless base, non-root.
- **Immutable, hash-pinned images.** No `latest` tags. No mutable deploys.
- Infrastructure as code (Terraform). No hand-clicked resources.
- **Deploy only when flat, or with a documented, rehearsed procedure for deploying with open positions.**
- Every deploy: automatic order-cancel-all → verify flat-or-expected → start → reconcile → resume.
- Rollback = redeploy previous image hash. Tested, not theoretical.
- Zero-downtime deploy is a **non-goal** in Year 1. A 60-second maintenance window with all positions flat is safer than any clever hot-swap, and it is free.

---

# 4. Architecture

## 4.1 Style Decision

| Style | Verdict | Reasoning |
|---|---|---|
| Microservices | **Rejected for MVP** | Distributed transactions across order/position/risk are a nightmare. Network partitions become financial exposure. A 1–3 engineer team does not have the coordination problem microservices solve. |
| Modular Monolith | **✅ Selected** | Single deployable, in-process communication (no serialization, no partial failure), strict module boundaries enforced at compile/lint time. Extract to services later at *already-defined seams*. |
| Event-Driven (internal) | **✅ Selected (within the monolith)** | The domain is genuinely event-shaped. Gives replay, audit, and backtest/live symmetry for free. |
| Event Sourcing (trading core only) | **✅ Selected, scoped** | The order/position/fill lifecycle is append-only by nature. Full CQRS+ES everywhere would be dogma; here it's a natural fit. |
| Serverless | **Rejected** | Cold starts. No persistent WS connections. No long-lived in-memory state. Wrong shape entirely. |
| Actor model | **Considered, partial** | Per-strategy and per-venue isolation are actor-shaped. Achieved via processes + async tasks without importing a full actor framework. |

**Selected: a modular monolith with an internal event bus, an event-sourced trading core, and hexagonal module boundaries — deployed as a small number of processes split along fault-isolation lines, not along microservice lines.**

The distinction matters. Splitting the venue gateway into its own process is not microservices; it's fault isolation. A venue adapter that hits a rate limit, or hangs on a socket read, or leaks memory in a vendor SDK, must not take down the risk engine. That is a *blast-radius* decision. Splitting `PositionService` from `OrderService` into separate services with a network call and a distributed transaction between them is microservices, and it buys nothing but a two-phase commit implemented badly.

## 4.2 Layers

```
Layer 4 · Interface / Adapters IN
    REST Read API | Dashboard SPA | CLI | Kill Switch Service
              |
              v  (depends on)
Layer 2 · Application / Use Cases
    Orchestrators | Event Handlers | Ports (Interfaces) | Transaction Boundaries
              |
              v  (depends on)
Layer 1 · Domain — pure, zero dependencies
    Order Aggregate + State Machine | Position/Portfolio | Risk Rules
    Instrument Model | Money/Decimal Value Objects | Domain Events

Layer 3 · Infrastructure / Adapters OUT  (implements Layer 2 ports)
    Venue Adapters | Market Data Adapters | Repositories (Postgres)
    Cache (Redis) | Event Store | Secrets (Vault) | Metrics/Log Exporters
```

**The dependency rule, absolutely:** arrows point inward. The domain layer imports nothing from the outer layers — not the ORM, not `httpx`, not `pydantic` if avoidable. It is plain Python objects and pure functions. This is what makes the risk engine testable in microseconds, and it's what allows unit-testing "what happens when a fill arrives for an order that was already canceled" without standing up a database or an exchange.

**Litmus test:** the entire domain test suite must run with no Docker, no network, no database, in under two seconds. If it can't, a dependency has leaked inward, and it should be fixed that day.

## 4.3 Deployment Topology

```
Process: market-data-gateway
    WS clients, Normalizer, Gap detector
        --market events--> [Event Bus: NATS/Redis Streams]

Process: trading-core
    Strategy Engine, Portfolio, Risk Engine, OMS
        <--market events-- [Event Bus]
        --order commands--> [Event Bus]

Process: venue-gateway (one per venue)
    EMS, Rate limiter, Venue adapter
        <--order commands-- [Event Bus]
        --fills, acks--> [Event Bus] --> trading-core
        <--REST/WS--> Exchange

Process: reconciler
    Independent venue polling, direct REST to Exchange
    (bypasses the event bus entirely)

Process: kill-switch
    Separate credentials, separate network, minimal deps
    ==EMERGENCY: direct to Exchange, bypasses core==

Process: api + dashboard
    Read-only projections from Postgres + Redis

Infrastructure:
    PostgreSQL + TimescaleDB | Redis | NATS/Redis Streams | Object Store (Parquet)
```

Six process types. The kill switch talks **directly to the exchange**, deliberately bypassing every piece of the platform's own infrastructure. It shares no library, no config file, and no credential with the trading core. When it's most needed, the trading core is exactly the thing that has failed — a kill switch that routes through the failed component is a kill switch that does not exist. It should be boringly simple: read a key from Vault, call `cancel_all`, call `close_all_positions`, log, exit. Under 200 lines. Reviewed by everyone. Drilled monthly.

## 4.4 Communication Patterns

| From → To | Pattern | Transport | Why |
|---|---|---|---|
| Market data → Strategy | Pub/Sub, fire-and-forget | NATS / Redis Streams | Many consumers, loss-tolerant (next tick is coming), backpressure via drop-oldest |
| Strategy → Portfolio | In-process call | Direct | Same transaction boundary; needs current positions synchronously |
| Portfolio → Risk | **Synchronous, blocking** | Direct | **Risk must be in the critical path.** Async risk is not risk; it's a report. |
| Risk → OMS | In-process, transactional | Direct | Approval and order creation must be atomic |
| OMS → Venue Gateway | Command, at-least-once + idempotency key | NATS (durable) | Survives gateway restart; idempotency makes redelivery safe |
| Venue → OMS | Event, at-least-once + dedup | NATS (durable) | **Never lose a fill.** Dedup on `venue_fill_id`. |
| Anything → Dashboard | Pub/Sub, lossy | WebSocket | Cosmetic; loss is fine |
| Reconciler → Everything | Poll | Direct REST to venue | Deliberately independent of the bus |

**The one thing to hammer:** risk is synchronous and blocking. There is enormous architectural pressure to make it async — it's the fashionable choice, it decouples nicely, it looks better on a diagram. It is wrong. If risk evaluates *after* the order is sent, then by construction there exists a window in which an order that violates the mandate is live in the market. That window is where account-ending losses live. Take the 5 ms.

## 4.5 Data Flow — Live (sequence)

1. Exchange sends a WS trade/candle-close event.
2. Market Data Gateway validates, normalizes, stamps both `exchange_ts` and `local_ts`.
3. Persists `MarketDataReceived` to the event store; publishes `CandleClosed` to the bus.
4. Strategy consumes it, updates incremental indicators, evaluates, and may emit `Signal(LONG, confidence=.8)`. Persists `SignalGenerated`.
5. Strategy submits the signal to the Portfolio Manager.
6. Portfolio Manager computes current position vs. target, sizes the delta via the vol-target model, and submits an `OrderIntent` to the Risk Engine **synchronously**.
7. Risk Engine runs its rule chain (fail-closed).
   - **If rejected**: persists `RiskRejected` and returns. Rejections are first-class events — a high rejection rate signals a misconfigured strategy.
   - **If approved**: persists `RiskApproved`, returns `ApprovedIntent` to OMS.
8. OMS builds a deterministic `client_order_id`, persists `OrderCreated` **before** publishing the command (see note below), then publishes `OrderCommand` to the bus.
9. Venue Gateway receives it, checks its rate-limit budget, and sends `POST /order` with the `client_order_id`.
10. Exchange acknowledges with a `venue_order_id`. Venue Gateway publishes `OrderAcked`; OMS persists it.
11. Exchange later sends a fill over WS. Venue Gateway dedupes on `venue_fill_id`, publishes `FillReceived`.
12. OMS persists `OrderFilled` and applies the fill to the Portfolio Manager, which updates position, cash, and realized P&L, and persists `PositionChanged`.

Note step ordering around persistence: **`OrderCreated` is written to the event store before the command is published.** If the process dies between the two, recovery replays the log, sees a created-but-unacked order, and queries the venue by `client_order_id` — which either finds it (adopt) or doesn't (retry). This is only safe because the ID is deterministic. Non-deterministic IDs make this recovery path impossible, which is why idempotency is an architectural property and not an implementation detail.

## 4.6 Execution Flow — Failure Paths (order state machine)

States: `PendingNew → Sent → {Acked | Rejected | Unknown}`. From `Unknown`: query by `client_order_id` → found → `Acked`; not found + terminal → `Rejected`; query fails → retry with backoff; retries exhausted → `Escalated` (human required). From `Acked`: `PartiallyFilled` (repeatable) or `Filled`; or `PendingCancel`, which itself can race to `Canceled`, `Filled`, or `PartiallyFilled`. `Acked` can also transition to `Expired` on TIF elapse. `Filled`, `Canceled`, `Rejected`, `Expired` are terminal.

`UNKNOWN` is the state that separates a real trading system from a toy. It arises constantly: HTTP timeouts, 502s from an overloaded exchange, WS disconnects mid-submit. The naive system retries and double-fills. The correct system says: *an order was sent with a deterministic client order ID; ask the exchange whether it knows about it; do not act until it answers.* And if the exchange won't answer after bounded retries, the system stops and **wakes a human**, because a silent guess here is how a position ends up materially wrong.

Note also `PENDING_CANCEL → FILLED`. A cancel and a fill cross in flight. Any state machine that treats this as an error will, on a volatile day, hit it dozens of times.

## 4.7 Backtest / Live Symmetry

The single most valuable architectural property in this document, and the one most systems get wrong.

**Shared code (identical, no forks, no `if is_backtest`):** Strategy Logic, Indicators, Position Sizing, Risk Rules, Portfolio Math, Order State Machine.

**Swapped adapters (the only difference):**
- Live uses `RealClock` + `WebSocketFeed` + `BinanceAdapter` (or similar).
- Backtest uses `SimulatedClock` + `HistoricalFeed` + `SimulatedVenue` (fills, fees, slippage).

**Three rules, non-negotiable:**

1. **`if backtest:` is banned in the domain layer.** Enforced by lint. The moment a conditional appears, the two paths begin to diverge and the backtest starts measuring a program that is never actually run.
2. **The strategy cannot access a wall clock.** It receives time from the injected `Clock` port. A strategy that calls `datetime.now()` is nondeterministic and will produce a different backtest on every run.
3. **The strategy receives a `MarketDataView` that structurally cannot return future data.** Not "careful discipline not to" — *structurally cannot*. The view holds a cursor at the simulated present and refuses to index past it. Lookahead bias is thereby a type error rather than a discipline problem, which is the only way to actually eliminate it. Every quant who claims to be "careful about lookahead" has shipped lookahead at some point.

---

# 5. Modules

Each module below: purpose, responsibilities, inputs, outputs, dependencies, risks, and future improvements.

---

### M1 · Market Data Gateway

**Purpose.** Convert the chaos of heterogeneous exchange feeds into one clean, validated, timestamped internal event stream.

**Responsibilities.** Maintain persistent WS connections; subscribe/resubscribe; normalize venue payloads into canonical events; stamp both `exchange_ts` and `local_recv_ts`; detect sequence gaps; trigger REST backfill; detect stale feeds; apply backpressure.

**Inputs.** Venue WS streams, venue REST (backfill), subscription config.
**Outputs.** `CandleClosed`, `TradePrinted`, `BookUpdated`, `FundingRateUpdated`, `FeedStale`, `GapDetected` — to bus and to storage.
**Dependencies.** Venue adapters, event bus, time-series store.

**Risks.**
- *Silent staleness* — the socket is open, no data flows, no error is raised. The system happily trades on a five-minute-old price. **Mitigation: a per-symbol watchdog that alerts on `now − last_tick > threshold`, independent of connection state.** This is the most common way a live system quietly goes insane.
- *Clock skew* — exchange timestamp vs. local clock. Always record both. Never mix them in one comparison.
- *Out-of-order / duplicate messages* — buffer and reorder by sequence; dedupe.
- *Reconnect gaps* — the WS reconnects and forty seconds of trades were silently missed. **Always backfill via REST on reconnect. Always.**
- *Rate-limit bans* from over-eager resubscription. Exponential backoff with jitter.

**Future.** Redundant feeds from two providers with disagreement detection; hardware timestamping; L3 book; multicast for traditional venues.

---

### M2 · Reference Data Service

**Purpose.** Own the answer to "what is this instrument, and what are its rules?"

**Responsibilities.** Symbol master; tick size, lot size, min notional, max order size; fee schedule and tiers; trading calendar and halts; contract specs (expiry, multiplier, funding interval); **point-in-time listing/delisting history**.

**Inputs.** Venue `exchangeInfo` endpoints, manual overrides.
**Outputs.** `Instrument` objects; validation predicates; a point-in-time universe query.
**Dependencies.** Venue adapters, storage.

**Risks.**
- **Survivorship bias.** Backtesting on today's symbol list excludes every coin that went to zero and got delisted, making the backtest fiction. This module's point-in-time universe is the *only* defense, and it must be built early because the data is not recoverable retroactively. **Start capturing daily universe snapshots on day one, before it's needed.**
- Silent spec changes: exchanges change tick sizes without notice; orders start getting rejected. Poll daily, diff, alert.
- Rounding: an order rounded to the wrong precision is rejected — or worse, silently truncated to a different size than intended.

**Future.** Cross-venue symbol mapping (`BTCUSDT` vs `BTC-USD` vs `XBTUSD`); FIGI/ISIN for equities; corporate action feed.

---

### M3 · Storage / Time-Series Layer

**Purpose.** Persist market data, events, and state durably, and serve them fast in two very different access patterns.

**Responsibilities.** OHLCV and tick storage; event store (append-only); operational state; hot/cold tiering; compression; retention; **point-in-time snapshot queries** (`state as of T`).

**Risks.** Write amplification at tick granularity; unbounded growth (a year of L2 for 100 symbols is tens of TB); slow analytical scans over an OLTP store; and — subtly — **backfilled corrections that silently change a historical backtest's results.** Never mutate history in place; version it.

**Future.** Columnar tier (ClickHouse/DuckDB over Parquet); tiered cold storage; a materialized feature store.

---

### M4 · Strategy Engine

**Purpose.** Host strategy plugins; feed them data; collect signals. Nothing else. It is a container, not a decision-maker.

**Responsibilities.** Discovery and registration; lifecycle management; data routing (only what's subscribed); warmup; **fault isolation — one strategy's exception must never touch another's**; state persistence; per-strategy metrics.

**Inputs.** Market events, strategy configs, portfolio state (read-only view).
**Outputs.** `Signal` objects. **Not orders.** A strategy expresses an opinion; it does not touch the market.
**Dependencies.** Event bus, indicator library, portfolio read model.

**Risks.**
- *Strategies that reach outside the sandbox* — call an HTTP endpoint, read a file, use `datetime.now()`. This destroys determinism and reproducibility. Restrict by convention, code review, and — ideally — a static check.
- *Slow strategies* blocking the event loop. Enforce a per-evaluation timeout; a strategy that exceeds it gets paused, not tolerated.
- *Unbounded memory* from indicator history. Fixed-size ring buffers.
- *State corruption across restart.* Version the state schema.

**Future.** Hot reload; per-strategy process isolation; a research SDK identical to the production interface; ML model serving with a model registry.

---

### M5 · Portfolio Manager

**Purpose.** Own the truth about what is held, what it's worth, and what the target is.

**Responsibilities.** Position tracking (qty, avg entry, realized/unrealized); cash ledger, double-entry; fee and funding accrual; mark-to-market; equity curve; **converting a strategy's `Signal` into a concrete `OrderIntent` via a sizing model**; netting across strategies that want the same instrument.

**Inputs.** Fills, marks, funding events, signals.
**Outputs.** `OrderIntent`; `PortfolioSnapshot`; `PositionChanged`.

**Risks.**
- **Float arithmetic.** Repeated float addition of fills drifts. After 10,000 trades the believed position is `0.30000000000000004` and the reconciler screams. `Decimal`, always. This is not pedantry; it is the single most common source of position drift in amateur systems.
- **Two strategies, opposite signals, same instrument.** Net them? Trade both? Let one win? **This is a policy decision that must be made explicitly (§16), not discovered in production.** Netting saves fees but destroys per-strategy attribution.
- Avg-entry-price semantics under partial closes are genuinely subtle. Test with property-based tests.
- Funding payments on perps are a real, recurring, easily-forgotten cash flow that will silently make P&L wrong.

**Future.** Multi-currency with FX conversion; tax lots; cross-margin modeling; attribution.

---

### M6 · Risk Engine

The most important module. See §10.

---

### M7 · Order Management System (OMS)

**Purpose.** The system of record for every order, from birth to terminal state.

**Responsibilities.** Order aggregate + state machine; **deterministic `client_order_id` generation**; state transitions with strict validation (illegal transitions raise, loudly); fill application and dedup; parent/child order relationships (for algos); the order book *of the platform's own orders*; timeout and `UNKNOWN`-state resolution.

**Inputs.** `ApprovedIntent`, venue acks, fills, rejections, cancel confirmations.
**Outputs.** `OrderCommand`; `OrderStateChanged` events; the authoritative order history.

**Risks.**
- **Duplicate submission.** Solved only by deterministic IDs plus venue-side idempotency. Never by "retrying carefully."
- **Lost fills.** A fill arriving during a restart. Solved by: durable bus, dedup, *and* the reconciler as a backstop. Belt and suspenders — this is one place redundancy is warranted.
- **The `PENDING_CANCEL` race.** Handled explicitly in the state machine.
- **Fills for unknown orders.** It happens (restart, adoption of a pre-existing order). Never discard them. Log, alert, adopt.

**Future.** Parent/child for execution algos; FIX protocol; order amendment (modify-in-place) where venues support it.

---

### M8 · Execution Management System / Venue Gateway

**Purpose.** Speak each venue's dialect. Contain each venue's failures.

**Responsibilities.** Implement the `Venue` port; translate canonical orders → venue payloads; sign requests; manage rate-limit budgets; classify errors (retryable vs. terminal vs. **unknown**); normalize fills; expose venue health.

**Risks.** Rate-limit bans (an IP ban mid-position is a real, bad day); venue-specific quirks (Binance's `newClientOrderId` length limits, weight-based rate limits, `-1021` timestamp errors); **misclassifying an unknown error as retryable, thereby double-submitting**; silent API changes.

**Future.** FIX; smart order routing; venue-latency-aware routing; co-location.

---

### M9 · Reconciliation Service

**Purpose.** Continuously prove that believed state equals the exchange's state. Run this even when it seems unnecessary.

**Responsibilities.** Poll venue positions, balances, and open orders on a fixed cadence; diff against internal state; classify drift; **auto-halt on material drift**; adopt orphan orders; reconcile at startup, after every reconnect, and after every deploy.

**Risks.** Racing against in-flight orders (use a quiescence window, or reconcile only settled state); false positives causing needless halts (tune thresholds, but bias toward halting); and the worst one — **a reconciler that silently overwrites local state with venue state, masking the bug that caused the drift.** Drift is a symptom. Halt, alert, and *investigate*. Do not paper over it.

**Future.** Three-way reconciliation (internal, venue API, venue statement/CSV); automated daily settlement reports.

---

### M10 · Event Bus

**Purpose.** Decouple producers from consumers; provide durability where correctness demands it.

**Responsibilities.** Pub/sub for market data (lossy, fast, drop-oldest under backpressure); durable queues for orders and fills (at-least-once, persisted); dead-letter queue; replay from an offset.

**Risks.** Backpressure — a slow consumer stalls the whole bus. Market data must be droppable; orders must never be. **These get different channels with different guarantees.** Also: at-least-once means duplicates, which means every consumer of order/fill events must be idempotent. Not most of them. Every one.

---

### M11 · Observability | M12 · Secrets & Identity

Covered in §3.6 and §3.7. Both are P0. Neither is glamorous. The secrets module in particular is one afternoon of work that eliminates an entire class of catastrophe.

---

### M13–M15 · Backtest, Optimizer, Validation

See §12.

### M16–M17 · Read API & Dashboard

See §13. Note: **the API is read-only over a projection.** The dashboard is not in the trading critical path and must never be able to block, slow, or corrupt it.

### M18 · Kill Switch

See §4.3 and §10.5. The most important 200 lines in the repository.

---

# 6. Technology Decisions

## 6.1 ADR-001 — Primary Language

**Status:** Proposed | **Deciders:** CTO, Lead Quant

**Context.** Mid-frequency (10–500 ms decision budget). Small team. Research velocity is the binding constraint on finding alpha; execution latency is *not* the binding constraint on capturing it (§3.1). The same code must run in backtest and live.

| Option | Latency | Numerics Ecosystem | Research Velocity | Talent Pool | Safety | Verdict |
|---|---|---|---|---|---|---|
| **Python 3.12+** | Adequate (µs–ms) | **Unmatched** (numpy, pandas, polars, scipy, statsmodels, sklearn, torch) | **Highest** | Largest in quant | Medium (mypy strict, Decimal) | ✅ **Selected** |
| Rust | Excellent | Immature | Low | Small | Highest | Selected for *hot spots later*, via PyO3 |
| C++ | Excellent | Mature but painful | Lowest | Small, expensive | Low (memory unsafety) | ❌ |
| Go | Very good | **Poor** — no real numerics | Medium | Medium | Medium | ❌ Numerics disqualify it |
| Java/Kotlin | Very good (after JIT warmup) | Medium | Low | Medium | High | ❌ GC pauses; ecosystem mismatch |
| C# | Very good | Medium | Medium | Medium | High | ❌ Ecosystem mismatch |
| TypeScript | Poor | Poor | Medium | Large | Medium | ❌ |

**Decision.** **Python 3.12+**, `asyncio`, `mypy --strict` on domain and application layers. Rust via PyO3 for identified hot spots **only when profiling proves the need** — realistically the backtest inner loop, and perhaps L2 book maintenance, and nothing else.

**Trade-off, stated honestly.** A ~10–100× single-threaded slowdown versus Rust is accepted in exchange for a ~5× improvement in research iteration speed. That trade is correct **because and only because** the workload is latency-tolerant. Network RTT to the exchange (20–100 ms) exceeds the entire compute budget by an order of magnitude. Optimizing Python here would be optimizing 2% of the total.

**The trade inverts** if the platform pursues market making, latency arbitrage, or sub-millisecond strategies. At that point this ADR is superseded, not amended, and the rewrite is scoped to the execution path only.

**Consequences.** *Easier:* research, hiring, library reuse, notebook↔production parity. *Harder:* true parallelism (GIL — mitigate with processes; note the GIL is largely irrelevant for I/O-bound trading); runtime type errors (mitigate with strict typing + Pydantic at boundaries); deployment size. *To revisit:* if p99 strategy-evaluation latency exceeds 20 ms, or if a latency-sensitive strategy class is adopted.

## 6.2 ADR-002 — Primary Database

**Context.** Two access patterns that pull in opposite directions: high-frequency time-series appends with range scans (analytical), and transactional order/position state with strong consistency (operational).

| Option | Time-Series | Transactional | Ops Burden | Ecosystem | Verdict |
|---|---|---|---|---|---|
| **PostgreSQL + TimescaleDB** | Very good (hypertables, compression, continuous aggregates) | **Excellent** (full ACID) | Low — it's just Postgres | Excellent | ✅ **Selected** |
| InfluxDB | Excellent | None | Medium | Fair | ❌ No transactions; license churn (2.x→3.x) burned many teams |
| ClickHouse | **Excellent** | Weak (no real transactions) | Medium | Good | ✅ **Deferred** — add as analytical tier at 1 TB+ |
| MongoDB | Poor | Weak | Low | Good | ❌ Wrong shape for both patterns |
| Vanilla Postgres | Fair | Excellent | Low | Excellent | ⚠️ Fallback if Timescale licensing concerns |
| QuestDB | Excellent | Weak | Medium | Small | ❌ Small community; bus factor |
| kdb+ | Best-in-class | Fair | High | Tiny + expensive | ❌ Cost and talent scarcity |
| DuckDB + Parquet | Excellent (analytical) | None | Very low | Growing | ✅ **Selected for research tier** |

**Decision.** A **two-tier design.**
- **Hot / operational**: PostgreSQL 16 + TimescaleDB. Orders, positions, fills, event store, recent candles (~90 days). ACID where it matters.
- **Cold / research**: Parquet on object storage (S3/R2), queried by DuckDB or Polars. Full candle and tick history. Cheap, columnar, fast for backtests, trivially versionable.
- **Deferred**: ClickHouse when tick data exceeds ~1 TB and DuckDB scans get slow.

**Trade-off.** One system (Postgres) for both patterns is operationally simpler and initially slower on analytical scans than a dedicated columnar store. Simplicity wins at this stage; the seam (`MarketDataRepository` port) makes the ClickHouse migration a swap, not a rewrite.

**Consequences.** *Easier:* one database to operate; transactional guarantees for money; mature tooling. *Harder:* very large analytical scans on the hot tier; requires disciplined archival to cold. *Revisit at:* 1 TB hot data, or if continuous-aggregate refresh becomes a bottleneck.

## 6.3 ADR-003 — Message Bus

| Option | Latency | Durability | Ops | Verdict |
|---|---|---|---|---|
| **In-process asyncio queues** | ~µs | None | None | ✅ **Phase 1** — modular monolith |
| **NATS + JetStream** | ~100 µs | Yes | **Low** — single binary | ✅ **Phase 2** — when processes split |
| Redis Streams | ~ms | Yes (AOF) | Low (already have Redis) | ✅ Acceptable alternative |
| Kafka | ~ms | Excellent | **High** | ❌ Not until sustained >100k msg/s |
| RabbitMQ | ~ms | Yes | Medium | ❌ Wrong shape for high-rate fanout |

**Decision.** Start with **in-process async queues behind an `EventBus` port.** Move to **NATS + JetStream** when processes split. Kafka is a Year-3 conversation, if ever.

**The important part is the port.** Code against the interface from day one, and the transport becomes a configuration change rather than a refactor. Do not adopt Kafka now for a scale not yet reached — its operational cost is real, immediate, and paid daily; its benefit is hypothetical and deferred. That asymmetry is the entire argument.

## 6.4 ADR-004 — ORM

| Option | Verdict | Reasoning |
|---|---|---|
| **SQLAlchemy 2.0 (Core, selectively ORM)** | ✅ Selected | Best-in-class; Core gives raw-SQL control where performance demands it |
| SQLModel | ❌ | Thin wrapper; conflates persistence with API schemas — a boundary that should be kept clean |
| Django ORM | ❌ | Framework lock-in |
| Tortoise / Piccolo | ❌ | Smaller ecosystem |
| Raw SQL only | ⚠️ | Correct for hot-path time-series inserts; too costly elsewhere |

**Decision.** SQLAlchemy 2.0 Core for repositories; **raw SQL / `COPY` for bulk time-series ingestion**; Alembic for migrations. **The domain layer never imports SQLAlchemy.** Repositories map between domain objects and rows at the boundary. This is more work up front and pays for itself the first time a store is swapped or the risk engine is unit-tested without a database.

## 6.5 Full Stack Summary

| Concern | Selection | Rejected | Why |
|---|---|---|---|
| Language | Python 3.12+ | Rust, Go, C++ | Research velocity; latency-tolerant workload (ADR-001) |
| Async | asyncio + uvloop | Trio, threads | Ecosystem gravity |
| Typing | mypy --strict, Pydantic v2 at boundaries | — | Catch errors before they cost money |
| Numerics | NumPy, Polars | Pandas (research only) | Polars is faster, lazy, stricter; Pandas' index semantics cause silent bugs |
| Money | `decimal.Decimal` | float | **Non-negotiable** |
| DB | Postgres 16 + TimescaleDB | Influx, Mongo, kdb+ | ADR-002 |
| Analytics | DuckDB + Parquet | — | Zero-ops columnar |
| Cache | Redis 7 | Memcached | Data structures, streams, pubsub, persistence |
| Bus | asyncio → NATS/JetStream | Kafka, Rabbit | ADR-003 |
| ORM | SQLAlchemy 2.0 Core | SQLModel, Django | ADR-004 |
| Migrations | Alembic | — | Standard |
| Backtest | **Custom, in-house** | Backtrader, VectorBT, Zipline, Nautilus | See below |
| Testing | pytest, Hypothesis, testcontainers | unittest | Property-based testing is essential here |
| Lint/Format | Ruff | black+flake8+isort | One tool, fast |
| Arch enforcement | import-linter | — | Rules not enforced by CI decay |
| Logging | structlog → JSON | stdlib logging | Structured from day one |
| Metrics | Prometheus + Grafana | Datadog | Cost; self-hosted is fine at this scale |
| Tracing | OpenTelemetry | — | Vendor-neutral |
| Alerting | Alertmanager → PagerDuty | — | P1 must reach a phone |
| Secrets | Vault (or cloud KMS) | .env files | §3.6 |
| Containers | Docker, distroless | — | — |
| Orchestration | **docker-compose → ECS/Nomad** | **Kubernetes** | K8s is a large tax on a 6-process system. Revisit at 20+ services. |
| IaC | Terraform | Manual | — |
| CI/CD | GitHub Actions | — | — |
| Cloud | **AWS, region-matched to venue** | GCP, Azure | Latency to venue >> cloud features |
| Dashboard | React + TypeScript + TanStack Query + Lightweight Charts | Streamlit, Dash | Streamlit is excellent for research notebooks, unacceptable for an ops console |
| API | FastAPI | Flask, Django | Async-native, OpenAPI, Pydantic |

### On building the backtester in-house

This is the one place to recommend building rather than buying, and it is worth justifying, since "build your own framework" is normally the wrong instinct.

| Framework | Fatal Flaw |
|---|---|
| Backtrader | Unmaintained. No live crypto path worth using. |
| VectorBT | Vectorized. Fast, and **structurally prone to lookahead bias**. Cannot share code with a live event-driven engine. |
| Zipline | Equities-shaped (daily bars, US market calendar). Effectively abandoned. |
| Nautilus Trader | Genuinely good. Rust core, event-driven, live-capable. **Real candidate.** Steep learning curve, opinionated, and adopting it means adopting its entire worldview — which becomes a constraint precisely when something it didn't anticipate needs doing. |
| Custom | Costs ~4–6 weeks. |

**Decision: build.** The reasoning is not "it can be done better." It is that **backtest/live symmetry is the core architectural property of this platform** (§4.7), and it cannot be bolted onto a framework that doesn't have it. A subtle lookahead bug inside a third-party vectorized backtester costs far more than six weeks — it costs the ability to distinguish a real edge from a fitted one, without ever knowing it happened.

**However** — Nautilus's source is worth reading carefully first. It is the best public reference for this problem, and its ideas are worth stealing even without taking its code.

---

# 7. Database Design

*Conceptual model only. No DDL, per instruction.*

## 7.1 Entity Relationships (textual)

- **Venue** lists many **Instrument**s.
- **Instrument** has many **Candle**, **TradeTick**, is traded via many **Order**, held in many **Position**, and is a member of many **UniverseSnapshot** rows (point-in-time).
- **Strategy** is deployed as one or more **StrategyInstance**.
- **StrategyInstance** generates many **Signal**, owns many **Position**, and is constrained by a **RiskBudget**.
- **Signal** becomes (at most) one **OrderIntent**.
- **OrderIntent** is evaluated by one **RiskDecision**.
- **RiskDecision**, if approved, creates one **Order**.
- **Order** is executed via many **Fill**, has many **OrderEvent** (state history), and may be parent to child **Order**s (execution algos).
- **Fill** mutates **Position** and produces many **LedgerEntry** rows (double-entry).
- **Account** holds many **Position**, records many **LedgerEntry**, and is valued by many **EquitySnapshot**.
- **BacktestRun** produces many **BacktestTrade**, summarizes into one **BacktestMetrics**, tests one **Strategy**, and is pinned to one **DatasetVersion**.
- **EventLog** entries hash-chain to the prior entry.

## 7.2 Core Entities

| Entity | Key Attributes | Notes |
|---|---|---|
| **Venue** | id, name, type (cex/dex/broker), capabilities, fee schedule | — |
| **Instrument** | id, venue_id, symbol, **asset_class discriminator**, base/quote, tick_size, lot_size, min_notional, status | Sealed variant per asset class (§3.8.1) |
| **UniverseSnapshot** | date, venue_id, instrument_id, is_tradeable | **Point-in-time. Anti-survivorship. Capture from day one.** |
| **Candle** | instrument_id, open_time, o/h/l/c/v, trade_count, is_closed, source | Hypertable. `open_time` is the **start** of the interval; store `is_closed` explicitly — a partially-formed candle is the #1 lookahead vector in live trading. |
| **TradeTick** | instrument_id, ts, price, qty, side, venue_trade_id | Hypertable. Highest volume. |
| **Strategy** | id, name, version, code_hash, params_schema | `code_hash` makes results reproducible |
| **StrategyInstance** | id, strategy_id, params, status, allocated_capital, capacity_usd | The deployed thing |
| **Signal** | id, strategy_instance_id, ts, instrument_id, direction, strength, metadata | Immutable |
| **OrderIntent** | id, signal_id, side, target_qty, order_type, limit_price | Pre-risk |
| **RiskDecision** | id, intent_id, ts, approved, rules_evaluated, rejection_reason | **Rejections persisted.** A rising rejection rate is a leading indicator of a broken strategy. |
| **Order** | id, **client_order_id (UNIQUE)**, venue_order_id, instrument_id, strategy_instance_id, side, qty, filled_qty, avg_fill_price, status, tif, parent_order_id, created_at | `client_order_id` unique constraint = idempotency, enforced by the database |
| **OrderEvent** | id, order_id, seq, event_type, payload, ts | Append-only. The order aggregate is rebuildable from these. |
| **Fill** | id, order_id, **venue_fill_id (UNIQUE per venue)**, qty, price, fee, fee_currency, ts, is_maker | Unique constraint = exactly-once, enforced by the database |
| **Position** | id, account_id, instrument_id, strategy_instance_id, qty, avg_entry, realized_pnl, updated_at | Derived from fills; materialized for speed; **verified against the derivation** |
| **LedgerEntry** | id, account_id, ts, debit_acct, credit_acct, amount, currency, ref_fill_id | **Double-entry.** Debits equal credits. Always. |
| **EquitySnapshot** | account_id, ts, cash, positions_value, total_equity, drawdown | Hypertable, 1-minute granularity |
| **EventLog** | seq, ts, event_type, aggregate_id, payload, **prev_hash, hash** | Hash-chained, append-only, tamper-evident |
| **BacktestRun** | id, strategy_id, code_hash, params, dataset_version, seed, started_at, git_sha | **Every run. Including the failures.** See §12.6. |
| **DatasetVersion** | id, content_hash, symbol_set, date_range, created_at | Pinning this makes backtests reproducible |

### On double-entry bookkeeping

It may be tempting to skip it — just track a cash balance and add/subtract. Don't. Double-entry gives a **continuously checkable invariant** (debits = credits) that catches an entire class of accounting bug the moment it occurs rather than three weeks later when reconciling against the exchange reveals $4,000 missing. It costs one extra table. Accountants converged on this design over roughly six centuries; the burden of proof is on deviation.

## 7.3 Indexing

| Table | Index | Serves |
|---|---|---|
| candle | `(instrument_id, open_time DESC)` — Timescale primary dimension | Range scans (the dominant query) |
| candle | BRIN on `open_time` | Cheap, effective on append-only time data |
| trade_tick | `(instrument_id, ts DESC)` | Same |
| order | UNIQUE `(venue_id, client_order_id)` | **Idempotency — the single most important index** |
| order | `(status)` WHERE status IN (open states) — *partial* | "Fetch open orders" — hot path, tiny index |
| order | `(strategy_instance_id, created_at DESC)` | Blotter |
| fill | UNIQUE `(venue_id, venue_fill_id)` | **Exactly-once — the second most important index** |
| fill | `(order_id)` | Aggregation |
| position | UNIQUE `(account_id, instrument_id, strategy_instance_id)` | Lookup |
| event_log | `(aggregate_id, seq)` | Replay |
| event_log | BRIN on `ts` | Time-range audit |
| ledger_entry | `(account_id, ts)` | Statements |
| universe_snapshot | `(date, venue_id)` | Point-in-time universe |

The two UNIQUE constraints above are not performance optimizations. They are **correctness guarantees enforced by the one component that cannot be bypassed by a bug in application code.** If the dedup logic has a race, the database rejects the insert. This is the cheapest, most reliable safety net in the system, and it costs nothing.

## 7.4 Growth Projection

| Data | Rate | 1 Year | 5 Years |
|---|---|---|---|
| 1m candles, 20 symbols | 10.5 M rows/yr | ~1 GB | ~5 GB |
| 1m candles, 500 symbols | 263 M rows/yr | ~25 GB | ~125 GB |
| Trade ticks, 20 symbols | ~3 B rows/yr | ~200 GB | ~1 TB |
| L2 book (10 lvl, 100 ms), 20 symbols | ~63 B rows/yr | ~5 TB | ~25 TB |
| Orders + fills | ~1 M/yr | ~1 GB | ~5 GB |
| Event log | ~50 M/yr | ~20 GB | ~100 GB |

**Candles are free. Ticks are manageable. L2 is where storage costs explode.** Decide *deliberately* whether L2 history is needed — it is only worth its cost for microstructure or execution-cost strategies. Most mid-frequency strategies never touch it. If collected, it goes straight to Parquet on object storage, never to Postgres.

## 7.5 Partitioning & Retention

**Partitioning.** Timescale hypertables, 1-day chunks for ticks, 7-day for candles. Space-partition by `instrument_id` at 100+ symbols. Compress chunks older than 7 days (Timescale reports 10–20× on this shape of data).

**Retention.**

| Data | Hot (Postgres) | Warm (Parquet, S3) | Cold (Glacier) | Delete |
|---|---|---|---|---|
| 1m candles | 90 d | Forever | — | Never |
| Ticks | 7 d | 2 y | Forever | Never |
| L2 book | 24 h | 90 d | 1 y | After 1 y |
| Orders/Fills/Ledger | Forever | — | — | **Never** |
| Event log | 1 y | Forever | — | **Never** |
| Metrics | 15 d | 13 mo (downsampled) | — | — |

**Financial records are never deleted.** Not for GDPR (legitimate-interest / legal-obligation basis applies), not for cost, not for tidiness. If external capital is ever taken, or an audit ever occurs, or the question "what happened on 2024-08-05 at 09:14 UTC" ever needs answering, the event log is the only thing that can answer it. Storage is cheaper than the question being unanswerable.

---

# 8. Trading Engine

## 8.1 Signal Flow

Market Event → update incremental indicators → `strategy.on_data` → Signal? → if yes: validate (known instrument, strength bounded, not stale) → persist `SignalGenerated` → forward to Portfolio Manager.

A `Signal` is an **opinion**: instrument, direction, strength ∈ [0,1], optional target price and stop, plus metadata explaining why. It is *not* an order. It has no size. Sizing is a portfolio-level concern, because the correct size depends on the *rest of the portfolio* — which the strategy, by design, cannot see. Strategies that size their own positions cannot be composed, cannot be risk-budgeted, and cannot be allocated across. This separation is the foundation of every multi-strategy system.

## 8.2 Order Flow

Signal → Portfolio Manager reads current position → computes target position → applies sizing model (vol-target / fixed-fractional) → delta = target − current → is `|delta|` > min notional AND > rebalance threshold?
- No → drop (avoids fee-churning noise).
- Yes → build `OrderIntent` → **Risk Engine (synchronous)** →
  - Reject → persist + metric + alert if rate is high.
  - Approve → deterministic `client_order_id` → round to tick/lot, re-validate min notional → persist `OrderCreated` → publish `OrderCommand` → Venue Gateway → rate-limit budget check → sign + send →
    - Ack → `ACKED`.
    - Reject → classify.
    - Timeout → `UNKNOWN` — query, never blind-retry.

Two easily-missed details. **The rebalance threshold** prevents a strategy whose target drifts by 0.3% every bar from generating a trade every bar and paying away its entire edge in fees. And **rounding happens after risk, then re-validates** — because rounding a 0.0009 BTC order down to 0.000 produces a zero-quantity order, and rounding *up* may breach the very limit risk just approved.

## 8.3 Portfolio Flow

Fill arrives → dedupe on `venue_fill_id` → apply to position (`Decimal`) → recompute avg entry / realized P&L → write double-entry ledger entries (position, cash, fee) → emit `PositionChanged` → mark-to-market → update equity curve → **feed drawdown back into the risk engine.**

That last arrow is the closed loop that makes drawdown limits real rather than decorative.

## 8.4 Risk Flow

See §10. The one-line version: **synchronous, in the critical path, fail-closed, and it can veto anything.**

## 8.5 Execution Flow & Failure Recovery

| Failure | Detection | Response |
|---|---|---|
| WS disconnect (market data) | Heartbeat timeout | Reconnect w/ backoff+jitter; **REST backfill the gap**; resume |
| WS disconnect (user stream) | Heartbeat timeout | Reconnect; **full reconcile before resuming trading** |
| Order submit timeout | HTTP timeout | → `UNKNOWN`. Query by `client_order_id`. Bounded retries. Then escalate to a human. **Never blind-retry.** |
| Venue 5xx | HTTP status | Retryable. Backoff. Circuit-break after N. |
| Venue 429 | HTTP status | Halt submissions; drain budget; resume on reset |
| Venue IP ban | 418 / auth fail | **Halt all trading. Page. Manual intervention.** |
| Fill for unknown order | OMS lookup miss | Log, alert, **adopt** — never discard. This is real money. |
| Position drift | Reconciler | **Halt. Page. Investigate.** Never silently correct. |
| Strategy exception | Handler | Pause **that strategy**; others continue; alert |
| Risk engine unavailable | Health check | **Reject all orders.** Fail closed. |
| DB unavailable | Health check | Halt new orders; keep processing fills in memory; alert; **do not lose fills** |
| Process crash | Supervisor | Restart → replay event log → reconcile → resume only if clean |
| Clock skew >50 ms | NTP monitor | Alert. Halt if >1 s — signed requests will fail anyway. |

### Reconnect Sequence — Non-negotiable Order

1. **HALT** new order submission.
2. Reconnect (backoff + jitter).
3. `GET` open orders.
4. `GET` positions.
5. `GET` balances.
6. `GET` trades since `last_known_fill_id`.
7. Reconcile: adopt orphans, apply missed fills, recompute state.
8. Backfill market data gap via REST.
9. If drift detected → **HALT. PAGE HUMAN.** Otherwise → resume trading.

Steps 3–7 are the ones that get skipped by systems built in a hurry, and they are precisely the ones that prevent resuming trading with a position that isn't known about. **Trading is halted first and resumed last.** The instinct to "get back online quickly" is exactly wrong: the market will still be there in ninety seconds, and a resumed system operating on stale state can do unbounded damage in that time.

## 8.6 Exchange Abstraction

The OMS depends only on a `Venue` port (interface): `submit`, `cancel`, `cancel_all`, `get_open_orders`, `get_positions`, `get_balances`, `get_fills_since`, `subscribe_user_stream`, `capabilities`. Adapters — Binance, Bybit, Coinbase, IBKR (future), Simulated (backtest), Paper (live data, fake fills) — all implement this same port. The domain layer knows nothing about any specific venue.

**The hard problem is capability variance, not protocol variance.** Binance supports `postOnly`; some venues don't. Some support amend-in-place; most don't. Some return fills on the order response; others only via WS.

The wrong answer is a lowest-common-denominator interface that supports only what every venue supports — access is lost to features being paid for. Also wrong is a fat interface where adapters raise `NotImplementedError` — now every call site needs a `try/except` and the type system communicates nothing.

**The right answer:** every adapter declares a `capabilities` set. The OMS **queries capabilities before constructing an order** and degrades explicitly and visibly (`post_only` requested but unsupported → either reject the intent with a clear reason, or emulate it, per a *documented, configured* policy). Capability mismatches become a startup-time validation error, not a 3 a.m. runtime surprise.

**Simulated and Paper adapters implement the same port.** This is what makes §4.7 real. The paper adapter is especially valuable: real market data, real latency, simulated fills. It is the last gate before real money, and it catches the class of bug that only appears when data arrives at unpredictable times.

---

# 9. Strategy Engine

## 9.1 Plugin Architecture

A strategy is a class implementing the `Strategy` port. It is loaded by a registry, instantiated with validated parameters, and fed only the data it declared it wants.

**The port surface — deliberately minimal:**

| Element | Direction | Purpose |
|---|---|---|
| `subscriptions()` | out | Declare instruments + timeframes. Enables data routing and warmup calculation. |
| `warmup_period()` | out | Bars needed before signals are valid |
| `params_schema()` | out | Typed, validated, with bounds. Bounds are also the optimizer's search space. |
| `on_start(context)` | in | Initialize state. Given a `Context`, not a global. |
| `on_data(event, view)` | in | **The only place signals are produced.** `view` is a past-only `MarketDataView`. |
| `on_fill(fill)` | in | Optional. React to own execution. |
| `on_stop()` | in | Persist state, clean up. |
| `state()` / `restore(state)` | both | Restart safety |

**What the port deliberately does *not* expose:**
- No wall clock. Time comes from `context.now()`, which the backtester controls.
- No network. No filesystem. No database.
- No order submission. Strategies emit `Signal`s. They cannot trade.
- No position sizing. That is the portfolio manager's job (§8.1).
- No access to other strategies.

Each exclusion is a bug class eliminated by construction. A strategy that *cannot* call `datetime.now()` cannot be nondeterministic. A strategy that *cannot* submit an order cannot bypass risk. A strategy that *cannot* see the future cannot leak lookahead. These are not restrictions on what can be expressed — they are restrictions on how it is expressed, and they are what make the results trustworthy.

## 9.2 Lifecycle

States: `Registered → Validated → Initialized → WarmingUp → Ready → Running`. From `Running`: → `Paused` (operator, risk breach, or exception threshold) → back to `Running`; or → `Draining` (stop requested) → `Stopped`; or → `Faulted` (unhandled exception) → `Paused` (auto) or `Stopped` (operator kills).

- **WarmingUp**: the strategy sees data but signals are DISCARDED. Indicators need history — a signal from a half-warm EMA is noise.
- **Draining**: never stop a strategy with open positions and no plan. Either flatten, or explicitly hand the position to a manual/liquidation-only owner.

The `Draining` state is routinely omitted from designs and routinely needed in production. "Stop strategy X" is ambiguous: does its position vanish? Get flattened at market? Get orphaned? Make the answer explicit and configurable per strategy, because the right answer differs between a mean-reversion strategy (flatten now) and a long-horizon trend follower (hold, stop generating new signals).

## 9.3 Isolation & Testing

**Isolation.** Phase 1: `try/except` around each `on_data`, with per-strategy exception counters and an auto-pause threshold. Phase 3: separate process per strategy, communicating over the bus. Cost: serialization + IPC latency, ~1 ms. Benefit: a segfault in a C extension, an OOM, or an infinite loop in strategy A cannot touch strategy B. At real capital across multiple strategies, that benefit dominates.

**Testing.** Because strategies are pure functions of `(event, view, state)`, they are trivially testable: feed a synthetic bar sequence, assert on emitted signals. No mocks, no database, no exchange. This is the payoff for the restrictive port. A strategy test suite should run in milliseconds and cover: warmup correctness, signal-at-boundary conditions, state round-trip through `state()`/`restore()`, and behavior on gapped/missing data.

**Versioning.** A `code_hash` on every strategy. A backtest result is meaningless without knowing exactly which code produced it. Pin it, store it, and refuse to run a strategy in production whose hash does not match a hash that has passed validation.

---

# 10. Risk Engine

> Every other module in this document exists to make money. This one exists to ensure that a bad day is survivable. It is the only module where added latency, added complexity, and false positives are acceptable as the price of being wrong in the safe direction.

## 10.1 Principles

1. **Pre-trade, synchronous, in the critical path.** Post-trade risk is a report about a loss already taken.
2. **Fail closed.** Unavailable, uncertain, timed out, or erroring → **reject**.
3. **Layered.** Order-level → position-level → strategy-level → portfolio-level → account-level. Every order passes every applicable layer.
4. **Limits are config-as-code**, versioned, reviewed, two-person-approved in production. Not a value in a database that anyone can `UPDATE`.
5. **Risk can always veto.** No strategy, no operator, no override path bypasses it. A bypass built "for emergencies" will be used routinely within a month.
6. **Independently verifiable.** The risk engine's view of exposure is computed from the *reconciled* venue state, not solely from internal beliefs. A bug in the portfolio manager must not blind the risk engine.

Point 6 is subtle and important. If the risk engine trusts the portfolio manager's position, then a position-tracking bug causes the risk engine to happily approve unlimited additional exposure. Independent derivation from reconciled state means both would have to be wrong *in the same way* to fail.

## 10.2 Check Chain

Ordered cheapest-and-most-likely-to-reject first. Short-circuit on first rejection:

1. Kill switch engaged? → reject.
2. System halted (daily loss / drawdown / drift / venue down)? → reject.
3. Strategy active and within its budget? → reject if not.
4. Fat-finger checks (notional ceiling, qty sanity, price collar vs. last) → reject if failed.
5. Order rate within limit? → reject if not.
6. Instrument limits (max position, tradeable, not halted) → reject if failed.
7. Post-trade exposure (gross, net, leverage) within bounds? → reject if not.
8. Correlation cluster concentration within bounds? → reject if not.
9. Liquidity (size vs. ADV, size vs. book depth) acceptable? → reject if not.
10. Margin sufficient post-trade? → reject if not.
11. Within strategy `capacity_usd`? → reject if not.
12. **APPROVE.**

Every step persists a `RiskDecision`, approved or rejected, with the reason.

**Both approvals and rejections are persisted.** A sudden spike in rejections means a strategy has become misconfigured, or the market has moved into a regime the strategy doesn't understand, or a limit is mis-set. It is a leading indicator, available *before* the loss, and it is free.

## 10.3 Position Sizing

| Method | Formula (conceptual) | Pros | Cons | Recommendation |
|---|---|---|---|---|
| Fixed notional | constant $ | Trivial | Ignores volatility entirely | Phase 1 only |
| Fixed fractional | % of equity | Simple, compounds | Ignores volatility | Phase 1 |
| **Volatility targeting** | size ∝ target_vol / realized_vol | **Normalizes risk across instruments and regimes** | Needs a good vol estimate; whipsaws on vol spikes | ✅ **Default** |
| ATR-based | risk_per_trade / (ATR × multiplier) | Intuitive stop placement | Same as above | ✅ Good for stop-based strategies |
| Kelly | edge / odds | Theoretically growth-optimal | **Catastrophic under parameter uncertainty.** Requires knowing the true edge, which is never actually known. | ❌ Never full Kelly |
| Fractional Kelly (¼–½) | Kelly × f | Growth with margin for error | Still needs edge estimate | ⚠️ Advanced only, with a hard cap |
| Risk parity | inverse-vol weighted | Balanced portfolio contribution | Needs a stable covariance estimate | ✅ Phase 3, multi-strategy |

**Default: volatility targeting with a hard notional cap.** The cap matters — vol targeting will size *enormously* into a low-volatility instrument, and low realized volatility immediately preceding a regime break is the classic setup for maximum position at exactly the wrong moment. The cap is what prevents the model from being clever right up until it causes serious damage.

**On Kelly:** full Kelly is optimal only if the edge is known exactly. It never is; there is only an estimate from a backtest with a finite sample, and that estimate is biased upward by selection. Betting full Kelly on an overestimated edge produces *negative* expected log growth. Half-Kelly on a correct edge gives ~75% of the growth with dramatically lower variance. Quarter-Kelly is what practitioners actually use. This is one of the few places where the theory is unambiguous and the practice still routinely ignores it.

## 10.4 Limits

| Layer | Limit | Typical | Action on Breach |
|---|---|---|---|
| Order | Max notional | $50k | Reject |
| Order | Price collar | ±5% of last | Reject |
| Order | Rate | 10/min/strategy | Throttle |
| Position | Max per instrument | 10% NAV | Reject increase |
| Position | Max leverage | 2× | Reject increase |
| Strategy | Capital allocation | 20% NAV | Reject |
| Strategy | Daily loss | 2% of allocation | **Pause strategy** |
| Strategy | Max drawdown | 10% of allocation | **Stop strategy, require review** |
| Portfolio | Gross exposure | 200% NAV | Reject increase |
| Portfolio | Net exposure | ±100% NAV | Reject increase |
| Portfolio | Cluster concentration | 30% NAV per correlation cluster | Reject increase |
| Account | **Daily loss** | **3% NAV** | **HALT ALL TRADING** |
| Account | **Max drawdown from peak** | **15% NAV** | **HALT + human review to resume** |
| Account | Margin utilization | 50% | Reject; alert at 40% |

Note the escalation: strategy-level breaches pause a strategy; account-level breaches halt everything and **require a human to resume.** Auto-resume after a drawdown halt is a trap — the condition that caused a 15% drawdown is very often still present, and an auto-resuming system will re-enter it.

## 10.5 Emergency Stop

Three tiers, and they are genuinely different mechanisms, not three buttons on the same page.

| Tier | Trigger | Action | Latency |
|---|---|---|---|
| **Soft halt** | Daily loss limit | Stop new orders. Existing orders live. Positions held. | Immediate |
| **Hard halt** | Drawdown limit, position drift, risk engine down | Stop new orders. **Cancel all open orders.** Positions held. | <5 s |
| **KILL** | Manual, or catastrophic condition | Cancel all. **Flatten all positions at market.** Revoke API keys. | **<30 s** |

**The kill switch is a separate process** (§4.3), with its own credentials, its own network path, and near-zero dependencies. Requirements:

- Triggerable from the dashboard, from a CLI, **and from a phone**, each with independent auth.
- Works when the trading core is hung, OOM-killed, or in an infinite loop.
- **Idempotent.** Pressing it twice is safe. Pressing it fifty times is safe.
- **Drilled monthly**, in production, with real (small) positions. An undrilled kill switch is a kill switch that does not work — this simply hasn't been discovered yet.
- Deliberately does *not* verify that flattening is a good idea. Flattening into a flash crash may realize a loss that would otherwise recover. That is an acceptable, chosen cost. The alternative — a kill switch with judgment — is a kill switch that can decline to fire.

Flattening at market during a liquidity crisis produces a bad price. This is fine. The kill switch exists for the scenario where the system's actual behavior is unknown, and in that scenario, a known bad price is strictly better than an unknown unbounded exposure.

## 10.6 Future

Real-time VaR/CVaR; stress scenarios (2020-03-12, 2022-05 LUNA, 2022-11 FTX, 2010 flash crash) run *nightly against the current book*, not just historically; factor exposure limits; a pre-mortem process for every new strategy ("assume this loses 20% next month — what happened?"); an independent risk process that can halt the trader; scenario-based margin.

---

# 11. Data Engine

## 11.1 Historical Data

**Sources.** Venue REST (free, authoritative for that venue, rate-limited, often shallow history), commercial vendors (Kaiko, CryptoCompare, Tardis — expensive, deep, includes delisted symbols), and free aggregators (unreliable; fine for prototyping, never for a decision).

**The trap: an exchange's REST API only returns data for *currently listed* symbols.** The price history of a coin that has been delisted cannot be retroactively obtained, because the endpoint returns 404 for a symbol that no longer exists. This means **survivorship bias is built into the free path, and it is not fixable later.** Every backtest run on such data is systematically optimistic, and the bias is largest precisely for strategies that buy weakness — which is most of them.

Two responses, both necessary: **(a) start capturing daily universe snapshots immediately**, from day one, before there's any use for them, because the data has an expiry date; **(b) budget for a vendor with delisted-symbol coverage before allocating serious capital.** This line item is not optional and it is not a Phase 5 concern.

**Backfill.** Chunked, rate-limit-aware, resumable, idempotent (upsert on `(instrument_id, open_time)`), with progress checkpointing and verification (row counts, gap scan) after each chunk.

## 11.2 Realtime Data

Persistent WS. Heartbeat monitoring **at the message level, not the socket level** — an open TCP connection carrying no data is the failure mode actually encountered in practice. Reconnect with exponential backoff and jitter (thundering herd on venue restart is real). Sequence-number tracking where the venue provides it. **REST backfill of the gap on every reconnect, unconditionally.**

Always record **both** `exchange_ts` and `local_recv_ts`. The difference is a free, continuous measurement of latency to the venue and of the venue's own health. When they diverge, something is wrong upstream, and it's better to know before the strategy acts on a price from thirty seconds ago.

## 11.3 Caching

| Layer | Store | TTL | Contents |
|---|---|---|---|
| L1 | In-process ring buffers | — | Last N bars per instrument (indicator inputs). Nanoseconds. |
| L2 | Redis | seconds–minutes | Latest tick, current book, positions read-model |
| L3 | Postgres/Timescale | — | Recent history |
| L4 | Parquet / object store | — | Full history |

**Cache invalidation rule for a trading system:** never serve a stale price to a decision. Every cached market value carries a timestamp, and consumers check staleness *before use*, rejecting anything older than a per-instrument threshold. A stale-price bug is not a performance problem; it is a trade at a price that does not exist.

## 11.4 Validation

Run at ingestion, and again as a nightly batch:

| Check | Detects |
|---|---|
| `high ≥ max(open, close)`, `low ≤ min(open, close)`, `high ≥ low` | Corrupt OHLC |
| Timestamps strictly monotonic, aligned to interval boundary | Duplicate / misaligned bars |
| No missing intervals in a trading window | **Gaps** |
| Volume ≥ 0; zero-volume streaks flagged | Halts, illiquidity, feed death |
| Price move > N σ (rolling) | Spikes, bad prints, real crashes — *flag, don't auto-drop* |
| Cross-source price agreement within tolerance | Single-source corruption |
| Bid ≤ ask | Crossed book |

**Flag; never silently drop.** A 20σ move might be a bad print, or it might be a real crash. Auto-dropping outliers deletes exactly the observations a risk model most needs to see, and it makes the backtest fantastically optimistic about tail behavior. Quarantine for review, don't erase.

## 11.5 Storage, Sync, Recovery

Hot Postgres/Timescale (~90 d), cold Parquet on object storage, partitioned `venue/instrument/year/month`. Datasets are **content-hashed and versioned**, so a backtest can pin `dataset_version` and be exactly reproducible a year later. **Never mutate historical data in place** — corrections create a new version, and a backtest run records which version it used. Otherwise a saved backtest may become unable to reproduce, with no way to explain why.

Nightly: gap scan → auto-backfill → re-validate → alert on anything unresolved. Recovery is a documented, rehearsed procedure, not an improvisation.

---

# 12. Backtesting

> A backtest is not a prediction. It is a *falsification tool*. Its only reliable output is "this idea is definitely bad." A backtest that looks good tells far less than it seems to, and the gap between what it tells you and what you believe it tells you is where most trading capital goes to die.

## 12.1 Architecture

**Event-driven, not vectorized.** Vectorized backtests are 100× faster and structurally invite lookahead bias — the entire price series is in memory, and one careless `.shift(-1)` or a rolling window that includes the current bar silently makes the strategy clairvoyant. More importantly, a vectorized backtest **cannot share code with a live event-driven engine**, which forfeits the platform's central design property (§4.7).

Use vectorized computation for *screening* — a fast, approximate first pass over a large parameter space to find regions worth examining. Then confirm every survivor with the event-driven engine. Never allocate capital on a vectorized result.

## 12.2 Simulation Flow

1. Load dataset version + pin hash.
2. Init `SimulatedClock` at `t0`.
3. Init `SimulatedVenue` (fees, latency, slippage models).
4. Warm up strategy — discard signals.
5. Loop over events: advance clock to `event.ts` → update `MarketDataView` cursor (**future is now unreachable**) → process pending venue events (fills from the *prior* bar's orders) → `strategy.on_data` → if signal: size it (Portfolio) → risk-check (same rules as live) → if approved: submit to `SimulatedVenue` → queue fill for a **future timestamp**, after simulated latency.
6. At end of events: compute metrics + tearsheet.

Two steps are where backtests lie.

**The `MarketDataView` cursor** — it advances with the clock and cannot be indexed past it. Lookahead becomes impossible by construction rather than by care.

**Fill timestamp queuing** — an order submitted on the bar that closes at time `t` **cannot fill at the close price of bar `t`.** That price was not known when the decision was made. It fills at bar `t+1`, after simulated latency. This single detail is the difference between a strategy that looks like it makes 200% a year and one that loses money. Every naive backtester gets this wrong, and gets it wrong in the profitable direction, which is why nobody notices.

## 12.3 Fill Realism

| Component | Naive | Realistic |
|---|---|---|
| Fill price | Close of signal bar | **Next bar open + slippage**, or book-walk if L2 available |
| Slippage | Zero | f(spread, order size / bar volume, volatility) |
| Fees | Zero | Maker/taker by venue tier; **funding for perps** |
| Partial fills | Never | Size vs. available volume |
| Latency | Zero | Sampled from a measured live-latency distribution |
| Market impact | Zero | Square-root law: impact ∝ σ·√(Q/ADV) |
| Rejections | Never | Min-notional, precision, insufficient margin |
| Queue position | N/A | For limit orders: modeled or pessimistic |

**Be pessimistic on purpose.** If a strategy is profitable under pessimistic assumptions, something real has been found. If it is only profitable under optimistic ones, nothing has been found — but it feels like something has, which is worse than finding nothing, because it costs money to discover.

A useful discipline: for maker/limit strategies, assume the order is **last in the queue** unless queue position has been properly modeled. Most limit-order backtests assume a limit order fills whenever the price touches it. In reality the price touches, a hundred orders ahead fill, and the order in question does not — *except* when the price is about to move against it, in which case it does fill. This adverse-selection asymmetry is the entire reason naive market-making backtests look wonderful and lose money live.

## 12.4 Metrics

| Category | Metrics |
|---|---|
| Return | Total, CAGR, monthly/annual table |
| Risk | Volatility, max drawdown, drawdown duration, VaR, CVaR, ulcer index |
| Risk-adj | Sharpe, Sortino, Calmar, Omega, **Deflated Sharpe Ratio**, Probabilistic Sharpe |
| Trade | Count, win rate, profit factor, avg win/loss, expectancy, MAE/MFE |
| Exposure | Time in market, avg/max gross, turnover |
| Cost | Total fees, total slippage, **fees as % of gross P&L** |
| Robustness | Parameter sensitivity surface, OOS/IS ratio, **PBO**, regime-conditional returns |

**Sharpe alone is nearly useless for strategy selection**, because it does not account for the number of strategies tried. Testing 1,000 random strategies will produce a "best" one with a Sharpe near 3 and no edge whatsoever — this is arithmetic, not bad luck. The **Deflated Sharpe Ratio** (Bailey & López de Prado) adjusts for the number of trials, the skew, and the kurtosis of returns, and it is the number that should actually be examined. Which means the trial count must be **counted honestly** — hence §12.6.

**Fees as a percentage of gross P&L** is the metric that quietly kills strategies. A strategy with a 1.8 Sharpe and gross P&L that is 95% consumed by fees is not a strategy; it is a subsidy to the exchange. Look at it early, before falling in love with the result.

## 12.5 Walk-Forward & Monte Carlo

**Walk-forward** — the minimum standard. Optimize on an in-sample window, test on the subsequent out-of-sample window, roll forward, and **report only the concatenated OOS performance.**

```
Optimize [Jan–Jun] -> Test [Jul–Sep]
        Optimize [Apr–Sep] -> Test [Oct–Dec]
                Optimize [Jul–Dec] -> Test [Jan–Mar]
```

Anchored (expanding) vs. rolling windows both have a defensible case; rolling is more honest about regime change. **Report only OOS.** The moment OOS results are examined and the strategy is adjusted in response, that OOS data is now in-sample, and it has been burned — permanently. Discipline is required here or the entire exercise is theater. Reserve a final holdout period to be looked at **once**, ever, before going live.

**Monte Carlo**, three flavors, answering three different questions:
1. **Trade resampling** (bootstrap the trade sequence) → "Was the equity curve just luck of ordering?" Gives a distribution of max drawdown. The answer is usually *sobering*: the realized max drawdown is typically near the median of the distribution, not the tail — meaning the worst historical drawdown is roughly what should be expected *again*, not a worst case.
2. **Parameter perturbation** (jitter params ±10%) → "Is this on a cliff or a plateau?" A strategy whose Sharpe collapses when a lookback goes from 20 to 22 is fitted to noise. **The plateau test is the single best overfitting detector available and it costs almost nothing to run.**
3. **Path simulation** (synthetic price series with matched statistical properties) → "Does this work on data that shares the market's statistics but not its specific history?"

## 12.6 The Backtest Run Registry — The Defense Against Self-Deception

**Every backtest run is logged**: git SHA, strategy code hash, full parameters, dataset version hash, random seed, timestamp, all metrics, and the operator. Never deleted. Never pruned.

This is not for reproducibility, though it provides that. It is because **the number of hypotheses tested determines how impressed one is allowed to be by the best result.** A researcher who tries 500 configurations and reports the best will, with essentially certainty, report something that looks like alpha and isn't. Without a registry, this trial count is invisible, unrecorded, and — crucially — *unconsciously understated by the researcher, who genuinely does not remember the 400 runs mentally discarded along the way.*

The registry makes the trial count an observable fact, which makes the Deflated Sharpe Ratio computable, which makes strategy selection statistically defensible rather than an exercise in self-deception.

**This is the highest-leverage, lowest-cost intellectual honesty mechanism in the entire platform.** It is one table and one decorator. Build it in Phase 2, before there's anything to hide from.

---

# 13. Dashboard

**The dashboard is read-only, off the critical path, and structurally incapable of harming the trading system** — with exactly one exception, and that exception is safe to press.

## 13.1 Pages

| Page | Purpose | Key Widgets | Refresh |
|---|---|---|---|
| **Overview** | "Is everything OK?" — answerable in 3 seconds from across the room | Equity curve, today's P&L, drawdown gauge, open positions, system health, **KILL SWITCH** | 1 s |
| **Positions** | Detail | Position table (qty, entry, mark, unrealized, exposure %), per-strategy breakdown | 1 s |
| **Orders** | Live + history | Open orders, blotter, fills, rejection log w/ reasons | 1 s |
| **Strategies** | Control | Per-strategy status, allocation, P&L, signals/hr, pause/resume, risk utilization | 5 s |
| **Risk** | Mandate | Limit-utilization gauges, exposure heatmap, correlation matrix, VaR, breach history | 5 s |
| **Performance** | Analysis | Tearsheet, monthly returns table, rolling Sharpe, attribution, TCA | 1 min |
| **Research** | Backtests | Run browser, tearsheets, param-sensitivity surface, walk-forward, **DSR + trial count** | On demand |
| **Data** | Health | Feed status, staleness per symbol, gap report, backfill queue | 5 s |
| **System** | Ops | Process health, latency histograms, error rates, venue connectivity, reconciliation status | 5 s |
| **Audit** | Forensics | Event log search, order timeline reconstruction, "why did this order exist?" | On demand |

## 13.2 KPIs — Always Visible

Total equity · Today's P&L (abs + %) · Current drawdown from peak · Gross/net exposure · Open position count · Risk-limit utilization (max across all limits) · System health · **Time since last successful reconciliation** · Data staleness (worst symbol)

The last two are the ones that no retail dashboard shows and every professional one does. "Time since last successful reconciliation" is the single number that tells you whether anything else on the screen can be believed.

## 13.3 UX Principles

1. **The most important information is the most visually prominent.** Drawdown and reconciliation status are large. Individual trade P&L is small.
2. **Color is semantic, and colorblind-safe.** Red = danger or short. Green = healthy or long. Never both meanings on one screen. Never red/green as the *only* channel.
3. **Latency honesty.** Every number carries an "as of" timestamp. A stale dashboard that looks live is a hazard, not a feature.
4. **The kill switch is always visible, always reachable, and requires a confirmation gesture** — not because it's dangerous (it isn't), but because accidental triggering during a normal day is disruptive.
5. **No manual order entry in v1.** Adding a "buy" button converts a systematic platform into a discretionary one that a bad night will use. If manual intervention is ever needed, it is `reduce-only`, hedge-or-liquidate, permissioned, logged, and alerts everyone.
6. **Optimized for 3 a.m.** The person reading this is half-awake and something is wrong. Reduce cognitive load. The primary question — "is money leaving?" — must be answerable without clicking.

## 13.4 Navigation & Permissions

Flat, keyboard-driven (`g o` → Overview, `g p` → Positions). Command palette. No nesting beyond two levels.

| Role | Read | Pause/Resume | Kill | Change Limits | Deploy |
|---|---|---|---|---|---|
| Viewer | ✅ | ❌ | ❌ | ❌ | ❌ |
| Operator | ✅ | ✅ | ✅ | ❌ | ❌ |
| Risk Officer | ✅ | ✅ | ✅ | ✅ (2-person) | ❌ |
| Engineer | ✅ | ✅ | ✅ | ❌ | ✅ |
| Admin | ✅ | ✅ | ✅ | ✅ | ✅ |

**Everyone can kill.** Killing is safe. Nobody should ever hesitate to kill because they lack permission, and no on-call rotation should have a hole in it. Note that Engineers *cannot* change risk limits and Risk Officers *cannot* deploy — that separation is the whole point, and it costs nothing to build now even if one person holds all the roles today.

---

# 14. Development Roadmap

**Complexity** is measured in engineer-weeks for a team of 2. Estimates assume the reference architecture and no significant scope creep — an assumption that rarely holds in practice, so apply an appropriate multiplier.

---

### Phase 0 — Foundations · 3 weeks · Low complexity

**Objectives.** Repository, CI, domain model, event bus port, `Decimal` money types, secrets, observability skeleton, dev environment.

**Deliverables.** Monorepo w/ enforced layer boundaries (import-linter in CI); `Instrument`, `Order`, `Fill`, `Position`, `Money` domain types; `EventBus`, `Clock`, `Venue`, `MarketDataFeed` ports; CI with mypy strict + ruff + pytest; docker-compose (Postgres+Timescale, Redis, Grafana); ADR-000..004 committed.

**Risks.** Over-engineering the domain model before it's clear what it needs (**bias toward fewer types**); bikeshedding tooling.
**Validation.** `make test` green in <30 s. Domain tests run with **zero** external dependencies. Import-linter fails the build on a deliberate violation. A new engineer sets up in <1 hour.

---

### Phase 1 — Data Engine · 4 weeks · Medium

**Objectives.** Ingest, store, validate, serve market data. **Start universe snapshots immediately.**

**Deliverables.** One venue adapter (read-only); historical backfill (resumable, idempotent); Timescale hypertables; validation suite; gap detection + auto-backfill; realtime WS w/ reconnect + gap fill; reference data + **point-in-time universe snapshots**; Parquet archival.

**Risks.** ⚠️ **Survivorship bias becomes unfixable if universe snapshots start late.** Underestimating exchange API quirks (double the estimate). Rate-limit bans during backfill.
**Dependencies.** Phase 0.
**Validation.** Backfill 2 years × 20 symbols with **zero** gaps. WS runs 7 days, survives forced disconnects, gap-fills correctly every time. Validation catches synthetic corruption injected into the stream.

---

### Phase 2 — Backtest Engine · 6 weeks · High

**Objectives.** Trustworthy, reproducible, lookahead-free backtesting. **The most important phase in this document.**

**Deliverables.** Event-driven engine; `SimulatedClock`; **`MarketDataView` that structurally cannot see the future**; `SimulatedVenue` (fees, slippage, latency, partial fills, rejections); strategy port + registry; indicator library (vectorized + incremental, **tested for equivalence**); metrics + tearsheet; **backtest run registry**; determinism harness.

**Risks.** ⚠️ **Subtle lookahead bias** — a single off-by-one in the view cursor invalidates everything downstream and is nearly invisible. ⚠️ Fill model too optimistic. ⚠️ Vectorized and incremental indicators diverging silently (test them against each other on random data, every build).
**Dependencies.** Phase 1.
**Validation.** A strategy that *only* reads future data must produce **zero** profit — i.e., it must be unable to read it at all. A known strategy on known data reproduces bit-identically across runs and machines. Buy-and-hold backtest matches a hand-computed result to the cent. Fees and slippage are visibly non-zero in the output.

---

### Phase 3 — Strategy + Research Loop · 4 weeks · Medium

**Objectives.** Close the research loop. Find out whether there is an actual edge.

**Deliverables.** 2–3 reference strategies; parameter sweep; **walk-forward**; **Monte Carlo (all three flavors)**; **Deflated Sharpe + PBO**; research notebook SDK; tearsheet reports.

**Risks.** ⚠️ **Overfitting.** ⚠️ OOS results examined, strategy adjusted, re-run — burning the holdout without noticing. Discipline and the run registry are the only defenses; no technical enforcement is possible.
**Dependencies.** Phase 2.
**Validation.** At least one strategy with **positive OOS walk-forward Sharpe > 1.0**, a DSR that survives the trial count, a parameter *plateau* (not a peak), and fees < 40% of gross P&L. **If no strategy passes, do not proceed to Phase 4.** Return to research. This gate is the entire point of Phases 0–3, and passing it by lowering the bar is the most expensive decision available at this point.

---

### Phase 4 — Risk + OMS · 5 weeks · High

**Objectives.** The machinery that prevents losing money to bugs.

**Deliverables.** Risk engine + full check chain; limits as versioned config; order aggregate + state machine (incl. `UNKNOWN`, `PENDING_CANCEL` race); **deterministic `client_order_id`**; fill dedup; double-entry ledger; reconciliation service; **kill switch (separate process)**; portfolio manager + sizing.

**Risks.** ⚠️ State machine gaps. ⚠️ Risk accidentally made async "for performance." ⚠️ Kill switch depending on the trading core.
**Dependencies.** Phase 0. *(Parallelizable with Phases 2–3 — this is the one place real parallelism exists.)*
**Validation.** Property tests on all invariants (§3.9). Chaos suite: duplicate fills, fills for unknown orders, cancel/fill races, venue timeouts — all handled without state corruption. **Kill switch drilled with the core process `SIGKILL`ed.** Every risk limit demonstrably rejects a violating order.

---

### Phase 5 — Paper Trading · 4 weeks · Medium

**Objectives.** Run the full live path with real data and fake money. Find the bugs that only appear in real time.

**Deliverables.** Paper venue adapter (real data, simulated fills); full live path end-to-end; **30-day continuous soak**; live-vs-backtest divergence report; ops runbooks; alerting wired to a phone.

**Risks.** ⚠️ Paper is too forgiving (always fills, no rejections) → tune it toward pessimism. ⚠️ Bugs that only manifest at 3 a.m. on a Sunday during a Binance maintenance window.
**Dependencies.** Phases 2, 3, 4.
**Validation.** **30 consecutive days, zero unhandled exceptions, zero state corruption, zero reconciliation drift.** Paper P&L within ±20% of what the backtest predicts for the same period. Every alert has fired at least once in a drill and reached a phone.

---

### Phase 6 — Live · Minimum Capital · 3 weeks · High (operational risk, not technical)

**Objectives.** Real money. As little as the exchange will allow.

**Deliverables.** Live venue adapter; production infra (region-matched to venue); secrets in Vault; **withdrawal-disabled, IP-allowlisted API keys**; blue/green deploy; on-call rotation; daily reconciliation report.

**Risks.** ⚠️⚠️ **Everything.** This is where theory meets an exchange that returns a 200 with an error in the body. ⚠️ Emotional pressure to increase size after early wins.
**Dependencies.** Phase 5.
**Validation.** **90 days.** Zero unintended orders. Zero reconciliation drift. Realized slippage within ±25% of model. Live Sharpe within ±0.5 of OOS. **Only then does capital increase — and then only in defined steps, with a defined drawdown trigger to step back down.**

---

### Phase 7 — Scale Out · 8 weeks · Medium

Multi-strategy w/ allocation and per-strategy budgets; second venue; process split (market data / core / venue gateways); NATS; execution algos (TWAP/VWAP); TCA; full dashboard.

**Risks.** Distributed state bugs; cross-strategy netting policy (§16); rate-limit contention across strategies.
**Validation.** Two strategies × two venues, 30 days, no drift. TCA shows execution algos beat naive market orders.

---

### Phase 8 — Institutional Maturity · 12 weeks · High

Attribution; VaR/CVaR/stress; L2 microstructure; ML strategies w/ feature store + model registry; compliance and reporting; **third-party audit of risk and reconciliation**; DR/failover.

---

### Phase 9 — Multi-Asset · 12+ weeks · Very High

Broker adapters (IBKR/Alpaca); calendars, halts, corporate actions; asset-class variant hierarchy realized (§3.8.1); regulatory (PDT, Reg-T, wash sales); tax lots.

**Risks.** ⚠️ **This is the phase most likely to reveal that the crypto-shaped abstraction doesn't generalize.** Budget for refactoring the instrument model. Regulatory complexity is a real, novel category of work — it may require counsel, not engineers.

---

### Timeline Summary

| Phase | Duration | Cumulative | Key Gate |
|---|---|---|---|
| P0 Foundations | 3 wk | 3 wk | — |
| P1 Data Engine | 4 wk | 7 wk | — |
| P2 Backtest Engine | 6 wk | 13 wk | — |
| P3 Strategy Loop | 4 wk | 17 wk | 🔴 Edge found OOS? |
| P4 Risk + OMS *(parallel w/ P2–P3)* | 5 wk | (overlaps) | — |
| P5 Paper Trading | 4 wk | 21 wk | 🔴 30-day clean soak? |
| P6 Live, Min Capital | 3 wk + 12 wk validation | ~36 wk | 🔴 90-day live clean? |
| P7 Scale Out | 8 wk | ~44 wk | — |
| P8 Institutional | 12 wk | ~56 wk | — |
| P9 Multi-Asset | 12+ wk | ~68+ wk | — |

**~9 months from zero to live with minimum capital.** Anyone promising materially faster is skipping Phase 2's validation gate, Phase 5's soak, or both — precisely the two things standing between the team and an expensive education.

Note that Phase 4 (Risk + OMS) runs in parallel with Phases 2–3 (research). This is the only genuine parallelism in the plan, and it exists because risk and OMS depend only on the domain model, not on the research outcome. Everything else is a chain, because each phase's validation gate is the input to the next phase's assumptions.

---

# 15. Risks

Severity = Probability × Impact. **P1** = existential.

## 15.1 Technical

| # | Risk | P | I | Sev | Mitigation |
|---|---|---|---|---|---|
| T1 | **Lookahead bias in backtest** | High | Critical | **P1** | Structural prevention via `MarketDataView`; a "future-reading" strategy must be *unable* to profit; golden-file regression tests |
| T2 | **Float arithmetic in money** | High | Critical | **P1** | `Decimal` everywhere; lint rule banning float in money paths; property tests |
| T3 | **Duplicate order submission** | Med | Critical | **P1** | Deterministic `client_order_id` + DB unique constraint + venue-side idempotency |
| T4 | **Lost fill** | Med | Critical | **P1** | Durable bus + dedup + reconciler backstop |
| T5 | Position drift undetected | Med | Critical | **P1** | Continuous reconciliation; halt-on-drift; never auto-correct |
| T6 | Optimistic fill model | High | High | P2 | Pessimistic defaults; compare paper vs. backtest; TCA vs. model |
| T7 | Overfitting | **Very High** | Critical | **P1** | Walk-forward; DSR; PBO; **run registry**; parameter-plateau test |
| T8 | Survivorship bias in data | High | High | P2 | Point-in-time universe **from day one**; vendor with delisted coverage |
| T9 | Silent stale data feed | Med | High | P2 | Per-symbol staleness watchdog independent of socket state |
| T10 | Clock skew | Low | High | P3 | Monitored NTP; halt on >1 s |
| T11 | Vectorized/incremental indicator divergence | Med | Med | P3 | Cross-test on random data every build |
| T12 | Deploy with open positions | Med | High | P2 | Deploy-when-flat policy; cancel-all on start; reconcile before resume |

## 15.2 Business

| # | Risk | P | I | Sev | Mitigation |
|---|---|---|---|---|---|
| B1 | **No strategy has real edge** | **High** | Critical | **P1** | Phase 3 gate. Be willing to stop. This is the most likely outcome and the hardest to accept. |
| B2 | **Strategy decays** | **Certain** | High | **P1** | Decay monitors defined *before* going live; a written "when do we stop" rule; a research pipeline, not a single strategy |
| B3 | Building the platform *instead of* finding alpha | **High** | High | **P1** | Ruthless MVP scope; the Phase 3 gate exists to force this question |
| B4 | Capital scales, edge doesn't (market impact) | Med | High | P2 | Explicit `capacity_usd`; impact model; monitor realized vs. modeled slippage |
| B5 | Fees consume the edge | High | High | P2 | Fees-as-%-of-gross metric from Phase 2; maker rebates; VIP tiers |
| B6 | Single-person key-man risk | High | High | P2 | Documentation; runbooks; a second person who has actually run a drill |
| B7 | Regulatory change (crypto) | Med | High | P2 | Venue diversification; jurisdiction awareness; counsel before external capital |

## 15.3 Security

| # | Risk | P | I | Sev | Mitigation |
|---|---|---|---|---|---|
| S1 | **API key theft → withdrawal** | Low | **Catastrophic** | **P1** | **Withdrawals disabled on every key.** IP allowlist. Vault. *This one control removes the worst case.* |
| S2 | Key theft → malicious trading | Low | High | P2 | Risk limits bound the damage; anomaly detection; rapid revocation runbook |
| S3 | Dependency supply-chain attack | Med | Critical | **P1** | Pinned + hashed lockfiles; audit in CI; minimal deps in the trading core |
| S4 | Insider / accidental limit change | Med | High | P2 | Limits as reviewed code; two-person approval; immutable audit |
| S5 | Dashboard XSS/CSRF | Med | Med | P3 | Read-only API; no order-entry endpoint; CSP; SameSite |
| S6 | Exchange itself is compromised/insolvent | Low | **Catastrophic** | **P1** | **Never hold more capital on a venue than you can afford to lose.** Sweep to cold storage on a schedule. Diversify venues. *FTX was not a tail event; it was a Tuesday.* |

## 15.4 Performance / Exchange / Maintenance / Scaling

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| P1 | Event-loop blocked by slow strategy | P2 | Per-eval timeout; auto-pause; later, process isolation |
| P2 | Backtest too slow → research velocity dies | P2 | Profile early; Rust hot path if proven necessary; vectorized pre-screen |
| P3 | DB write amplification at tick rate | P3 | Batch `COPY`; async writes; Timescale compression |
| X1 | Venue API breaking change | P2 | Contract tests vs. recorded fixtures; monitor changelogs; adapter isolation |
| X2 | Venue outage / degraded matching | P2 | Multi-venue; halt on anomaly; never retry blindly into a degraded venue |
| X3 | Rate-limit ban | P2 | Client-side budget manager; conservative headroom |
| X4 | Venue liquidation of position | **P1** | Conservative leverage; margin monitoring; alert well before maintenance margin |
| M1 | Architecture decays without enforcement | P2 | import-linter in CI; ADRs; regular review |
| M2 | Test suite too slow → gets skipped | P2 | Domain tests <2 s; integration in a separate tier |
| M3 | Knowledge concentrated in one head | P2 | Runbooks; pair on incidents; rotate on-call |
| SC1 | Premature distribution | P2 | Modular monolith; distribute only on profiling evidence |
| SC2 | Redesign forced by asset-class expansion | P2 | Sealed variant hierarchy (§3.8.1); accept some refactor as inevitable and budget for it |

## 15.5 The Three That Will Actually Get You

Everything above is real, but the three that empirically end projects like this one:

1. **T7 · Overfitting.** Near-certain to occur. Feels exactly like success. The backtest is beautiful; live is flat or negative; the conclusion drawn is that the implementation is buggy, and three months are spent looking for a bug that isn't there. Defense: walk-forward, DSR, the run registry, and the parameter-plateau test — all built *before* there is a strategy anyone is emotionally invested in protecting.

2. **B3 · Building the platform instead of finding alpha.** Engineers, given a choice between a hard research problem with an uncertain payoff and a satisfying engineering problem with a clear payoff, will choose the engineering problem every single time, and will produce excellent work, and the firm will go bankrupt anyway. **The platform is not the product. The edge is the product.** The Phase 3 gate exists specifically to force this question at a point where there is still runway.

3. **S6 · Exchange counterparty risk.** No amount of correct software protects against a venue that loses your money. Mt. Gox, QuadrigaCX, FTX, Celsius. **Keep on the venue only the working capital the strategies actually require, sweep the rest, and treat any exchange balance as an unsecured loan to a lightly-regulated entity** — because that is precisely what it is.

---

# 16. Open Questions Requiring Approval

Recommendations have been made throughout, but the following are decisions for the founder/business owner, not the architect. Several of them invalidate large parts of this document if answered differently than assumed here. The blocking ones are flagged 🔴.

## 16.1 Strategy & Business — 🔴 BLOCKING

| # | Question | Why It Matters | Recommendation |
|---|---|---|---|
| Q1 | 🔴 **What is the actual latency requirement?** Mid-frequency (this document) or HFT/market making? | Determines language, architecture, hosting, cost, team. **This document is invalid for HFT.** | Mid-frequency. HFT is a different firm. |
| Q2 | 🔴 **Is there already a strategy with demonstrated edge, or is the platform being built to find one?** | If no edge exists, Phases 0–3 are the entire project and Phases 4–9 may never happen. | Be honest. If no edge: cut scope hard, spend the time on research. |
| Q3 | 🔴 **What capital, and on what schedule?** $10k behaves nothing like $10M. | Drives capacity, market impact, venue choice, whether custody matters, whether any of §15.3 is optional. | — |
| Q4 | 🔴 **Will this ever manage other people's money?** | If yes: regulatory, compliance, audit, custody, and segregation obligations change the architecture materially and expensively. **Answer now, not in Year 2.** | Assume no; design the audit log as though yes. |
| Q5 | **Directional or market-neutral? Long-only or long/short?** | Determines risk model, margin, whether shorting infrastructure is needed. | — |
| Q6 | **Expected holding period?** | Minutes vs. weeks changes fee sensitivity, data granularity, and infrastructure by an order of magnitude. | — |
| Q7 | **What is the maximum tolerable drawdown, as a number?** | Sets every risk limit in §10.4. Must be decided *before* experiencing one. | 15% account-level halt. |

## 16.2 Scope & Product

| # | Question | Recommendation |
|---|---|---|
| Q8 | Which exchange first? | **Binance** (deepest liquidity, best API/docs) or **Coinbase** (US-regulated). Choose on jurisdiction. |
| Q9 | Spot, perpetuals, or both? | **Spot first.** Perps add funding, mark price, liquidation, and leverage — a genuinely different risk model. |
| Q10 | How many strategies at launch? | **One.** Not two. |
| Q11 | Is DeFi/on-chain in scope? | **No.** MEV, gas, key custody, non-atomic settlement — a separate product. |
| Q12 | Is the dashboard a Phase-1 need, or is a CLI + Grafana sufficient? | **CLI + Grafana for MVP.** The dashboard is 6 weeks that could be spent on research. |
| Q13 | Is multi-asset genuinely needed, or is that aspiration? | If aspiration, **say so** — it removes ~40% of the abstraction burden and §3.8 could be simplified considerably. |

## 16.3 Technical

| # | Question | Recommendation |
|---|---|---|
| Q14 | Confirm Python. Any hard requirement for another language? | Python. Revisit only if Q1 says HFT. |
| Q15 | Self-hosted or managed cloud? | AWS, region-matched to venue. Managed Postgres (RDS) — avoid operating a database directly if possible. |
| Q16 | Budget for market data vendors? (**delisted-symbol coverage is not optional** at real capital) | Budget $500–2000/mo before Phase 6. |
| Q17 | **Two strategies want opposite positions in the same instrument. Net, or trade both?** | **Net at the portfolio level, but attribute P&L per strategy.** Saves fees; keeps attribution. Requires care. |
| Q18 | Per-strategy positions, or one account-level position? | Per-strategy (logical), netted at the venue (physical). |
| Q19 | Is L2 order book data needed in Phase 1? | **No.** It is the largest storage cost and only microstructure/execution strategies need it. |
| Q20 | Build the backtester, or adopt Nautilus Trader? | **Build.** But read Nautilus first, thoroughly. |
| Q21 | Is a 60-second maintenance window (positions flat) acceptable for deploys? | **Yes.** Zero-downtime deploy is not worth the complexity in Year 1. |

## 16.4 Risk & Ops

| # | Question | Recommendation |
|---|---|---|
| Q22 | 🔴 **Who is on call, and what is the response SLA?** A 24/7 market with nobody watching is an unbounded liability. | Define before Phase 6. If nobody can be on call, **reduce leverage until an unattended overnight loss is survivable.** |
| Q23 | Who may change risk limits in production? | Two-person rule. Even when the two people are the founder and a rubber duck, the friction is the point. |
| Q24 | Auto-resume after a drawdown halt, or human-only? | **Human-only.** The condition that caused it is often still present. |
| Q25 | What triggers *retiring* a strategy? Define the number now. | Live Sharpe < 0 over 3 months, **or** drawdown exceeds 1.5× backtest max DD. Write it down before becoming emotionally invested. |
| Q26 | How much capital sits on the exchange vs. cold storage? | **Only working capital on-venue.** Sweep the rest weekly. |
| Q27 | Is the kill switch drilled monthly, in production, with real positions? | **Yes.** An undrilled kill switch does not work. |

## 16.5 Team & Process

| # | Question | Recommendation |
|---|---|---|
| Q28 | 🔴 **How many engineers, and what is their trading-systems experience?** | The roadmap assumes 2. One person makes it ~18 months. |
| Q29 | Is there a separate quant researcher, or is the engineer also the researcher? | If the same person: **expect the platform to be over-built and the research under-done.** This is the default failure mode and it requires active resistance. |
| Q30 | Who signs off on going live? | Not the person who wrote the strategy. |

---

## Closing Note

Three things worth keeping on the record before any line of code is written.

**The Phase 3 gate is real.** If no strategy passes walk-forward validation with a Deflated Sharpe that survives its trial count, do not proceed to Phase 4. Continuing anyway is the single most expensive decision available in this project, and it will be argued for persuasively — using the sunk cost of the platform as the argument. Decide now, in a calm moment, to honor the gate. Write it down. Tell someone.

**The kill switch is the most important component in this document.** Not the strategy engine. Not the backtester. When everything else has failed in a way that was not anticipated — and it will, because the space of ways a system can fail is larger than the space of ways it can be imagined to fail — the kill switch is what stands between a bad day and an unrecoverable one. Build it early, keep it stupid, drill it monthly.

**Disable withdrawals on the exchange API keys right now.** It takes ninety seconds and it converts the worst outcome in this entire risk register from "everything, gone" to "some bad trades, bounded by limits under your control." It is, by a wide margin, the highest return on ninety seconds available anywhere in this project.

---

*End of document. Awaiting answers to §16 — particularly Q1–Q4, Q22, and Q28 — before any implementation begins. Several sections will need material revision depending on those answers.*
