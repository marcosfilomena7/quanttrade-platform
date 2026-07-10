# QuantTrade Platform — Implementation Task Registry

> Source of truth: `docs/ARCHITECTURE.md` · `docs/DATABASE.md`
>
> **Rules:**
> - Every task fits in one coding session (≤ 8 focused hours).
> - Every task is independently testable before the next begins.
> - Dependencies list only **blocking** predecessors.
> - Complexity: **Low** = known pattern, straightforward; **Medium** = non-trivial logic or integration; **High** = subtle correctness or complex state.

---

## Phase 0 — Foundations
**Goal:** Repository, CI, domain model, port interfaces, Decimal money, secrets, observability skeleton, dev environment.
**Estimate:** 3 weeks (2 engineers). **Depends on:** Nothing.

---

### T-P0-01 · Monorepo Structure, Tooling, and Layer Enforcement

**Description.** Create the project directory layout (`domain/`, `application/`, `infrastructure/`, `tests/`). Configure `pyproject.toml` with `mypy --strict`, `ruff`, `pytest`, and `import-linter`. The import-linter rule must make the build fail if any `domain/` module imports from `infrastructure/` or `application/`. Add a `Makefile` with `make test`, `make lint`, `make typecheck` targets.

**Dependencies.** None.

**Acceptance Criteria.**
- `make lint` and `make typecheck` pass on an empty project.
- Deliberately adding `from infrastructure.db import Session` inside `domain/order.py` causes `make lint` to fail with an import-linter violation.
- A new engineer can clone the repo and run `make test` successfully within 60 minutes of setup.

**Complexity.** Low

---

### T-P0-02 · GitHub Actions CI Pipeline

**Description.** Create `.github/workflows/ci.yml` running on every push and pull request: install deps, run `ruff`, `mypy --strict` on `domain/` and `application/`, `pytest` (unit only, no Docker). Fail fast. Cache pip dependencies.

**Dependencies.** T-P0-01.

**Acceptance Criteria.**
- CI runs in < 3 minutes on a clean push.
- A deliberate mypy error in `domain/` fails the pipeline.
- A deliberate test failure fails the pipeline.
- A passing push shows green status on GitHub.

**Complexity.** Low

---

### T-P0-03 · Money and Decimal Value Objects

**Description.** Implement `Money(amount: Decimal, currency: str)` as an immutable value object in `domain/`. Implement arithmetic (`+`, `-`, `*`, `/`) that returns `Money` and raises on currency mismatch. Add a `ruff` custom rule (or `mypy` plugin / `ast` check in CI) that bans `float` literals and `float` type annotations anywhere inside `domain/` and `application/`.

**Dependencies.** T-P0-01.

**Acceptance Criteria.**
- `Money(Decimal("10.5"), "USD") + Money(Decimal("4.5"), "USD") == Money(Decimal("15.0"), "USD")`.
- Adding currencies raises `CurrencyMismatch`.
- `float` appearing in `domain/order.py` causes `make lint` to fail.
- All arithmetic uses `Decimal` internally — no float path exists.

**Complexity.** Low

---

### T-P0-04 · Instrument Domain Model (Sealed Variant Hierarchy)

**Description.** Implement `Instrument` core (id, venue_id, symbol, base, quote, tick_size, lot_size, min_notional, status) plus sealed asset-class variant hierarchy: `Spot`, `PerpetualSwap` (funding_interval, mark_price_type), `DatedFuture` (expiry, multiplier, roll_days). Use `asset_class: Literal[...]` discriminator. Exhaustive pattern matching must be testable via mypy's `assert_never`.

**Dependencies.** T-P0-03.

**Acceptance Criteria.**
- Adding a new variant without handling it in a `match` block causes a mypy error (`assert_never` unreachable).
- `PerpetualSwap` has `funding_interval`; `Spot` does not — accessing `.funding_interval` on a `Spot` is a type error.
- Round-trip serialization (Pydantic v2) preserves all fields and the discriminator.
- Unit tests cover all three variants.

**Complexity.** Medium

---

### T-P0-05 · Order Domain Model and State Machine

**Description.** Implement the `Order` aggregate and `OrderEvent` in `domain/`. States: `PendingNew → Sent → {Acked | Rejected | Unknown}`. From `Acked`: `PartiallyFilled`, `Filled`, `PendingCancel → {Canceled | Filled | PartiallyFilled}`, `Expired`. Terminal states: `Filled`, `Canceled`, `Rejected`, `Expired`. Illegal transitions must raise `InvalidOrderTransition`, never silently no-op. `OrderEvent` records each transition with timestamp and payload.

**Dependencies.** T-P0-03, T-P0-04.

**Acceptance Criteria.**
- Every legal transition is exercised in a test with the correct resulting state.
- Every illegal transition (e.g., `Filled → Sent`) raises `InvalidOrderTransition`.
- `PendingCancel → Filled` is legal (cancel/fill race).
- Rebuilding an `Order` from its `OrderEvent` sequence produces the same state as applying transitions live.
- No `datetime.now()` or external I/O in this module.

**Complexity.** High

---

### T-P0-06 · Fill and Position Domain Models

**Description.** Implement `Fill` (id, order_id, venue_fill_id, qty: Decimal, price: Decimal, fee: Money, ts, is_maker) and `Position` (instrument_id, strategy_instance_id, qty: Decimal, avg_entry: Decimal, realized_pnl: Money) in `domain/`. Implement `Position.apply_fill(fill: Fill) -> Position` using FIFO lot math. All arithmetic uses `Decimal`.

**Dependencies.** T-P0-03, T-P0-04.

**Acceptance Criteria.**
- `Position.apply_fill` property tests: `Σ(signed fill quantities) == position.qty` always holds.
- Partial close reduces qty and books realized P&L correctly.
- Cash after fill = cash before − notional − fee, exactly in Decimal.
- Applying the same fill twice produces the same state as applying it once (idempotent).
- No float in any code path.

**Complexity.** High

---

### T-P0-07 · Port Interfaces (EventBus, Clock, Venue, MarketDataFeed)

**Description.** Define abstract base classes (Python `Protocol` or `ABC`) in `domain/ports/`: `EventBus` (publish, subscribe), `Clock` (now() → datetime), `VenuePort` (submit, cancel, cancel_all, get_open_orders, get_positions, get_balances, get_fills_since, subscribe_user_stream, capabilities), `MarketDataFeed` (subscribe, unsubscribe). No implementations — only interfaces. Add `MarketDataView` Protocol: `bars(symbol, timeframe, n) → Sequence[Candle]` with the constraint that it cannot return data past the simulated present (enforced structurally, not by convention).

**Dependencies.** T-P0-04, T-P0-05, T-P0-06.

**Acceptance Criteria.**
- All ports are importable from `domain/ports/` with no infrastructure imports.
- mypy confirms every port method has typed signatures.
- A stub implementation of each port passes mypy without warnings.
- `VenuePort` exposes `capabilities() -> frozenset[str]` so callers can query before constructing an order.

**Complexity.** Low

---

### T-P0-08 · Observability Skeleton (Structured Logging, Metrics, Correlation IDs)

**Description.** Configure `structlog` to emit JSON to stdout. Implement a `CorrelationContext` (contextvars-based) that threads `signal_id → intent_id → order_id → fill_id` through log records automatically. Set up `prometheus_client` with a registry. Define metric families: `order_submissions_total`, `order_rejections_total{reason}`, `risk_decisions_total{outcome}`, `fill_processing_seconds`, `data_staleness_seconds{symbol}`. All are counters or histograms — no gauges that could reflect stale state silently.

**Dependencies.** T-P0-01.

**Acceptance Criteria.**
- A log line emitted inside a coroutine carrying a `CorrelationContext` includes all IDs in the JSON output.
- Prometheus metrics are scrapeable at `/metrics` from a minimal FastAPI app.
- No log line is emitted without a `level`, `event`, and `ts` field.
- `data_staleness_seconds` can be updated per symbol without a metric per symbol being pre-declared.

**Complexity.** Low

---

### T-P0-09 · Secrets Management Skeleton

**Description.** Implement a `SecretsClient` abstraction in `infrastructure/secrets/` with two implementations: `VaultSecretsClient` (HashiCorp Vault KV v2) and `EnvSecretsClient` (reads `os.environ`, for local dev only, never in prod). The abstraction exposes `get(key: str) -> str` and `zeroize()`. Config loading must call `SecretsClient`, never read `os.environ` directly for credentials. Add a CI check that bans `os.environ["*KEY*"]` patterns outside `infrastructure/secrets/`.

**Dependencies.** T-P0-01.

**Acceptance Criteria.**
- Unit tests mock `VaultSecretsClient` and confirm `get()` returns the correct value.
- `EnvSecretsClient` raises `SecretsClientError` if the key is absent.
- `zeroize()` overwrites the in-memory string with zeros (best effort in Python).
- A deliberate `os.environ["BINANCE_API_KEY"]` in `application/` fails the CI check.

**Complexity.** Low

---

### T-P0-10 · Docker-Compose Dev Environment

**Description.** Create `docker-compose.yml` with: PostgreSQL 16 + TimescaleDB extension, Redis 7, Grafana (pre-seeded with a placeholder datasource), and a test-only Postgres instance on a different port. Add a `scripts/init_db.sh` that creates the app user and database. Add a `make dev-up` target. No application containers in this compose — only infrastructure.

**Dependencies.** None.

**Acceptance Criteria.**
- `make dev-up` starts all containers in under 60 seconds.
- `psql` to the app database succeeds with the configured user.
- TimescaleDB extension is enabled: `SELECT extname FROM pg_extension WHERE extname = 'timescaledb'` returns a row.
- `make dev-down` tears down cleanly with no volumes left over (use `--volumes` flag).

**Complexity.** Low

---

### T-P0-11 · Alembic Database Migrations — Baseline Schema

**Description.** Set up Alembic. Write the initial migration creating all 23 entities from `docs/DATABASE.md`: Venue, Instrument, UniverseSnapshot, Candle (hypertable), TradeTick (hypertable), Strategy, StrategyInstance, Signal, OrderIntent, RiskDecision, Order, OrderEvent, Fill, Position, LedgerEntry, EquitySnapshot (hypertable), EventLog, DatasetVersion, BacktestRun, BacktestTrade, BacktestMetrics. Add the two critical UNIQUE constraints: `order(venue_id, client_order_id)` and `fill(venue_id, venue_fill_id)`. EventLog has NO foreign keys (by design — see ARCHITECTURE.md §7). RiskLimitConfig table is permanently excluded.

**Dependencies.** T-P0-10.

**Acceptance Criteria.**
- `alembic upgrade head` runs against a clean database with zero errors.
- `alembic downgrade -1` reverses cleanly.
- Both UNIQUE constraints are present: inserting a duplicate `(venue_id, client_order_id)` in `order` raises `UniqueViolation`.
- EventLog has no FK columns.
- Candle, TradeTick, and EquitySnapshot are TimescaleDB hypertables (`select_tablespace` returns `_timescaledb_internal`).

**Complexity.** Medium

---

### T-P0-12 · Architecture Decision Records (ADR-000 through ADR-004)

**Description.** Commit ADR-000 (mid-frequency Python, not HFT), ADR-001 (Python language selection), ADR-002 (PostgreSQL + TimescaleDB), ADR-003 (asyncio queues → NATS), ADR-004 (SQLAlchemy 2.0 Core, no ORM in domain) to `docs/adr/`. Each ADR: Status, Context, Decision, Consequences, Supersedes/Superseded-by. These are living documents — never deleted, only superseded with a forward link.

**Dependencies.** T-P0-01.

**Acceptance Criteria.**
- Five files exist in `docs/adr/` following a consistent template.
- Each ADR references the corresponding section of ARCHITECTURE.md.
- CI checks that no ADR file is deleted (only new files allowed in `docs/adr/`).

**Complexity.** Low

---

## Phase 1 — Data Engine
**Goal:** Ingest, store, validate, and serve market data. Start universe snapshots immediately.
**Estimate:** 4 weeks. **Depends on:** Phase 0 complete.

---

### T-P1-01 · Venue REST Client (Binance, Rate-Limit-Aware)

**Description.** Implement a `BinanceRestClient` in `infrastructure/venues/binance/` using `httpx`. Enforce Binance's weight-based rate limit client-side: track remaining weight from response headers, back off when approaching the limit, and reset on the 1-minute boundary. Sign requests with HMAC-SHA256. Return typed response models (Pydantic v2), never raw dicts.

**Dependencies.** T-P0-07, T-P0-09.

**Acceptance Criteria.**
- Unit tests replay recorded HTTP fixtures (VCR / `httpx` mock transport) covering: success, 429 rate-limit, 418 IP ban, 5xx retry, `-1021` timestamp error.
- A 429 response triggers backoff; the client does not immediately retry.
- A 418 response raises `VenueIPBanError` (not retried automatically).
- All typed response fields use `Decimal` for price and quantity fields, never `float`.

**Complexity.** Medium

---

### T-P1-02 · Reference Data Importer

**Description.** Implement a job that calls Binance `GET /api/v3/exchangeInfo`, maps the response to `Instrument` domain objects, and upserts into the `instrument` table via `SQLAlchemy 2.0 Core`. Detect and alert on tick-size or lot-size changes for existing instruments (silent spec changes cause rejected orders). Schedule to run daily.

**Dependencies.** T-P1-01, T-P0-11.

**Acceptance Criteria.**
- Running the importer twice is idempotent (no duplicate rows).
- A simulated tick-size change logs a warning and emits a `reference_data_changed` metric.
- All numeric fields (tick_size, lot_size, min_notional) are stored as `NUMERIC` in Postgres and returned as `Decimal` in Python.
- Integration test runs against the test Postgres instance (testcontainers or local dev-compose).

**Complexity.** Low

---

### T-P1-03 · Point-in-Time Universe Snapshot Job

**Description.** Implement a daily job that inserts one `UniverseSnapshot` row per active instrument per venue (date, venue_id, instrument_id, is_tradeable). This captures which symbols were listed and tradeable on each calendar day. Must run on day one, before any backtesting, because the data is unrecoverable retroactively. Schedule immediately alongside the reference data importer.

**Dependencies.** T-P1-02.

**Acceptance Criteria.**
- Running the job on consecutive days produces two rows per instrument (one per date), not one.
- An instrument delisted between two runs has `is_tradeable = false` on the second snapshot.
- A query `SELECT instrument_id FROM universe_snapshot WHERE date = $1 AND is_tradeable = true` returns the correct set for any historical date.
- Integration test verifies the point-in-time query returns different sets for two different dates when one instrument was delisted.

**Complexity.** Low

---

### T-P1-04 · Historical OHLCV Backfill (Chunked, Idempotent, Resumable)

**Description.** Implement a backfill job: given a symbol and date range, fetch OHLCV data from Binance REST in rate-limit-safe chunks (1000 bars per request), upsert into the `candle` hypertable on `(instrument_id, open_time)`, log progress to a checkpoint table so interrupted runs resume from the last successful chunk. Run gap detection after each chunk and alert on detected gaps.

**Dependencies.** T-P1-01, T-P0-11.

**Acceptance Criteria.**
- Backfilling the same range twice produces no duplicates (idempotent).
- Killing the process mid-backfill and restarting resumes from the last checkpoint, not from the beginning.
- Integration test: backfill 30 days of 1m BTC/USDT candles, then verify row count matches `30 * 24 * 60 = 43,200` (no gaps).
- All OHLCV values stored as `NUMERIC`, not `FLOAT`.

**Complexity.** Medium

---

### T-P1-05 · Data Validation Suite

**Description.** Implement a validation pipeline that runs on ingested data: (1) OHLC invariants: `high ≥ max(open, close)`, `low ≤ min(open, close)`, `high ≥ low`; (2) timestamps strictly monotonic and aligned to the interval boundary; (3) volume ≥ 0; zero-volume streaks flagged after N consecutive bars; (4) price move > N σ (rolling window) flagged, not dropped; (5) no missing intervals in a trading window. Violations write to a `data_quality_event` log table and emit metrics. Data is quarantined, never silently dropped.

**Dependencies.** T-P0-11.

**Acceptance Criteria.**
- Injecting a candle with `high < close` produces a quarantine record and a metric increment.
- A 20σ price spike is flagged but the candle is retained (not deleted).
- Running the suite against the 30-day BTC dataset from T-P1-04 produces zero violations (clean data).
- Injecting synthetic corruption (gap, inverted OHLC, non-monotonic timestamp) is caught in each corresponding test.

**Complexity.** Medium

---

### T-P1-06 · Gap Detection and Auto-Backfill Scheduler

**Description.** Implement a gap detector: scan the `candle` table for missing `open_time` intervals within trading windows (for crypto: 24/7, so every minute must have a row). Automatically trigger the backfill job for detected gaps. Run on startup and on a nightly schedule. Log all detected gaps to `data_quality_event`.

**Dependencies.** T-P1-04, T-P1-05.

**Acceptance Criteria.**
- Manually deleting 5 rows from the candle table and running the detector identifies all 5 missing intervals.
- The auto-backfill job fills the gaps and the subsequent detector scan finds zero gaps.
- Gaps spanning a known exchange maintenance window are classified separately (not a data error).

**Complexity.** Medium

---

### T-P1-07 · WebSocket Client (Persistent Connection, Heartbeat, Sequence Tracking)

**Description.** Implement a `BinanceWebSocketClient` that maintains a persistent WS connection to the Binance stream endpoint, sends `ping` frames on the Binance-required 30-second heartbeat, tracks sequence numbers where available, detects silent connection death (open socket, no messages for > threshold seconds), and emits a `FeedStale` event. Uses exponential backoff with jitter on reconnect.

**Dependencies.** T-P1-01.

**Acceptance Criteria.**
- Simulating socket silence for > threshold seconds triggers a `FeedStale` event and a reconnect attempt.
- Reconnect uses exponential backoff: delays are non-zero, increasing, and jittered (not identical across reconnects).
- Unit test with a mock WS server: 1000 messages received without drops.
- A forcefully closed connection triggers reconnect within 5 seconds.

**Complexity.** Medium

---

### T-P1-08 · Realtime Candle Stream (Normalize, Stamp, Publish)

**Description.** Subscribe to Binance's `<symbol>@kline_<interval>` stream. Normalize each WS payload to a `Candle` domain object. Stamp both `exchange_ts` (from the venue payload) and `local_recv_ts` (local monotonic clock). Distinguish between `is_closed = true` (full bar) and partial candles — only publish `CandleClosed` events for closed bars. Partial candles are buffered for the latest-tick Redis cache only.

**Dependencies.** T-P1-07, T-P0-08.

**Acceptance Criteria.**
- A partial candle (is_closed = false) does not emit a `CandleClosed` event.
- A closed candle emits exactly one `CandleClosed` event with both timestamps populated.
- `exchange_ts != local_recv_ts` — they are independent fields, never conflated.
- 1000 simulated WS messages processed with zero events emitted for partial candles and exactly the expected count of `CandleClosed` events.

**Complexity.** Low

---

### T-P1-09 · WebSocket Reconnect with REST Gap-Fill

**Description.** On every reconnect, unconditionally trigger a REST backfill of the interval `[last_known_bar_ts, now]` before resuming WS consumption. This prevents silent data gaps from reconnects. The sequence: detect disconnect → backoff → reconnect → REST gap-fill → validate → resume publishing. Gap-fill must complete before resuming event publication.

**Dependencies.** T-P1-07, T-P1-08, T-P1-04.

**Acceptance Criteria.**
- Forcing a WS disconnect while the client is running results in a gap-fill covering the disconnected interval before the next `CandleClosed` event is published.
- If the gap-fill itself fails (REST 5xx), publication remains halted and the failure is logged.
- Integration test: simulate 10 forced reconnects over 10 minutes; verify zero gaps in the stored candle data afterward.

**Complexity.** Medium

---

### T-P1-10 · Per-Symbol Data Staleness Watchdog

**Description.** Implement a per-symbol watchdog independent of connection state. Every second, check `now - last_received_ts[symbol]` against a configurable threshold (default: 60 seconds). If exceeded, emit a `FeedStale` event, increment `data_staleness_seconds{symbol}` Prometheus metric, and trigger a P1 alert. The watchdog runs in a separate async task, not the main receive loop — an open socket carrying no data must not suppress it.

**Dependencies.** T-P1-08, T-P0-08.

**Acceptance Criteria.**
- Stopping the WS client while the watchdog runs triggers a `FeedStale` event within 65 seconds.
- The watchdog does not trigger for a symbol that is actively receiving data.
- The watchdog's staleness check is independent of the WS connection state variable (tests confirm it fires even when the socket object reports `connected = True`).

**Complexity.** Low

---

### T-P1-11 · Parquet Archival Pipeline (Hot → Cold Tier)

**Description.** Implement a nightly job that exports candle and tick data older than 90 days from Postgres to Parquet files on object storage (S3 or local filesystem for dev), partitioned as `venue/instrument/year/month/data.parquet`. After a successful export and checksum verification, delete the corresponding Postgres rows. Register the export as a `DatasetVersion` with a content hash.

**Dependencies.** T-P0-11, T-P1-04.

**Acceptance Criteria.**
- After archival, querying the archived date range from Postgres returns zero rows.
- The Parquet file exists at the correct path with the correct row count.
- Content hash in `DatasetVersion` matches the Parquet file's SHA-256.
- A failed checksum aborts deletion and emits an alert.
- Re-running the archival job for an already-archived period is idempotent (no duplicate files, no error).

**Complexity.** Medium

---

### T-P1-12 · DatasetVersion Content Hash and Versioning

**Description.** Implement `DatasetVersion` creation: when a dataset is finalized (either from backfill or archival), compute a content hash over `(symbol_set, date_range, row_count, sample_hashes)` and store it in the `dataset_version` table. Expose a `DatasetVersionRepository.get(id)` that returns the version record, enabling backtests to pin a dataset version and be exactly reproducible.

**Dependencies.** T-P0-11.

**Acceptance Criteria.**
- Two identical dataset exports produce the same content hash.
- Modifying one row in the dataset produces a different hash.
- `DatasetVersionRepository.get(id)` returns the exact record used to generate a hash.
- A backtest run storing `dataset_version_id` can later retrieve the same version metadata.

**Complexity.** Low

---

## Phase 2 — Backtest Engine
**Goal:** Trustworthy, reproducible, lookahead-free backtesting. The most important phase.
**Estimate:** 6 weeks. **Depends on:** Phase 1 complete.

---

### T-P2-01 · SimulatedClock

**Description.** Implement `SimulatedClock` as the `Clock` port. The clock advances only when `advance_to(ts: datetime)` is called by the backtest loop. `now()` returns the current simulated time. The clock must be injected — strategies never call `datetime.now()` directly (enforced by the lint rule from T-P0-01). A `RealClock` implementation also provided for live use.

**Dependencies.** T-P0-07.

**Acceptance Criteria.**
- `SimulatedClock.now()` returns exactly the value last set by `advance_to()`.
- Calling `now()` before any `advance_to()` raises `ClockNotInitialized`.
- `advance_to()` with a time earlier than current raises `ClockRegressionError`.
- `RealClock.now()` returns a timezone-aware UTC datetime within 1 second of `datetime.utcnow()`.
- Both implement the `Clock` protocol and are interchangeable.

**Complexity.** Low

---

### T-P2-02 · MarketDataView — Lookahead Prevention by Construction

**Description.** Implement `MarketDataView`: a read-only cursor over historical data. The view holds a `current_ts` pointer. `bars(symbol, timeframe, n)` returns the last `n` closed bars *before* `current_ts`. Attempting to access data at or after `current_ts` is structurally impossible — not blocked at runtime with an `if` check, but architecturally absent from the return value. The view is advanced externally by the backtest loop via `advance(ts)`. Strategies receive a `MarketDataView` — they have no other data access path.

**Dependencies.** T-P2-01, T-P0-07.

**Acceptance Criteria.**
- `view.bars("BTC/USDT", "1m", 10)` with `current_ts = T` returns the 10 bars ending at `T-1m` (not `T`).
- A strategy that attempts to use `bars()` to "see" data from the current bar gets the bar before the cursor, never the current one.
- A "clairvoyant" test strategy that tries to peek at the next bar gets zero signal (cannot index past cursor).
- The structural test: calling any `view` method after advancing the cursor to `T+1` never reveals data from after `T`.

**Complexity.** High

---

### T-P2-03 · Historical Feed and Event Ordering

**Description.** Implement `HistoricalFeed` as the `MarketDataFeed` port for backtesting. Loads bars from a `DatasetVersion` (Parquet or Postgres). Emits events in strictly monotonic timestamp order across all symbols and timeframes. Multiple timeframes are merged into a single event stream: a 1h bar closes only when all contained 1m bars have been processed. Uses a min-heap for merge ordering.

**Dependencies.** T-P2-01, T-P1-12, T-P0-07.

**Acceptance Criteria.**
- Events from two symbols interleaved by timestamp are emitted in correct chronological order.
- A 1h `CandleClosed` event is never emitted before all 60 constituent 1m bars.
- Loading a `DatasetVersion` with a known content hash produces events deterministically.
- Exhausting the feed raises `FeedExhausted` rather than silently stopping.

**Complexity.** Medium

---

### T-P2-04 · Backtest Event Loop (Core Simulation Loop)

**Description.** Implement the main backtest loop in `application/backtest/`: (1) load dataset version; (2) init `SimulatedClock`; (3) init strategy and warm up (discard signals during warmup); (4) loop: advance clock → update `MarketDataView` cursor → process pending fills from prior bar → call `strategy.on_data(event, view)` → if signal: pass to Portfolio → if intent: pass to SimulatedVenue → queue fill. Warmup bars do not trigger signals.

**Dependencies.** T-P2-01, T-P2-02, T-P2-03, T-P0-07.

**Acceptance Criteria.**
- A strategy that generates a signal on every bar produces zero signals during its declared warmup period.
- Signals from warmup bars are silently discarded, not counted in metrics.
- A strategy running on 1000 bars with a 50-bar warmup processes exactly 950 signal opportunities.
- The loop is deterministic: two runs with the same dataset version and seed produce the same sequence of events.

**Complexity.** High

---

### T-P2-05 · SimulatedVenue — Fee Model

**Description.** Implement fee calculation inside `SimulatedVenue`: maker/taker fees by configurable tier schedule (as a percentage of notional), funding rate charges for perps (accrued every 8 hours against the open position at the simulated time). Fee schedules are loaded from config, not hardcoded. Fees are deducted in the fill and propagated to the ledger.

**Dependencies.** T-P0-05, T-P0-06.

**Acceptance Criteria.**
- A taker order for 1 BTC at $50,000 with a 0.1% taker fee produces a fee of `Money(Decimal("50.0"), "USDT")`.
- A maker order uses the maker fee rate, not the taker rate.
- A perp position open for 8 hours accrues exactly one funding payment.
- Fees-as-percentage-of-gross-P&L is computable from the output of any backtest run.

**Complexity.** Low

---

### T-P2-06 · SimulatedVenue — Slippage and Fill Model

**Description.** Implement slippage and fill logic: (1) fills execute at **next bar's open + slippage**, never the current bar's close; (2) slippage is `f(spread, order_size / bar_volume, volatility)` — configurable model, pessimistic defaults; (3) partial fills: order quantity vs. available volume fraction; (4) simulated rejections: min-notional check, precision check, insufficient-margin check. Fill timestamp is `next_bar.open_time + simulated_latency` sampled from config.

**Dependencies.** T-P2-05.

**Acceptance Criteria.**
- An order submitted on bar `t` never fills at bar `t`'s close price — fills are always at bar `t+1` or later.
- With zero slippage configured, fill price equals next bar's open exactly.
- A fractional fill (order size > bar volume) fills only the available fraction.
- A buy-and-hold backtest computed manually against the Parquet data matches the engine's output to the cent.

**Complexity.** High

---

### T-P2-07 · Strategy Port and Registry

**Description.** Define the `Strategy` ABC in `domain/strategy/`: `subscriptions()`, `warmup_period()`, `params_schema()`, `on_start(context)`, `on_data(event, view)`, `on_fill(fill)`, `on_stop()`, `state()`, `restore(state)`. Implement `StrategyRegistry`: discovers strategy classes from configured modules, validates parameter schema against provided params, instantiates, and returns. No network, no database, no wall clock in any strategy method — enforced by the port contract and lint.

**Dependencies.** T-P0-07, T-P2-02.

**Acceptance Criteria.**
- Registering a strategy class and instantiating it with valid params succeeds.
- Instantiating with a param that fails schema validation raises `InvalidStrategyParams`.
- A strategy that calls `datetime.now()` in `on_data` is flagged by the custom lint rule.
- `state()` / `restore(state)` round-trip: a strategy stopped mid-run and restored produces the same next signal as a strategy run continuously.

**Complexity.** Medium

---

### T-P2-08 · Indicator Library — Vectorized Variants

**Description.** Implement vectorized (Polars/NumPy) indicators in `infrastructure/indicators/vectorized/`: SMA, EMA, RSI (14-period), ATR, Bollinger Bands, MACD, rolling volatility (annualized). All accept a `pl.Series` or `np.ndarray` and return the same type. All handle the `NaN` prefix correctly (first `n-1` values are NaN for an n-period indicator). No float in price inputs — accept Decimal arrays converted to float only at the indicator boundary.

**Dependencies.** T-P0-01.

**Acceptance Criteria.**
- SMA(20) of [1..100] matches a hand-computed reference value at index 20 and 100.
- The first `n-1` values of an n-period indicator are `NaN`.
- EMA with `span=10` matches `pandas.ewm(span=10, adjust=False).mean()` on identical input.
- All indicators process 1M rows in < 1 second (vectorized performance requirement).

**Complexity.** Low

---

### T-P2-09 · Indicator Library — Incremental (Streaming) Variants

**Description.** Implement streaming/incremental versions of every indicator from T-P2-08 in `infrastructure/indicators/incremental/`. Each maintains internal state and exposes `update(bar: Candle) -> Decimal | None`. Returns `None` during warmup. Must produce numerically equivalent results to the vectorized variant given the same bar sequence.

**Dependencies.** T-P2-08.

**Acceptance Criteria.**
- An incremental SMA(20) and vectorized SMA(20) fed the same 100 bars produce identical values at every step.
- An incremental EMA produces the same values as the vectorized variant at every bar after warmup.
- Calling `update()` one bar at a time on 1M bars completes in < 30 seconds (single-threaded performance).
- Returns `None` for the first `n-1` bars, then a `Decimal` thereafter.

**Complexity.** Medium

---

### T-P2-10 · Vectorized vs. Incremental Equivalence Test Harness

**Description.** Implement a property-based test (Hypothesis) that generates random price sequences and asserts that the vectorized and incremental variants of every indicator produce identical output. Run this test on every build. A divergence anywhere means a bug that will produce different backtest results from live signal generation.

**Dependencies.** T-P2-08, T-P2-09.

**Acceptance Criteria.**
- The Hypothesis test runs for all indicator types (SMA, EMA, RSI, ATR, Bollinger, MACD, vol) and passes with 100 examples each.
- Intentionally introducing a one-bar off-by-one in the incremental EMA causes the test to fail and identify which bar diverged.
- The test completes in < 10 seconds in CI.

**Complexity.** Low

---

### T-P2-11 · Backtest Metrics and Tearsheet

**Description.** Implement the metrics computation module in `application/backtest/metrics.py`: Total return, CAGR, max drawdown, drawdown duration, Sharpe ratio, Sortino ratio, Calmar ratio, Omega ratio, win rate, profit factor, avg win/loss, expectancy, time in market, total fees, slippage, fees-as-%-of-gross-P&L. Produce a tearsheet as a structured dict that serializes to JSON.

**Dependencies.** T-P2-06, T-P0-06.

**Acceptance Criteria.**
- A buy-and-hold strategy on BTC for 1 year produces metrics matching hand-computed references (Sharpe, CAGR, max DD) within 0.01%.
- A strategy with all losing trades produces a profit factor < 1 and negative Sharpe.
- Fees-as-%-of-gross-P&L is always computable (no division-by-zero guard returning 0 when gross P&L = 0 — return `None` instead).
- Tearsheet serializes to JSON without errors.

**Complexity.** Medium

---

### T-P2-12 · Backtest Run Registry

**Description.** Implement auto-logging of every backtest run to the `backtest_run` table: git SHA (from `git rev-parse HEAD`), strategy code hash (SHA-256 of the strategy class source), full params, `dataset_version_id`, random seed, start timestamp, all metrics (JSON), and the operator (username). Never deleted. Implement as a decorator/context manager so no run can escape logging. Store in `backtest_metrics` as a child record.

**Dependencies.** T-P0-11, T-P2-11.

**Acceptance Criteria.**
- Running any backtest without the registry decorator causes a `BacktestRegistryRequired` error.
- Two runs of the same strategy with different params produce two distinct registry rows with different hashes.
- Querying `SELECT COUNT(*) FROM backtest_run WHERE strategy_id = $1` returns the correct trial count for DSR computation.
- The registry is append-only: attempting to `DELETE` or `UPDATE` a row raises an RLS or trigger error.

**Complexity.** Medium

---

### T-P2-13 · Determinism Harness (Seed, Hash, Reproducibility)

**Description.** Implement a determinism test that runs the same strategy on the same `DatasetVersion` twice with the same seed and asserts bit-identical output: same sequence of signals, same fills, same final metrics. Also test cross-machine reproducibility by storing a golden-file output and comparing on every CI run. A divergence means a non-deterministic path exists (e.g., `datetime.now()`, non-seeded random, dict ordering).

**Dependencies.** T-P2-12, T-P2-04.

**Acceptance Criteria.**
- Two local runs of the reference strategy produce bit-identical tearsheets.
- The golden-file test passes in CI (i.e., the CI environment produces the same output as the local golden file).
- Introducing a `random.random()` call in the strategy breaks the determinism test.
- Changing the `DatasetVersion` used breaks the golden-file test.

**Complexity.** Medium

---

## Phase 3 — Strategy and Research Loop
**Goal:** Close the research loop. Find out whether there is an actual edge.
**Estimate:** 4 weeks. **Depends on:** Phase 2 complete.

---

### T-P3-01 · Reference Strategy 1 — EMA Crossover

**Description.** Implement a simple EMA crossover strategy (fast/slow EMA, go long on crossover, go flat on crossunder) as a concrete `Strategy` implementation. This is a **reference implementation** used for testing the engine, not for trading. Must be fully parameterized (fast_period, slow_period), must use only `MarketDataView` and incremental indicators, must not call `datetime.now()`.

**Dependencies.** T-P2-07, T-P2-09.

**Acceptance Criteria.**
- Strategy passes the engine's strategy validation (params schema, warmup declared, no forbidden imports).
- On synthetic data with a known crossover pattern, the strategy emits a `LONG` signal on the crossover bar and a flat signal on the crossunder bar.
- `state()` / `restore(state)` round-trip preserves indicator state.
- Backtest of the reference strategy on 2 years of BTC/USDT data runs without errors.

**Complexity.** Low

---

### T-P3-02 · Reference Strategy 2 — RSI Mean-Reversion

**Description.** Implement an RSI-based mean-reversion strategy (long on oversold, exit on overbought) as a second reference implementation. Fully parameterized (rsi_period, oversold_threshold, overbought_threshold). Used for multi-strategy testing in later phases.

**Dependencies.** T-P2-07, T-P2-09.

**Acceptance Criteria.**
- Emits `LONG` signal when RSI < oversold_threshold on synthetic data where RSI is known to drop below threshold.
- No signal during warmup period.
- Produces non-zero but reasonable fees in tearsheet (strategy actually trades).

**Complexity.** Low

---

### T-P3-03 · Parameter Sweep / Grid Search Runner

**Description.** Implement a grid search runner in `application/research/`: given a strategy class and a parameter grid (list of param dicts), run a backtest for each combination, log each to the run registry, and collect all tearsheets. Parallelize using `concurrent.futures.ProcessPoolExecutor` (not threads — each run is CPU-bound). Results are returned as a sorted list by a configurable metric.

**Dependencies.** T-P2-12.

**Acceptance Criteria.**
- A sweep of 10 parameter combinations produces 10 distinct `backtest_run` rows.
- Results are sortable by Sharpe, CAGR, or max drawdown.
- A sweep with 0 valid parameter combinations raises an informative error.
- The trial count in the registry equals the number of combinations run (not just the winners).

**Complexity.** Medium

---

### T-P3-04 · Walk-Forward Analysis

**Description.** Implement walk-forward analysis: given a strategy, dataset, in-sample (IS) window size, out-of-sample (OOS) window size, and optional step size, run rolling optimization on IS windows and test on subsequent OOS windows. Aggregate and report only the concatenated OOS performance. Warn (and optionally abort) if OOS data is examined and used for further parameter selection.

**Dependencies.** T-P3-03.

**Acceptance Criteria.**
- For a 2-year dataset, IS=6mo, OOS=3mo, step=3mo, produces exactly 2 OOS windows.
- The reported metrics cover only the OOS periods (not IS or the entire dataset).
- A strategy with positive IS Sharpe but negative OOS Sharpe is reported as failing the OOS gate.
- Walk-forward results include IS vs. OOS Sharpe ratio for each window (overfitting indicator).

**Complexity.** High

---

### T-P3-05 · Monte Carlo — Trade Resampling

**Description.** Implement bootstrap trade resampling: given a backtest's trade list, resample with replacement N times (N=1000 default), compute an equity curve for each resampled sequence, and report the distribution of max drawdown and final equity. Report the median and 95th percentile drawdown. This answers: "Was the historical equity curve just lucky ordering of the trades?"

**Dependencies.** T-P2-11.

**Acceptance Criteria.**
- With 1000 resamples, the reported median drawdown is close to but typically worse than the historical drawdown.
- A strategy with all winning trades produces zero drawdown in every resample.
- A strategy with alternating wins/losses produces a distribution, not a single value.
- Output serializes to JSON and is stored alongside the backtest run in the registry.

**Complexity.** Medium

---

### T-P3-06 · Monte Carlo — Parameter Perturbation (Plateau Test)

**Description.** Implement parameter perturbation: given an optimized parameter set, run the strategy on a grid of ±10% variations around each parameter independently. Report a "plateau score" (Sharpe remains within 20% of optimum across all perturbations = plateau = stable) vs. "peak score" (Sharpe collapses when a parameter shifts by 10% = overfitted). This is the single best overfitting detector available at near-zero cost.

**Dependencies.** T-P3-03.

**Acceptance Criteria.**
- A strategy with stable performance across ±10% param variation is labeled `PLATEAU`.
- A strategy where Sharpe drops > 20% when the fast EMA period changes from 10 to 11 is labeled `PEAK`.
- The output includes a sensitivity heatmap data structure (param → Sharpe at each perturbation).
- Results are stored in `backtest_metrics` alongside the original run.

**Complexity.** Medium

---

### T-P3-07 · Monte Carlo — Path Simulation

**Description.** Implement synthetic path simulation: given the statistical properties of the historical return series (mean, variance, skewness, kurtosis, autocorrelation), generate N synthetic price paths that match those properties. Run the strategy on each synthetic path and report the distribution of outcomes. Answers: "Does this strategy work on data that shares the market's statistics but not its specific history?"

**Dependencies.** T-P2-11.

**Acceptance Criteria.**
- Generated paths match the input series' mean and variance within 5% (verified by test).
- A strategy that overfits the specific historical path performs significantly worse on synthetic paths.
- 500 paths run in < 5 minutes on a modern laptop.
- Distribution of Sharpe ratios across paths is reported with mean, std, and 5th/95th percentile.

**Complexity.** High

---

### T-P3-08 · Deflated Sharpe Ratio and PBO Calculation

**Description.** Implement the Deflated Sharpe Ratio (DSR; Bailey & López de Prado 2014) in `application/research/stats.py`. DSR adjusts the Sharpe ratio for the number of trials, skewness, and kurtosis. Also implement Probability of Backtest Overfitting (PBO) using the CSCV framework. Both calculations require the `backtest_run` registry to supply trial count honestly — the registry makes this possible.

**Dependencies.** T-P2-12.

**Acceptance Criteria.**
- DSR for a single trial equals the standard Probabilistic Sharpe Ratio.
- DSR decreases as the number of trials increases (holding other parameters constant).
- PBO returns a value in [0, 1] where 1.0 means certain overfitting and 0.0 means no overfitting.
- A known DSR example from the Bailey & López de Prado paper is reproduced within 0.001.

**Complexity.** High

---

### T-P3-09 · Research Notebook SDK

**Description.** Implement a thin Python SDK in `research/sdk.py` exposing: `load_dataset(version_id)`, `run_backtest(strategy_class, params, dataset)`, `walk_forward(...)`, `monte_carlo(...)`, `plot_tearsheet(run_id)`. The SDK wraps the application layer and is designed for Jupyter use. It must use the same code paths as the production engine — no separate "research mode" implementations.

**Dependencies.** T-P3-04, T-P3-05, T-P3-06, T-P3-07, T-P3-08.

**Acceptance Criteria.**
- A notebook using only the SDK API can run a full walk-forward backtest in < 20 lines of code.
- `run_backtest()` through the SDK produces an identical result to running the same backtest via the CLI.
- `plot_tearsheet()` renders inline in Jupyter without errors.
- The SDK has zero non-research imports (no infrastructure, no database connections from notebook).

**Complexity.** Low

---

### T-P3-10 · Phase 3 Validation Gate Checker

**Description.** Implement a CLI command `quanttrade validate-strategy --run-id <id>` that reads a `backtest_run` record and its `backtest_metrics`, then applies the Phase 3 gate criteria: OOS walk-forward Sharpe > 1.0, DSR survives the trial count, parameter plateau (not peak), fees < 40% of gross P&L. Outputs PASS/FAIL with the specific criterion that failed. This is the gate that must be cleared before Phase 4 capital is deployed.

**Dependencies.** T-P3-04, T-P3-08, T-P3-06.

**Acceptance Criteria.**
- A run with OOS Sharpe = 0.8 produces FAIL with reason "OOS Sharpe < 1.0".
- A run with fees = 60% of gross P&L produces FAIL with reason "fees exceed 40% of gross P&L".
- A run passing all criteria produces PASS with the metrics displayed.
- The validator reads only from the registry — it does not re-run the backtest.

**Complexity.** Low

---

## Phase 4 — Risk Engine and OMS
**Goal:** The machinery that prevents losing money to bugs.
**Estimate:** 5 weeks. **Parallelizable with Phases 2–3.**
**Depends on:** Phase 0 only (not Phase 2 or 3).

---

### T-P4-01 · Risk Engine Skeleton (Port, Chain Runner, Fail-Closed)

**Description.** Implement the `RiskEngine` in `application/risk/`: a synchronous, blocking check-chain runner. Each check is a `RiskRule` with a `check(intent, context) -> RiskDecision` interface. The chain runs rules in order and short-circuits on the first rejection. If any rule raises an exception (including timeout), the result is `REJECTED` with reason `RISK_ENGINE_ERROR` — never `APPROVED`. `RiskEngineUnavailable` → reject. This is the fail-closed contract.

**Dependencies.** T-P0-07.

**Acceptance Criteria.**
- An empty rule chain returns `APPROVED`.
- A chain where rule 3 raises an exception returns `REJECTED(reason=RISK_ENGINE_ERROR)`.
- A chain where rule 2 rejects does not call rule 3 (short-circuit).
- Calling the risk engine while it is marked unavailable returns `REJECTED` immediately without calling any rules.
- Zero async code — the risk engine is synchronous by design.

**Complexity.** Medium

---

### T-P4-02 · Risk Checks 1–4 (Kill Switch, System Halt, Strategy Budget, Fat-Finger)

**Description.** Implement the first four rules in the check chain:
1. Kill switch engaged? → REJECT
2. System halted (daily loss / drawdown / drift / venue down)? → REJECT
3. Strategy active and within its budget (allocated_capital, daily_loss_limit, drawdown_limit)? → REJECT if not
4. Fat-finger checks: notional > max_order_notional, qty > max_order_qty, price outside collar (± collar_pct of last price)? → REJECT if any

**Dependencies.** T-P4-01, T-P0-11.

**Acceptance Criteria.**
- Each check is tested independently with a minimal intent that triggers only that check.
- Fat-finger: an order 6% above last price with a 5% collar is rejected; 4% above is not.
- Strategy daily loss over limit rejects but does not halt the whole system (only strategy-level halt).
- Kill switch check is the first rule and short-circuits all others when engaged.

**Complexity.** Medium

---

### T-P4-03 · Risk Checks 5–8 (Rate Limit, Instrument Limits, Exposure, Correlation)

**Description.** Implement checks 5–8:
5. Order rate ≤ configured rate limit (orders/min per strategy)
6. Instrument limits: max position per instrument (notional + units), instrument tradeable, not halted
7. Post-trade gross exposure and net exposure within portfolio limits
8. Correlation cluster concentration ≤ limit (stub: cluster membership from config; full implementation deferred to Phase 8)

**Dependencies.** T-P4-01.

**Acceptance Criteria.**
- Rate limit: submitting 11 orders in 1 minute with a 10/min limit rejects the 11th.
- Instrument limit: an order that would push instrument position above 10% NAV is rejected.
- Exposure check uses post-trade position (not current), so it catches an order that would breach.
- Check 8 stub: if instrument cluster is "crypto_large_cap" and cluster concentration would exceed 30% NAV, reject.

**Complexity.** Medium

---

### T-P4-04 · Risk Checks 9–11 + APPROVE Path

**Description.** Implement checks 9–11 and the APPROVE terminal:
9. Liquidity: order size vs. ADV ratio > configured max (stub: use 24h volume from candle; full L2 deferred)
10. Margin sufficient post-trade (margin_used + new_order_margin < margin_available * 0.5)
11. Order within strategy `capacity_usd` limit

APPROVE: persist `RiskDecision` with `approved=True`, all rules evaluated, timestamps.

**Dependencies.** T-P4-03.

**Acceptance Criteria.**
- An order consuming > configured ADV fraction is rejected with reason `LIQUIDITY_LIMIT`.
- An order that would push margin utilization > 50% is rejected with reason `MARGIN_LIMIT`.
- An order from a strategy already at its capacity_usd is rejected.
- A passing intent produces a persisted `RiskDecision` row with `approved=True` and all rule names recorded.

**Complexity.** Medium

---

### T-P4-05 · Risk Limits Config-as-Code Loader

**Description.** Implement config loading from a versioned TOML or YAML file (`config/risk_limits.toml`). The loader validates the config at startup (type checks, range checks, required fields). The `limits_config_version` (hash of the file) is recorded on every `RiskDecision` row for auditability. Changing a limit requires changing the file and going through git review — there is no database table for limits (see ARCHITECTURE.md §10.1 rule 4 and §7 permanent exclusions).

**Dependencies.** T-P4-01, T-P0-11.

**Acceptance Criteria.**
- Loading a config with a missing required field raises `RiskConfigValidationError` at startup, not at trade time.
- The `limits_config_version` field in every `RiskDecision` matches the hash of the loaded config file.
- Modifying the config file changes the hash and all subsequent decisions record the new hash.
- The config loader has no database dependency — it reads only from the filesystem.

**Complexity.** Low

---

### T-P4-06 · RiskDecision Persistence (Both Approvals and Rejections)

**Description.** Persist every `RiskDecision` to the `risk_decision` table: intent_id, ts, approved, rules_evaluated (JSON list), rejection_reason, limits_config_version. Both approvals and rejections are stored. A high rejection rate is a leading indicator of a misconfigured strategy. Implement a query `rejection_rate(strategy_id, window_minutes)` that exposes this metric.

**Dependencies.** T-P4-05, T-P0-11.

**Acceptance Criteria.**
- After 100 risk evaluations (50 approved, 50 rejected), the `risk_decision` table has 100 rows.
- `rejection_rate("s1", 60)` returns 0.5 for the above scenario.
- A `rules_evaluated` field lists all rule names that ran before rejection (not just the rejecting rule's name).
- Integration test verifies persistence using the test Postgres instance.

**Complexity.** Low

---

### T-P4-07 · Order State Machine (Complete, Including UNKNOWN State)

**Description.** Wire the `Order` state machine from T-P0-05 into the application layer with persistence. Implement `OrderRepository` with `SQLAlchemy 2.0 Core`. Every state transition persists an `OrderEvent` row before updating the `order` row (event-first, then projection). The `UNKNOWN` state is first-class: when HTTP times out during submission, the order enters `UNKNOWN` — not retried blindly. Implement `resolve_unknown(order_id)`: query venue by `client_order_id`, then transition to `ACKED` or `REJECTED`.

**Dependencies.** T-P0-05, T-P0-11.

**Acceptance Criteria.**
- An HTTP timeout on order submission transitions the order to `UNKNOWN`, not `REJECTED`.
- `resolve_unknown()` that finds the order at the venue transitions it to `ACKED`.
- `resolve_unknown()` that confirms the order was not accepted transitions it to `REJECTED`.
- Rebuilding an `Order` from its `OrderEvent` sequence produces the same state as the `order` table row.
- Chaos test: simulating a process crash between `OrderEvent` write and `order` row update; on recovery, the order is in `UNKNOWN` state (not lost).

**Complexity.** High

---

### T-P4-08 · Deterministic Client Order ID Generator

**Description.** Implement `generate_client_order_id(strategy_id, symbol, intent_seq, side) -> str` producing a deterministic, reproducible ID. The algorithm: `SHA-256(strategy_id + "|" + symbol + "|" + str(intent_seq) + "|" + side)[:32]`. Store as `client_order_id` on the `Order` with a UNIQUE(venue_id, client_order_id) constraint. On re-submission after a crash, the same input produces the same ID, and the venue returns the existing order (not a duplicate).

**Dependencies.** T-P4-07.

**Acceptance Criteria.**
- The same inputs always produce the same ID across processes and machines.
- Inserting two orders with the same `(venue_id, client_order_id)` raises a `UniqueViolation` from Postgres.
- The ID fits within Binance's 36-character `newClientOrderId` limit.
- A crash-recovery test: generate an intent, crash, regenerate the same intent, confirm the ID is identical.

**Complexity.** Low

---

### T-P4-09 · Fill Deduplication in OMS

**Description.** Implement fill processing with deduplication on `(venue_id, venue_fill_id)`. The DB unique constraint (from T-P0-11) is the primary guard. At the application layer: before applying a fill, attempt insert; catch `UniqueViolation`; log the duplicate and discard it without error. Never raise an exception for a duplicate fill — it is expected and safe. The reconciler can also arrive with fills (belt and suspenders).

**Dependencies.** T-P4-07, T-P0-11.

**Acceptance Criteria.**
- Receiving the same fill twice applies the position change exactly once.
- Receiving the same fill 100 times applies it exactly once (idempotent).
- A `UniqueViolation` from the DB is logged as `INFO`, not `ERROR` (it is expected behavior).
- Property test: applying any fill N times produces the same `Position` as applying it once.

**Complexity.** Low

---

### T-P4-10 · OrderIntent → Order Pipeline

**Description.** Implement the pipeline in `application/trading/`: `ApprovedIntent → generate_client_order_id → round to tick/lot size → re-validate min notional (rounding can push below) → persist OrderCreated event → publish OrderCommand to EventBus`. The persist-before-publish order is critical: if the process dies between the two, recovery replays the log, finds the unacked order, and queries the venue by client_order_id.

**Dependencies.** T-P4-08, T-P4-07, T-P0-07.

**Acceptance Criteria.**
- Rounding a 0.0009 BTC order down to 0.0 (below min notional) is rejected with `BELOW_MIN_NOTIONAL` before any order is created.
- Rounding a valid order to tick/lot precision does not change the risk-approved notional by more than one tick.
- An `OrderCreated` event is persisted before the `OrderCommand` is published.
- Simulating a crash after persist but before publish: on recovery, the unacked order is found and the pipeline re-publishes the command.

**Complexity.** High

---

### T-P4-11 · UNKNOWN State Resolution (Query, Bounded Retry, Escalate)

**Description.** Implement the `UnknownOrderResolver` background task: polls for orders in `UNKNOWN` state, queries the venue by `client_order_id`, resolves to `ACKED` or `REJECTED` based on venue response. If the venue itself is unavailable, retry with exponential backoff up to a configurable max. After max retries without resolution, transition to `ESCALATED` and page a human. Never blind-retry a submission.

**Dependencies.** T-P4-08, T-P4-07.

**Acceptance Criteria.**
- An order stuck in `UNKNOWN` is resolved within 30 seconds when the venue is available.
- After 5 failed resolution attempts (venue down), the order transitions to `ESCALATED` and a `SystemHaltEvent` is triggered.
- A resolved order that was `ACKED` at the venue is adopted with the correct `venue_order_id`.
- A resolved order that was not found at the venue after its max TTL transitions to `REJECTED`.

**Complexity.** High

---

### T-P4-12 · Double-Entry Ledger

**Description.** Implement ledger entry creation in `application/accounting/`: on every fill, write two `LedgerEntry` rows (debit and credit) representing the position increase and cash decrease (or vice versa), plus one more pair for fees. The `debit_amount == credit_amount` invariant must hold for every fill. Implement a `ledger_check()` query: `SUM(debits) - SUM(credits) == 0` over all entries — this is a continuously checkable invariant.

**Dependencies.** T-P4-09, T-P0-11.

**Acceptance Criteria.**
- After a buy fill: cash is debited, position is credited, fee is debited to expense and credited to exchange.
- `ledger_check()` returns 0 after any number of fills.
- Property test: applying 1000 random fills, `ledger_check()` always returns 0.
- Integration test: `SELECT SUM(amount) FROM ledger_entry WHERE debit_acct = credit_acct` is 0.

**Complexity.** Medium

---

### T-P4-13 · Position Tracker with Decimal Arithmetic

**Description.** Implement the live `PositionTracker` in `application/portfolio/`: maintains current positions in memory, applies fills using the same `Position.apply_fill` logic from T-P0-06, and persists `Position` rows to the database as a materialized projection. The derivation from fills must be verifiable: `Position.qty == Σ(signed fill quantities)` is continuously asserted in background. No float in any position arithmetic path.

**Dependencies.** T-P0-06, T-P4-09, T-P0-11.

**Acceptance Criteria.**
- After 10,000 fills, `position.qty` matches `Σ(fill.qty * sign)` to the last decimal place.
- No float in the computation path (enforced by the lint rule from T-P0-03).
- A position that reaches exactly zero is stored as `Decimal("0")`, not a float approximation.
- Integration test: apply 1000 fills, verify the DB row matches the in-memory state.

**Complexity.** Medium

---

### T-P4-14 · Mark-to-Market and Equity Curve

**Description.** Implement mark-to-market valuation in `application/portfolio/`: `unrealized_pnl = (current_mark_price - avg_entry) * qty`. Persist `EquitySnapshot` every minute (as a TimescaleDB hypertable) with `cash + positions_value = total_equity` and `drawdown = (peak_equity - current_equity) / peak_equity`. Feed the running drawdown back into the risk engine context so the drawdown limit check (Phase 4, check 2) has current data.

**Dependencies.** T-P4-13, T-P0-11.

**Acceptance Criteria.**
- A position in a rising market produces positive unrealized P&L that increases tick by tick.
- `drawdown` reaches 10% when equity falls from $100k to $90k and does not reset until equity exceeds the prior peak.
- The `EquitySnapshot` hypertable has a row every minute during active trading.
- Drawdown fed to the risk engine: when drawdown crosses 15%, subsequent orders are rejected via check 2.

**Complexity.** Medium

---

### T-P4-15 · Portfolio Sizing Models

**Description.** Implement two position sizing models in `application/portfolio/sizing.py`:
1. **Fixed fractional**: `size = account_equity * fraction / current_price`
2. **Volatility targeting**: `size = (target_vol / realized_vol) * account_equity / current_price`, capped at `max_notional`. `realized_vol` uses a 20-period rolling ATR / price. Both return `Decimal` quantities rounded to lot size.

**Dependencies.** T-P4-14, T-P2-09.

**Acceptance Criteria.**
- Volatility targeting produces a larger position when realized vol is low and smaller when vol is high (inverse relationship).
- Both models return a quantity that passes min-notional and lot-size constraints.
- A low-vol instrument with vol targeting does not exceed the `max_notional` cap.
- The hard cap prevents unbounded sizing into near-zero-vol instruments.

**Complexity.** Medium

---

### T-P4-16 · Reconciliation Service — Data Fetch

**Description.** Implement the `ReconciliationService` data-fetch layer in `infrastructure/reconciliation/`: poll venue REST endpoints (GET open orders, GET positions, GET balances, GET fills since last known fill ID) on a 60-second cadence. This service is **independent of the event bus** — it calls the venue directly. Results are stored in a `reconciliation_snapshot` structure for comparison.

**Dependencies.** T-P1-01.

**Acceptance Criteria.**
- The service fetches all four endpoint types and produces a snapshot within 10 seconds.
- If one endpoint fails (5xx), the snapshot is marked `PARTIAL` and reconciliation is skipped for that cycle.
- The cadence is configurable (default 60 seconds).
- The service runs in a separate async task and does not share state with the event bus or trading core.

**Complexity.** Medium

---

### T-P4-17 · Reconciliation Service — Diff and Halt on Drift

**Description.** Implement the comparison logic: diff `ReconciliationSnapshot` against internal `PositionTracker` and `OrderRepository` state. Classify: `MATCH`, `WITHIN_TOLERANCE`, `DRIFT`. On `DRIFT`: immediately trigger a `SystemHaltEvent`, increment `reconciliation_drift_total` metric, emit a P1 alert, and log the specific discrepancy. Never auto-correct — halt and alert. A human must resume after investigation.

**Dependencies.** T-P4-16, T-P4-13, T-P4-07.

**Acceptance Criteria.**
- Simulating a position difference of 0.001 BTC between internal and venue triggers `DRIFT` and a halt.
- Simulating a difference within tolerance (configurable, e.g., 0.0001 BTC) produces `WITHIN_TOLERANCE` with no halt.
- After a halt, all risk checks return `REJECTED` (system halted, check 2 from T-P4-02).
- A `SystemHaltEvent` is persisted to the `system_halt_event` table.

**Complexity.** High

---

### T-P4-18 · Kill Switch Process

**Description.** Implement the kill switch as a **completely separate Python process** in `kill_switch/main.py` (< 200 lines). It has its own credentials (separate Vault path), its own HTTP client (no shared library with trading core), and its own network path. Logic: authenticate → cancel all open orders (`cancel_all`) → flatten all positions at market (`close_all_positions`) → log to file (not to the app database) → exit 0. Idempotent: running it twice is safe.

**Dependencies.** T-P0-09.

**Acceptance Criteria.**
- The process has zero imports from any `domain/`, `application/`, or `infrastructure/` module.
- Total line count (excluding blank lines and comments) is < 200.
- A test against a mock venue confirms: (1) cancel_all is called, (2) close_all_positions is called, (3) both are called even if cancel_all fails (no exception propagation stopping the flatten).
- `SIGKILL`ing the trading-core process does not affect the kill switch (independent process verified by separate terminal).
- Running the kill switch twice produces the same outcome as running it once.

**Complexity.** High

---

### T-P4-19 · Kill Switch Triggering (Dashboard, CLI, and Phone Endpoint)

**Description.** Implement three trigger paths for the kill switch:
1. **CLI**: `quanttrade kill-switch --confirm` (requires explicit `--confirm` flag, no accident).
2. **Phone/API**: A minimal FastAPI endpoint `POST /kill` with a separate authentication token (not the dashboard token). Zero dashboard dependencies.
3. **Dashboard button**: WebSocket message to the kill switch process (wired in Phase 7 dashboard task; stub the endpoint here).
Each path must work independently of the trading core.

**Dependencies.** T-P4-18.

**Acceptance Criteria.**
- `quanttrade kill-switch` without `--confirm` prints a warning and exits 1.
- `quanttrade kill-switch --confirm` starts the kill switch process.
- `POST /kill` with wrong auth token returns 401.
- `POST /kill` with correct auth token starts the kill switch process and returns 200 immediately (fire-and-forget).
- The kill-switch API process runs on a different port from the main API.

**Complexity.** Medium

---

### T-P4-20 · Property-Based Tests — All Core Invariants

**Description.** Implement Hypothesis property tests covering all invariants from ARCHITECTURE.md §3.9:
1. `Σ(fill.qty) ≤ order.qty` always.
2. `position.qty == Σ(signed fill quantities)` always.
3. `cash_after_trade == cash_before − notional − fees` exactly (Decimal).
4. No order transitions out of a terminal state.
5. Applying the same fill twice produces the same state as once.
6. `Σ(debit amounts) == Σ(credit amounts)` for any sequence of fills.

**Dependencies.** T-P0-06, T-P4-07, T-P4-09, T-P4-12.

**Acceptance Criteria.**
- All 6 invariants are covered by Hypothesis tests with at least 100 examples each.
- Deliberately breaking invariant 2 (by adding 0.001 to a position after a fill) causes the test to fail.
- All property tests run in < 30 seconds in CI.
- Test output names each invariant explicitly so failures are diagnosable.

**Complexity.** Medium

---

## Phase 5 — Paper Trading
**Goal:** Run the full live path with real data and simulated fills. Find bugs that only appear in real time.
**Estimate:** 4 weeks. **Depends on:** Phases 2, 3, 4.

---

### T-P5-01 · Paper Venue Adapter

**Description.** Implement `PaperVenueAdapter` implementing `VenuePort` (T-P0-07). It consumes real market data from the live WS feed (T-P1-08) and simulates fills using the `SimulatedVenue` fill model (T-P2-06). Crucially: it returns fills that look like real venue fills, including realistic rejection rates (min-notional, precision), partial fills, and simulated latency. It is pessimistic by default — tuned to under-fill rather than over-fill.

**Dependencies.** T-P1-08, T-P2-06, T-P0-07.

**Acceptance Criteria.**
- `PaperVenueAdapter` passes the same interface tests as any `VenuePort` implementation.
- A market order submitted at 12:00:00 fills at 12:00:01 (next tick), not at 12:00:00.
- A simulated partial fill reduces the order's `filled_qty` correctly via the standard fill path.
- Running paper trading for 60 minutes produces a non-zero number of fills and a non-zero P&L.

**Complexity.** Medium

---

### T-P5-02 · Full Live Path Integration

**Description.** Wire all processes together for paper trading: market-data-gateway (WS feed) → event bus → trading-core (strategy → portfolio → risk → OMS) → paper venue adapter → fills back to OMS. This is the first end-to-end integration of all application modules. The event bus for Phase 5 is still in-process asyncio queues (NATS migration deferred to Phase 7).

**Dependencies.** T-P5-01, T-P4-10, T-P4-13, T-P3-01 (or T-P3-02).

**Acceptance Criteria.**
- Starting the system with a configured strategy and paper adapter: signals are generated, risk-checked, orders are created, and fills arrive — all within a single process.
- A signal-to-fill round trip is logged end-to-end with a correlation ID threading through all log lines.
- Stopping the system triggers graceful shutdown (next task).
- The system processes at least 1 bar per minute without errors over a 10-minute run.

**Complexity.** High

---

### T-P5-03 · Graceful Shutdown

**Description.** Implement graceful shutdown triggered by SIGTERM or SIGINT: (1) stop accepting new signals from strategies; (2) cancel all open paper orders; (3) wait for in-flight fills to settle (with timeout); (4) persist current position state; (5) flush all pending log entries; (6) exit 0. Positions must be persisted before exit. A SIGKILL (ungraceful) must be recoverable via event log replay.

**Dependencies.** T-P5-02.

**Acceptance Criteria.**
- Sending SIGTERM completes all 6 steps within 10 seconds and exits 0.
- After graceful shutdown, the DB position state matches what was in memory at shutdown time.
- After SIGKILL, replaying the event log recovers the pre-crash position within 1% error.
- A test simulates SIGKILL during an in-flight fill and confirms recovery adopts the fill correctly.

**Complexity.** High

---

### T-P5-04 · Crash Recovery Sequence

**Description.** Implement the crash recovery sequence in the startup path: (1) replay `OrderEvent` table from last known good checkpoint to rebuild in-memory order state; (2) query venue for all open orders and compare; (3) query venue for all positions and balances; (4) apply any fills that arrived during the downtime (`get_fills_since(last_fill_id)`); (5) reconcile against venue state; (6) only if reconciliation is clean, resume trading; otherwise, halt and page.

**Dependencies.** T-P4-07, T-P4-16, T-P4-17.

**Acceptance Criteria.**
- Simulating a crash with 3 open orders and 1 fill that arrived during downtime: recovery applies the fill and correctly updates position before resuming.
- If recovery finds drift (internal state ≠ venue state after fill application), the system halts and does not resume automatically.
- Recovery is tested with: (a) no crash, (b) crash with no in-flight orders, (c) crash with in-flight orders, (d) crash with fills during downtime.
- Recovery completes in < 30 seconds under normal conditions.

**Complexity.** High

---

### T-P5-05 · 30-Day Soak Test Harness

**Description.** Implement a soak test runner in `tests/soak/`: runs the full paper trading system for a configurable duration, monitors for unhandled exceptions (all exceptions must be caught and classified — none propagate to the top level), checks for state corruption every hour (position = Σfills), verifies reconciliation succeeds on every cycle, and produces a final report: exception count, reconciliation drift events, uptime percentage.

**Dependencies.** T-P5-02, T-P5-03, T-P5-04.

**Acceptance Criteria.**
- A 24-hour soak run with the reference strategy produces: 0 unhandled exceptions, 0 state corruption events, 0 reconciliation drift events.
- If an exception escapes the catch handler, the soak test fails immediately.
- The soak report includes: total bars processed, total signals generated, total orders submitted, total fills received, reconciliation success rate.
- The pass criteria are: 30 consecutive days, zero unhandled exceptions, zero drift events.

**Complexity.** High

---

### T-P5-06 · Live vs. Backtest Divergence Report

**Description.** Implement a comparison job that runs the same strategy in backtest over the same period as the paper trading run, then compares: total P&L (paper vs. backtest), number of trades, fill prices (actual vs. model), slippage (actual vs. model). Produces a divergence report. If paper P&L differs from backtest prediction by > 20%, flag for investigation of the fill model.

**Dependencies.** T-P2-11, T-P4-13.

**Acceptance Criteria.**
- The divergence report computes metrics for both paper and backtest runs over identical date ranges.
- A deliberately misconfigured fee model (e.g., 0% fees in backtest vs. real fees in paper) produces a > 20% P&L divergence and is flagged.
- The report produces a JSON artifact storable alongside the backtest run registry entry.
- A < 20% divergence passes without flag.

**Complexity.** Medium

---

### T-P5-07 · Alerting Wired to Phone (PagerDuty Integration)

**Description.** Implement the alerting pipeline: Prometheus Alertmanager rules for all P1 alerts (position drift, kill switch fired, risk breach, auth failure, data feed down > 60s) → Alertmanager → PagerDuty integration. Every P1 alert must reach a phone. Implement a drill mode that sends a test alert through the full pipeline without triggering real trading actions.

**Dependencies.** T-P0-08, T-P0-10.

**Acceptance Criteria.**
- Simulating a data feed stale event (> 60 seconds) triggers a PagerDuty incident.
- Simulating a position drift event triggers a PagerDuty incident.
- The drill command fires a test alert and confirms receipt in PagerDuty without halting trading.
- All P1 alerts have response SLA = 5 minutes (configured in PagerDuty).
- P2 alerts (order rejection rate > 5%) go to chat/email, not phone.

**Complexity.** Medium

---

### T-P5-08 · Health Checks and Heartbeat Watchdog

**Description.** Implement health check endpoints for each process: `GET /health` returns `{"status": "ok", "checks": {"db": "ok", "redis": "ok", "venue_ws": "ok", ...}}`. Implement a watchdog that monitors all process health endpoints on a 10-second cadence. If any process fails its health check for > 30 seconds, emit a P2 alert. If the trading core or venue gateway fails for > 60 seconds, emit a P1 alert.

**Dependencies.** T-P0-08, T-P5-02.

**Acceptance Criteria.**
- Killing the Postgres connection makes the health check return `{"db": "error"}`.
- The watchdog detects a failed health check within 30 seconds.
- Health check endpoint responds in < 100ms.
- A healthy system's watchdog runs continuously without emitting any alerts.

**Complexity.** Low

---

### T-P5-09 · Operations Runbooks

**Description.** Write runbooks in `docs/runbooks/` for: (1) Reconnect procedure (WS disconnect, step-by-step), (2) Deploy procedure (positions flat, cancel-all, start, reconcile, resume), (3) Kill switch drill (monthly, with real positions, step-by-step), (4) Incident response (position drift, unhandled exception, venue outage), (5) New engineer setup (goal: run a backtest on day one). Each runbook tested by a team member who didn't write it.

**Dependencies.** T-P5-03, T-P5-04, T-P4-19.

**Acceptance Criteria.**
- A person following the deploy runbook without prior context can deploy successfully.
- A person following the kill switch drill runbook can execute a drill without guidance.
- Each runbook specifies the expected outcome at every step so deviations are obvious.
- The new-engineer runbook is timed: a new person completes a backtest in < 2 hours.

**Complexity.** Low

---

## Phase 6 — Live, Minimum Capital
**Goal:** Real money. As little as the exchange will allow.
**Estimate:** 3 weeks setup + 12 weeks validation. **Depends on:** Phase 5 complete.

---

### T-P6-01 · Live Venue Adapter (Binance, Production)

**Description.** Implement `BinanceVenueAdapter` implementing `VenuePort`: submit, cancel, cancel_all, get_open_orders, get_positions, get_balances, get_fills_since, subscribe_user_stream, capabilities. Handles all Binance-specific quirks: weight-based rate limits (server-side budget, not just client-side), newClientOrderId length constraint (≤ 36 chars), `-1021` timestamp error recovery, postOnly flag support, partial fill WS events. Uses production credentials from Vault.

**Dependencies.** T-P1-01, T-P0-07, T-P0-09.

**Acceptance Criteria.**
- Venue contract tests (recorded VCR fixtures) cover: success, 429, 418, 5xx, -1021, postOnly rejection, partial fill.
- `capabilities()` returns the correct frozenset for Binance Spot (no `post_only` → no, `reduce_only` → no).
- A `cancel_all()` call cancels all open orders in one atomic venue call (not one-by-one).
- All price and quantity fields in responses are parsed as `Decimal`, never `float`.

**Complexity.** High

---

### T-P6-02 · Rate-Limit Budget Manager

**Description.** Implement client-side rate-limit budget tracking: parse `X-MBX-USED-WEIGHT-1M` from every Binance response header, maintain a rolling 1-minute budget, back off submissions when remaining budget < configured threshold (e.g., 20% headroom), and block new order submissions during backoff. Emit a metric `venue_rate_limit_headroom_pct{venue}` continuously. Different weight costs for different endpoints (GET vs. POST orders).

**Dependencies.** T-P6-01.

**Acceptance Criteria.**
- When used-weight > 80% of limit, new order submissions are queued, not dropped or sent.
- After the 1-minute window resets, queued submissions are sent.
- A simulated 429 response halts all submissions for the configured retry-after period.
- Metric `venue_rate_limit_headroom_pct` stays > 0 during normal operation.

**Complexity.** Medium

---

### T-P6-03 · Error Classification (Retryable, Terminal, Unknown)

**Description.** Implement a `BinanceErrorClassifier` that maps Binance error codes and HTTP statuses to `RetryableError`, `TerminalError`, or `UnknownError`. Rules: 5xx → `Retryable`; 429 → `Retryable` (after wait); 418 → `TerminalError` (IP ban, halt everything); -1121 (invalid symbol) → `TerminalError`; -2010 (duplicate clientOrderId) → `TerminalError` (already exists, treat as acked); -1021 (timestamp) → `RetryableError` (resync clock and retry once). Unknown codes → `UnknownError` → transition order to `UNKNOWN` state.

**Dependencies.** T-P6-01, T-P4-07.

**Acceptance Criteria.**
- Every Binance error code from the API documentation is classified (no unclassified codes reach production).
- A 418 response triggers an immediate halt of all order submissions and a P1 alert.
- -2010 (duplicate order ID) does not retry — it queries by client_order_id and adopts the existing order.
- An unknown error code produces `UnknownError` and the order enters `UNKNOWN` state.

**Complexity.** Medium

---

### T-P6-04 · Production AWS Infrastructure (Terraform)

**Description.** Write Terraform in `infra/` for: EC2 instance (region: ap-northeast-1 for Binance, or us-east-1 for Coinbase), RDS PostgreSQL 16 with TimescaleDB extension, ElastiCache Redis, VPC with private subnets, security groups (DB accessible only from app instance), S3 bucket for Parquet archival, IAM roles (instance profile for secrets access). No hand-clicked resources. Everything is reproducible via `terraform apply`.

**Dependencies.** T-P0-10.

**Acceptance Criteria.**
- `terraform plan` produces a plan with no errors on a clean AWS account.
- `terraform apply` provisions all resources without manual steps.
- The application instance can connect to RDS and ElastiCache from within the VPC.
- The application instance cannot connect to RDS from outside the VPC (security group test).
- `terraform destroy` tears down all resources without leaving orphaned resources.

**Complexity.** Medium

---

### T-P6-05 · Vault / AWS KMS Secrets Integration (Production)

**Description.** Configure HashiCorp Vault (or AWS Secrets Manager as an alternative) with the production API keys. Implement the production `VaultSecretsClient` that fetches keys at startup from the Vault path, holds them in memory, and zeroizes on shutdown. No key ever touches a `.env` file, an environment variable in production, or a git commit. The application logs a startup warning if it detects a key in an environment variable.

**Dependencies.** T-P0-09, T-P6-04.

**Acceptance Criteria.**
- Starting the application without a Vault token fails with a clear error, not a cryptic `KeyError`.
- The Vault path for production keys requires an authenticated token — no anonymous access.
- Application startup log includes `secrets_loaded: true` and the Vault path (not the key value).
- `grep -r "API_KEY" .env` produces no results in production (no .env in production).

**Complexity.** Low

---

### T-P6-06 · Blue/Green Deploy Pipeline

**Description.** Implement a GitHub Actions deploy workflow: (1) build Docker image (multi-stage, distroless base, non-root user); (2) push to ECR with a content-hash tag (no `latest`); (3) on deploy: cancel all open orders → verify flat (or documented exception) → start new container → wait for health check → reconcile → if clean: route traffic to new; if not: rollback to previous image hash. Rollback is `docker pull previous-hash && docker run`.

**Dependencies.** T-P5-08, T-P6-04.

**Acceptance Criteria.**
- A deploy with open positions fails at step 3 (verify flat) and does not proceed.
- A deploy with flat positions completes the full sequence and the new container is serving.
- A failed health check after deploy triggers automatic rollback.
- Rollback is tested (not theoretical): force a health-check failure and confirm the previous version is restored.
- Image tag is the Docker content hash — no `latest` tag is ever pushed.

**Complexity.** High

---

### T-P6-07 · On-Call Rotation and Daily Reconciliation Report

**Description.** Set up PagerDuty on-call schedule covering 24/7 (even if it is one person). Implement a daily reconciliation report job: compares internal positions/balances with venue at EOD, computes daily P&L, exports to a summary email/slack message and a JSON file archived to S3. The report is the authoritative record for that trading day and must be retained permanently.

**Dependencies.** T-P4-16, T-P5-07.

**Acceptance Criteria.**
- The on-call schedule has no uncovered windows (someone is always on-call).
- The daily report runs at 00:05 UTC and produces a JSON file within 2 minutes.
- The report includes: date, final positions, daily P&L, fill count, reconciliation status, any alerts fired.
- Reports are stored in S3 with a `reports/daily/YYYY-MM-DD.json` key and never deleted.

**Complexity.** Low

---

## Phase 7 — Scale Out
**Goal:** Multi-strategy, second venue, process split, NATS, execution algos, TCA, full dashboard.
**Estimate:** 8 weeks. **Depends on:** Phase 6 validated (90 days live).

---

### T-P7-01 · Per-Strategy Capital Allocation and Budget Enforcement

**Description.** Implement strategy-level capital allocation in `StrategyInstance.allocated_capital`. Risk check 3 (T-P4-02) enforces that each strategy's outstanding exposure does not exceed its allocation. Implement a `StrategyBudgetManager` that tracks total notional deployed per strategy. Exposing this as a Prometheus gauge per strategy.

**Dependencies.** T-P4-02, T-P4-13.

**Acceptance Criteria.**
- Configuring two strategies with 30% and 20% of NAV: the 30% strategy cannot deploy > 30% at any time.
- Decreasing a strategy's allocation mid-run does not immediately close positions (only blocks new increases).
- Metric `strategy_exposure_pct{strategy_id}` reflects current vs. allocated in real time.

**Complexity.** Medium

---

### T-P7-02 · Signal Netting at Portfolio Level

**Description.** Implement netting in the `PortfolioManager`: when two strategies generate opposite signals for the same instrument, net the resulting `OrderIntent` quantities into a single order. Attribute P&L back to each strategy proportionally. Write the netting policy to config (net vs. trade-both); net is the default per ADR. This prevents fee doubling for offsetting signals.

**Dependencies.** T-P4-15, T-P7-01.

**Acceptance Criteria.**
- Strategy A signals LONG 1 BTC, Strategy B signals SHORT 1 BTC → net intent is 0 (no order submitted).
- Strategy A signals LONG 1 BTC, Strategy B signals SHORT 0.5 BTC → net intent is LONG 0.5 BTC.
- P&L attribution: each strategy is credited/debited based on its portion of the net fill.
- Setting netting policy to `TRADE_BOTH` in config disables netting and submits both orders.

**Complexity.** High

---

### T-P7-03 · Second Venue Adapter

**Description.** Implement a second `VenuePort` (Bybit or Coinbase). The adapter must pass the same contract test suite as the Binance adapter. This validates that the `VenuePort` abstraction is real and not Binance-specific. Implement per-venue rate-limit budget managers independently — one venue's rate limit must not affect the other's.

**Dependencies.** T-P6-01, T-P0-07.

**Acceptance Criteria.**
- The new adapter passes all `VenuePort` contract tests.
- The venue capabilities set differs from Binance's in at least one capability.
- A strategy routed to the new venue submits and fills correctly end-to-end in paper mode.
- The rate-limit manager for the new venue is completely independent of the Binance one.

**Complexity.** High

---

### T-P7-04 · Process Split — Market Data Gateway

**Description.** Extract the `market-data-gateway` into a standalone process that publishes to the event bus. All other processes consume from the bus rather than receiving data in-process. This requires the `EventBus` port (T-P0-07) to be implemented against NATS/JetStream (see T-P7-05). The gateway process has its own Docker container and health check.

**Dependencies.** T-P7-05, T-P1-08.

**Acceptance Criteria.**
- Killing the market-data-gateway process stops data delivery to the trading core but does not crash the trading core.
- Restarting the gateway resumes data delivery within 30 seconds without trading core restart.
- All market data events consumed by the trading core carry the same timestamps as when emitted by the gateway.

**Complexity.** High

---

### T-P7-05 · NATS + JetStream Event Bus

**Description.** Implement the `NatsEventBus` as the production `EventBus` port replacing asyncio queues. Configure JetStream streams: `MARKET_DATA` (lossy, drop-oldest, no persistence), `ORDERS` (durable, at-least-once, replayed on restart), `FILLS` (durable, at-least-once, replayed on restart). All consumers of order/fill events must be idempotent. Add NATS to docker-compose for local dev.

**Dependencies.** T-P0-07, T-P0-10.

**Acceptance Criteria.**
- Swapping `AsyncioEventBus` for `NatsEventBus` in the application wiring requires zero changes to business logic.
- A fill message published to the FILLS stream is re-delivered to the OMS on reconnect if not acked.
- A market data message published to MARKET_DATA is dropped, not buffered, when the consumer is slow.
- Integration test: publish 1000 order commands, simulate consumer restart mid-stream, confirm all 1000 are processed exactly once.

**Complexity.** High

---

### T-P7-06 · TWAP Execution Algorithm

**Description.** Implement TWAP (Time-Weighted Average Price) in `application/execution/algos/twap.py`: given a parent order (qty, instrument, duration_minutes), slice into N child orders submitted at equal time intervals. Uses the `VenuePort` to submit child orders. Manages child order lifecycle: if a child is partially filled, adjust remaining slices accordingly. Respects venue rate limits.

**Dependencies.** T-P4-07, T-P6-01.

**Acceptance Criteria.**
- A TWAP order for 10 BTC over 10 minutes with 10 slices submits 1 BTC every minute (±1 second).
- A partially filled child slice reduces the remaining quantity spread across future slices.
- Canceling the parent TWAP order cancels all pending child orders.
- The total filled quantity across all children never exceeds the parent order quantity.

**Complexity.** High

---

### T-P7-07 · VWAP Execution Algorithm

**Description.** Implement VWAP (Volume-Weighted Average Price) in `application/execution/algos/vwap.py`: given a parent order and a historical intraday volume profile (fraction of daily volume per time bucket), size each child order proportionally to the expected volume in that bucket. Uses the same child order management infrastructure as TWAP.

**Dependencies.** T-P7-06.

**Acceptance Criteria.**
- A VWAP order sizes child orders proportionally: a bucket with 20% of daily volume gets 20% of the parent quantity.
- If actual volume in a bucket is 0 (illiquid period), the slice is skipped and redistributed.
- Integration test against paper adapter: VWAP for 10 BTC over 60 minutes submits correct slice sizes.

**Complexity.** High

---

### T-P7-08 · Transaction Cost Analysis (TCA) Module

**Description.** Implement TCA in `application/analytics/tca.py`: for each completed order, compute `realized_slippage_bps = (avg_fill_price - arrival_price) / arrival_price * 10000 * side_sign`. Compute `fees_as_pct_gross_pnl` per strategy per period. Compare realized slippage vs. modeled slippage from the backtest fill model. Report the discrepancy. Alert if realized > 2× modeled.

**Dependencies.** T-P2-06, T-P4-09, T-P4-13.

**Acceptance Criteria.**
- TCA report for a completed order shows arrival price, average fill price, slippage in bps, and fees.
- Realized slippage consistently > 2× the backtest model triggers a P2 alert.
- Fees-as-%-of-gross-P&L is computed per strategy and per portfolio.
- TCA results are persisted and queryable by date range and strategy.

**Complexity.** Medium

---

### T-P7-09 · Dashboard — React App Skeleton

**Description.** Scaffold the React + TypeScript dashboard in `dashboard/`: Vite build, TanStack Query for data fetching, React Router for navigation, Lightweight Charts for price charts. Configure a proxy to the FastAPI read API. Keyboard navigation: `g o` → Overview, `g p` → Positions, etc. No real data connections yet — stub API responses.

**Dependencies.** None (frontend only).

**Acceptance Criteria.**
- `npm run dev` starts the dashboard in < 10 seconds.
- Keyboard shortcut `g o` navigates to the Overview page.
- All routes render without errors (404-free).
- TypeScript strict mode is enabled and `npm run typecheck` passes.

**Complexity.** Low

---

### T-P7-10 · Dashboard — Overview Page and Kill Switch

**Description.** Implement the Overview page: equity curve (Lightweight Charts), today's P&L (abs + %), current drawdown gauge, open positions count, system health indicator, and the Kill Switch button. The kill switch button requires a two-click confirmation gesture. On confirmation, it calls `POST /kill` on the kill switch API. The kill switch is always visible on every page (persistent header).

**Dependencies.** T-P7-09, T-P4-19.

**Acceptance Criteria.**
- Single-clicking the kill switch does not trigger it — a confirmation dialog appears.
- Confirming the dialog calls the kill switch API and displays a visual confirmation.
- The equity curve updates every second via WebSocket subscription to `EquitySnapshot` events.
- The drawdown gauge turns red when drawdown > 10%.
- The kill switch button is visible when the viewport is narrow (mobile / dark room scenario).

**Complexity.** Medium

---

### T-P7-11 · Dashboard — Positions, Orders, and Strategy Pages

**Description.** Implement three dashboard pages:
1. **Positions**: table with qty, entry price, mark price, unrealized P&L, exposure %, per-strategy breakdown. Updates every second.
2. **Orders**: open orders and fill blotter with rejection log and rejection reasons.
3. **Strategies**: per-strategy status (Running/Paused/Faulted), allocation, daily P&L, signals/hr, pause/resume controls. Pause/resume calls the FastAPI strategy control endpoint.

**Dependencies.** T-P7-10.

**Acceptance Criteria.**
- Positions page updates in real time (within 2 seconds of a fill arriving).
- Rejection log shows rejection reason text, not a numeric code.
- Pausing a strategy via the dashboard causes the strategy to stop generating signals within 5 seconds.
- Resuming a strategy re-enters the `Running` state.

**Complexity.** Medium

---

### T-P7-12 · Dashboard — Risk, System Health, and Backtest Pages

**Description.** Implement three remaining dashboard pages:
1. **Risk**: limit-utilization gauges per limit, exposure heatmap (instrument × strategy), breach history table.
2. **System**: process health, latency histograms, error rates, venue connectivity, time since last reconciliation.
3. **Research/Backtests**: run browser (list of backtest_runs), tearsheet viewer, walk-forward OOS chart, DSR + trial count display.

**Dependencies.** T-P7-11, T-P2-12, T-P3-08.

**Acceptance Criteria.**
- Risk page: all limits display their current utilization as a bar gauge (0–100%).
- System page: "time since last reconciliation" is always visible and updates in real time.
- Research page: clicking a backtest run ID displays the full tearsheet metrics.
- DSR and trial count are displayed together (not separately) to discourage cherry-picking.

**Complexity.** Medium

---

## Phase 8 — Institutional Maturity
**Goal:** Attribution, VaR, L2 microstructure, ML strategies, compliance, audit, DR.
**Estimate:** 12 weeks. **Depends on:** Phase 7 validated.

---

### T-P8-01 · Performance Attribution by Strategy and Factor

**Description.** Implement P&L attribution in `application/analytics/attribution.py`: decompose portfolio P&L into per-strategy contributions. For factor attribution (Phase 8 only): project strategy returns onto a factor model (market beta, momentum, value) and report residual (alpha) vs. explained (beta) components. Store attribution results as time series queryable by period.

**Dependencies.** T-P4-13, T-P7-01.

**Acceptance Criteria.**
- `Σ(strategy_pnl)` equals `portfolio_pnl` within `Decimal` precision.
- Factor attribution: a purely market-beta strategy shows high R² to the market factor.
- Attribution is computable for any historical date range, not just the current period.

**Complexity.** High

---

### T-P8-02 · Real-Time VaR and CVaR Engine

**Description.** Implement parametric and historical simulation VaR in `application/risk/var.py`. Historical VaR: sort daily returns, find the 5th percentile. CVaR (Expected Shortfall): average of returns below the VaR threshold. Run against the current book on a configurable cadence (default: every 5 minutes). Store results as time series. Display on the Risk dashboard page (T-P7-12).

**Dependencies.** T-P4-14, T-P8-01.

**Acceptance Criteria.**
- Historical VaR(95%) on a 252-day return series matches a hand-computed reference within 0.01%.
- CVaR > VaR always (by definition — test this invariant with Hypothesis).
- Real-time VaR runs against the current live book in < 1 second.
- VaR breach (current position VaR > risk limit) triggers a P2 alert.

**Complexity.** High

---

### T-P8-03 · Nightly Stress Scenario Replayer

**Description.** Implement a nightly job that takes the current portfolio book and simulates it through historical stress scenarios: 2020-03-12 (COVID crash), 2022-05 (LUNA collapse), 2022-11 (FTX collapse). For each scenario, compute the hypothetical portfolio P&L and maximum drawdown. Results are stored and displayed on the Risk page. Alerts if any scenario produces a hypothetical loss > the account drawdown limit.

**Dependencies.** T-P8-02, T-P4-14.

**Acceptance Criteria.**
- Three scenarios are implemented and produce non-trivial loss estimates for a long-only portfolio.
- Scenario results run in < 30 seconds per scenario.
- If a scenario produces a hypothetical loss > 15% NAV (the halt threshold), a P2 alert fires.
- Results are queryable by date: "what was the scenario result on 2026-01-15?"

**Complexity.** Medium

---

### T-P8-04 · L2 Order Book Data Capture

**Description.** Subscribe to Binance's order book depth WS stream (snapshot + incremental deltas). Maintain an in-memory L2 book (top 10 levels per side). Write snapshots to Parquet only (not Postgres — L2 volume is too high for hot-tier storage per §7.4). Compute derived features: mid-price, spread, depth imbalance. L2 data is optional in the backtest (only for microstructure strategies).

**Dependencies.** T-P1-07.

**Acceptance Criteria.**
- An in-memory L2 book stays consistent through 10,000 incremental updates (verified by checksum against periodic full snapshots).
- Parquet writes are batched (not one row per update) to stay within write budget.
- A deliberately invalid update (sequence gap) triggers a snapshot refresh, not a corrupted book.
- The spread is always bid ≤ ask; a crossed book triggers a `CROSSED_BOOK` alert.

**Complexity.** High

---

### T-P8-05 · ML Feature Store and Model Registry

**Description.** Implement a feature store in `infrastructure/ml/`: a pipeline that computes versioned feature sets (technical indicators, cross-sectional ranks, lagged returns) from the candle/tick data and stores them in Parquet with a content hash. Implement a model registry in `infrastructure/ml/registry.py`: stores trained models with version, git SHA, feature_set_id, training_date, metrics. Models are loaded by the strategy engine via the standard `Strategy` port.

**Dependencies.** T-P2-08, T-P1-11.

**Acceptance Criteria.**
- Feature set computation is deterministic: same input data + same hash → same features.
- A trained model registered in the registry is loadable by its ID in a subsequent session.
- An ML strategy using the registry passes all `Strategy` port contract tests (same interface as rule-based strategies).
- Deploying a new model version does not require a system restart (hot-load via registry).

**Complexity.** High

---

### T-P8-06 · Disaster Recovery and Failover Test

**Description.** Write and rehearse a DR runbook: (1) RDS automated backup is enabled with 7-day retention; (2) Test restore: restore to a point-in-time 1 hour ago, verify event log is intact, verify positions are recoverable; (3) Measure: RTO (time from failure detection to trading-capable) and RPO (maximum data loss). Target: RTO < 4 hours, RPO < 1 minute (WAL replication).

**Dependencies.** T-P6-04, T-P5-04.

**Acceptance Criteria.**
- A point-in-time restore of the production database completes successfully in a test environment.
- After restore, `alembic current` shows the correct migration head.
- Positions derived from event log replay match the restored database positions.
- The DR runbook is updated with the actual RTO and RPO measured during the drill.

**Complexity.** Medium

---

## Phase 9 — Multi-Asset Expansion
**Goal:** Broker adapters, trading calendars, corporate actions, regulatory compliance, tax lots.
**Estimate:** 12+ weeks. **Depends on:** Phase 8 validated.

---

### T-P9-01 · Broker Adapter (IBKR or Alpaca)

**Description.** Implement a broker `VenuePort` for Interactive Brokers (via `ib_insync`) or Alpaca (REST + WebSocket). This is the first non-crypto venue. Must pass all `VenuePort` contract tests. Key differences from crypto: market hours, order types (MOC, LOC, MOO), fractional shares, regulatory order constraints (PDT, Reg-T). The adapter declares these via `capabilities()`.

**Dependencies.** T-P0-07, T-P7-03.

**Acceptance Criteria.**
- Passes all `VenuePort` contract tests.
- `capabilities()` returns `{"market_hours": "us_equity", "supports_fractional": True, ...}`.
- Submitting a market order outside market hours returns `VenueRejectedError("OUTSIDE_MARKET_HOURS")`.
- The adapter is tested with recorded fixtures for all expected error responses.

**Complexity.** High

---

### T-P9-02 · Trading Calendar Service

**Description.** Implement a `TradingCalendarService` in `infrastructure/calendars/`: given a venue and an instrument, returns: is this timestamp a trading minute? What is the next open/close? Are there halts in effect? Use `exchange_calendars` library for US equity calendars; implement a `CryptoCalendar` (always open, 24/7, no halts) as the default. The risk engine queries this before order submission.

**Dependencies.** T-P0-04.

**Acceptance Criteria.**
- `is_trading(us_equity_venue, datetime(2026, 7, 4, 15, 30))` returns False (US Independence Day).
- `is_trading(crypto_venue, datetime(2026, 7, 4, 15, 30))` returns True.
- `next_close(us_equity_venue, datetime(2026, 7, 7, 12, 0))` returns `datetime(2026, 7, 7, 20, 0)` (4 PM ET).
- An order submitted outside trading hours is rejected by the risk engine with `OUTSIDE_TRADING_HOURS`.

**Complexity.** Medium

---

### T-P9-03 · Corporate Actions Processor

**Description.** Implement corporate actions in `infrastructure/corporate_actions/`: fetch splits, dividends, and mergers from a data vendor API (or IBKR). Apply splits to historical candle data by creating a new `DatasetVersion` with adjusted prices (never mutating existing versions). Apply dividends as `LedgerEntry` credits to the account. Store all corporate actions in a `corporate_action` table for audit.

**Dependencies.** T-P1-11, T-P4-12.

**Acceptance Criteria.**
- A 2:1 split applied to historical data halves all prices and doubles all volumes in the new dataset version.
- The original dataset version is unchanged after the split adjustment (immutable).
- A dividend payment creates a `LedgerEntry` debit to the instrument position and credit to cash.
- `DatasetVersion` for the adjusted series has a different content hash from the unadjusted series.

**Complexity.** High

---

### T-P9-04 · Equity Asset-Class Variant and DatedFuture Variant

**Description.** Add `Equity` and `DatedFuture` to the sealed instrument variant hierarchy (T-P0-04). `Equity` adds: `exchange`, `isin`, `sector`, `corporate_action_port`. `DatedFuture` adds: `expiry_date`, `roll_days_before_expiry`, `contract_multiplier`, `underlying_instrument_id`. mypy's `assert_never` must flag every existing `match` block that does not handle the new variants.

**Dependencies.** T-P0-04, T-P9-03.

**Acceptance Criteria.**
- Adding `Equity` without handling it in every existing `match` block causes a mypy error.
- An `Equity` instrument has `isin`; `Spot` does not — accessing `.isin` on a `Spot` is a mypy error.
- A `DatedFuture` instrument carries `expiry_date`; roll logic is testable with a 5-days-before-expiry config.
- All existing tests for `Spot` and `PerpetualSwap` continue to pass.

**Complexity.** High

---

### T-P9-05 · Regulatory Rules — PDT, Reg-T, Wash Sales

**Description.** Implement US equity regulatory rules as risk checks added to the risk check chain (after check 11):
- **PDT**: track round-trip trades in a 5-day rolling window for accounts < $25k margin; reject a 4th round trip.
- **Reg-T**: enforce 50% initial margin requirement on US equity purchases.
- **Wash sale detection**: flag (not block) when a sell at a loss is followed by a repurchase within 30 days; log for tax accounting.

**Dependencies.** T-P4-01, T-P9-02.

**Acceptance Criteria.**
- A 4th round-trip trade in 5 days on a < $25k account is rejected with `PDT_VIOLATION`.
- Buying $10,000 of AAPL with $4,999 in cash is rejected with `REG_T_INSUFFICIENT_MARGIN`.
- Selling AAPL at a loss and rebuying within 30 days logs a `WASH_SALE_FLAG` event (not rejected, just flagged).
- None of these rules trigger for crypto instruments (calendar-based filter).

**Complexity.** High

---

### T-P9-06 · Tax Lot Tracking (FIFO/LIFO/Spec-ID)

**Description.** Implement tax lot tracking in `application/accounting/tax_lots.py`: each purchase creates a tax lot (date acquired, qty, cost basis). On sale, apply the configured method (FIFO, LIFO, or Spec-ID) to determine which lots are consumed and compute the taxable gain/loss. Store lot assignment in `LedgerEntry.tax_lot_id`. Short-term vs. long-term gain classification based on holding period.

**Dependencies.** T-P4-12.

**Acceptance Criteria.**
- FIFO: selling 5 units when lots are [3 @ $100, 4 @ $110] consumes the first lot fully and 2 from the second.
- LIFO: same lots, LIFO consumes 4 from the second lot and 1 from the first.
- A holding period > 365 days is classified as long-term; ≤ 365 days is short-term.
- Property test: `Σ(lots.qty)` always equals `position.qty` after any sequence of buys and sells.

**Complexity.** High

---

*End of task registry. Total tasks: 131 across 10 phases.*

---

## Appendix — Task Count by Phase

| Phase | Tasks | Est. Duration | Gate |
|---|---|---|---|
| P0 Foundations | 12 | 3 wk | — |
| P1 Data Engine | 12 | 4 wk | — |
| P2 Backtest Engine | 13 | 6 wk | — |
| P3 Research Loop | 10 | 4 wk | 🔴 OOS Sharpe > 1.0, DSR passes |
| P4 Risk + OMS | 20 | 5 wk (parallel w/ P2–P3) | — |
| P5 Paper Trading | 9 | 4 wk | 🔴 30-day clean soak |
| P6 Live, Min Capital | 7 | 3 wk + 12 wk validation | 🔴 90-day live clean |
| P7 Scale Out | 12 | 8 wk | — |
| P8 Institutional | 6 | 12 wk | — |
| P9 Multi-Asset | 6 | 12+ wk | — |
| **Total** | **107** | **~68 wk** | |

> The three 🔴 gates are not checkpoints. Passing them by lowering the bar is the most expensive decision available at each juncture.
