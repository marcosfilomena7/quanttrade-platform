"""baseline schema

TASKS.md T-P0-11: creates all 23 entities from docs/DATABASE.md plus
`trade_tick` (see infrastructure/db/tables/market_data.py's docstring for
why it is also included, and infrastructure/db/tables/portfolio.py's
docstring for why equity_snapshot's primary key is composite (id, ts)
rather than the bare `id` DATABASE.md states).

Generated from infrastructure/db/tables (a single shared `MetaData`), via
SQLAlchemy's mock-engine DDL emission — this guarantees table/type
creation order respects every foreign-key dependency, and gives an exact,
frozen snapshot of the schema as SQLAlchemy would build it. Deliberately
NOT re-imported from infrastructure.db.tables at migration run time:
a migration must represent a fixed historical step, not a moving target
that would silently change if a later commit edits the live table
definitions.

Revision ID: e01343649d57
Revises: 
Create Date: 2026-07-12 05:05:49.175347

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e01343649d57'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
CREATE TYPE order_side AS ENUM ('buy', 'sell')
        """
    )
    op.execute(
        """
CREATE TYPE order_type AS ENUM ('market', 'limit')
        """
    )
    op.execute(
        """
CREATE TYPE order_status AS ENUM ('pending_new', 'sent', 'acked', 'partially_filled', 'filled', 'pending_cancel', 'canceled', 'rejected', 'expired', 'unknown')
        """
    )
    op.execute(
        """
CREATE TYPE order_tif AS ENUM ('gtc', 'ioc', 'fok')
        """
    )
    op.execute(
        """
CREATE TYPE order_event_type AS ENUM ('created', 'sent', 'acked', 'rejected', 'partially_filled', 'filled', 'cancel_requested', 'canceled', 'expired', 'adopted')
        """
    )
    op.execute(
        """
CREATE TYPE reconciliation_resolution AS ENUM ('none_needed', 'auto_adopted', 'halted_pending_review')
        """
    )
    op.execute(
        """
CREATE TYPE venue_type AS ENUM ('cex', 'dex', 'broker')
        """
    )
    op.execute(
        """
CREATE TYPE venue_status AS ENUM ('active', 'disabled')
        """
    )
    op.execute(
        """
CREATE TYPE asset_class AS ENUM ('spot')
        """
    )
    op.execute(
        """
CREATE TYPE instrument_status AS ENUM ('trading', 'halted', 'delisted')
        """
    )
    op.execute(
        """
CREATE TYPE system_halt_tier AS ENUM ('soft_halt', 'hard_halt', 'kill')
        """
    )
    op.execute(
        """
CREATE TYPE system_halt_triggered_by AS ENUM ('system', 'operator')
        """
    )
    op.execute(
        """
CREATE TYPE strategy_instance_status AS ENUM ('registered', 'validated', 'initialized', 'warming_up', 'ready', 'running', 'paused', 'draining', 'stopped', 'faulted')
        """
    )
    op.execute(
        """
CREATE TYPE signal_direction AS ENUM ('long', 'short', 'flat')
        """
    )
    op.execute(
        """
CREATE TABLE event_log (
	seq BIGINT GENERATED ALWAYS AS IDENTITY, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	event_type VARCHAR NOT NULL, 
	aggregate_id UUID NOT NULL, 
	payload JSONB NOT NULL, 
	prev_hash BYTEA NOT NULL, 
	hash BYTEA NOT NULL, 
	PRIMARY KEY (seq)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_event_log_aggregate_id_seq ON event_log (aggregate_id, seq)
        """
    )
    op.execute(
        """
CREATE TABLE dataset_version (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	content_hash VARCHAR NOT NULL, 
	symbol_set JSONB NOT NULL, 
	date_range_start DATE NOT NULL, 
	date_range_end DATE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (content_hash)
)
        """
    )
    op.execute(
        """
CREATE TABLE venue (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	name VARCHAR NOT NULL, 
	venue_type venue_type NOT NULL, 
	api_base_url VARCHAR NOT NULL, 
	capabilities JSONB NOT NULL, 
	fee_schedule JSONB NOT NULL, 
	status venue_status NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)
        """
    )
    op.execute(
        """
CREATE TABLE strategy (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	name VARCHAR NOT NULL, 
	code_hash VARCHAR NOT NULL, 
	params_schema JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_strategy_name_code_hash UNIQUE (name, code_hash)
)
        """
    )
    op.execute(
        """
CREATE TABLE backtest_run (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	strategy_id UUID NOT NULL, 
	code_hash VARCHAR NOT NULL, 
	params JSONB NOT NULL, 
	dataset_version_id UUID NOT NULL, 
	seed INTEGER NOT NULL, 
	git_sha VARCHAR NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	operator VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(strategy_id) REFERENCES strategy (id), 
	FOREIGN KEY(dataset_version_id) REFERENCES dataset_version (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_backtest_run_strategy_id_started_at ON backtest_run (strategy_id, started_at DESC)
        """
    )
    op.execute(
        """
CREATE TABLE account (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	name VARCHAR NOT NULL, 
	venue_id UUID NOT NULL, 
	base_currency VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_account_venue_id_name UNIQUE (venue_id, name), 
	FOREIGN KEY(venue_id) REFERENCES venue (id)
)
        """
    )
    op.execute(
        """
CREATE TABLE instrument (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	venue_id UUID NOT NULL, 
	symbol VARCHAR NOT NULL, 
	asset_class asset_class NOT NULL, 
	base_currency VARCHAR NOT NULL, 
	quote_currency VARCHAR NOT NULL, 
	tick_size NUMERIC NOT NULL, 
	lot_size NUMERIC NOT NULL, 
	min_notional NUMERIC NOT NULL, 
	max_order_size NUMERIC, 
	status instrument_status NOT NULL, 
	listed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	delisted_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_instrument_venue_id_symbol UNIQUE (venue_id, symbol), 
	CONSTRAINT ck_instrument_tick_size_positive CHECK (tick_size > 0), 
	CONSTRAINT ck_instrument_lot_size_positive CHECK (lot_size > 0), 
	CONSTRAINT ck_instrument_min_notional_positive CHECK (min_notional > 0), 
	FOREIGN KEY(venue_id) REFERENCES venue (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_instrument_status_trading ON instrument (status) WHERE status = 'trading'
        """
    )
    op.execute(
        """
CREATE TABLE strategy_instance (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	strategy_id UUID NOT NULL, 
	params JSONB NOT NULL, 
	status strategy_instance_status NOT NULL, 
	allocated_capital NUMERIC NOT NULL, 
	capacity_usd NUMERIC NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	stopped_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_strategy_instance_allocated_capital_nonneg CHECK (allocated_capital >= 0), 
	CONSTRAINT ck_strategy_instance_capacity_usd_nonneg CHECK (capacity_usd >= 0), 
	FOREIGN KEY(strategy_id) REFERENCES strategy (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_strategy_instance_status_running ON strategy_instance (status) WHERE status = 'running'
        """
    )
    op.execute(
        """
CREATE TABLE backtest_trade (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	backtest_run_id UUID NOT NULL, 
	instrument_id UUID NOT NULL, 
	side order_side NOT NULL, 
	qty NUMERIC NOT NULL, 
	entry_price NUMERIC NOT NULL, 
	exit_price NUMERIC, 
	entry_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	exit_ts TIMESTAMP WITH TIME ZONE, 
	pnl NUMERIC NOT NULL, 
	fees NUMERIC NOT NULL, 
	slippage_applied NUMERIC NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_backtest_trade_entry_before_exit CHECK (exit_ts IS NULL OR entry_ts < exit_ts), 
	FOREIGN KEY(backtest_run_id) REFERENCES backtest_run (id), 
	FOREIGN KEY(instrument_id) REFERENCES instrument (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_backtest_trade_backtest_run_id ON backtest_trade (backtest_run_id)
        """
    )
    op.execute(
        """
CREATE TABLE backtest_metrics (
	backtest_run_id UUID NOT NULL, 
	total_return NUMERIC NOT NULL, 
	cagr NUMERIC NOT NULL, 
	volatility NUMERIC NOT NULL, 
	max_drawdown NUMERIC NOT NULL, 
	sharpe NUMERIC NOT NULL, 
	sortino NUMERIC NOT NULL, 
	calmar NUMERIC NOT NULL, 
	deflated_sharpe NUMERIC NOT NULL, 
	probabilistic_sharpe NUMERIC NOT NULL, 
	win_rate NUMERIC NOT NULL, 
	profit_factor NUMERIC NOT NULL, 
	avg_trade_pnl NUMERIC NOT NULL, 
	turnover NUMERIC NOT NULL, 
	fees_pct_of_gross NUMERIC NOT NULL, 
	trial_count_at_time_of_run INTEGER NOT NULL, 
	PRIMARY KEY (backtest_run_id), 
	FOREIGN KEY(backtest_run_id) REFERENCES backtest_run (id)
)
        """
    )
    op.execute(
        """
CREATE TABLE reconciliation_check (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	account_id UUID NOT NULL, 
	venue_id UUID NOT NULL, 
	ran_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	drift_detected BOOLEAN NOT NULL, 
	discrepancies JSONB NOT NULL, 
	resolution reconciliation_resolution NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES account (id), 
	FOREIGN KEY(venue_id) REFERENCES venue (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_reconciliation_check_ran_at ON reconciliation_check (ran_at DESC)
        """
    )
    op.execute(
        """
CREATE INDEX ix_reconciliation_check_drift_detected ON reconciliation_check (drift_detected) WHERE drift_detected IS true
        """
    )
    op.execute(
        """
CREATE TABLE candle (
	instrument_id UUID NOT NULL, 
	interval VARCHAR NOT NULL, 
	open_time TIMESTAMP WITH TIME ZONE NOT NULL, 
	open NUMERIC NOT NULL, 
	high NUMERIC NOT NULL, 
	low NUMERIC NOT NULL, 
	close NUMERIC NOT NULL, 
	volume NUMERIC NOT NULL, 
	trade_count INTEGER NOT NULL, 
	is_closed BOOLEAN NOT NULL, 
	source VARCHAR NOT NULL, 
	inserted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_candle PRIMARY KEY (instrument_id, interval, open_time), 
	CONSTRAINT ck_candle_high_vs_open_close CHECK (high >= greatest(open, close)), 
	CONSTRAINT ck_candle_low_vs_open_close CHECK (low <= least(open, close)), 
	CONSTRAINT ck_candle_high_vs_low CHECK (high >= low), 
	CONSTRAINT ck_candle_volume_nonneg CHECK (volume >= 0), 
	FOREIGN KEY(instrument_id) REFERENCES instrument (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_candle_instrument_open_time ON candle (instrument_id, open_time DESC)
        """
    )
    op.execute(
        """
SELECT create_hypertable('candle', 'open_time', if_not_exists => TRUE)
        """
    )
    op.execute(
        """
CREATE TABLE trade_tick (
	instrument_id UUID NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	venue_trade_id VARCHAR NOT NULL, 
	price NUMERIC NOT NULL, 
	qty NUMERIC NOT NULL, 
	side order_side NOT NULL, 
	CONSTRAINT pk_trade_tick PRIMARY KEY (instrument_id, ts, venue_trade_id), 
	CONSTRAINT ck_trade_tick_price_positive CHECK (price > 0), 
	CONSTRAINT ck_trade_tick_qty_positive CHECK (qty > 0), 
	FOREIGN KEY(instrument_id) REFERENCES instrument (id)
)
        """
    )
    op.execute(
        """
SELECT create_hypertable('trade_tick', 'ts', if_not_exists => TRUE)
        """
    )
    op.execute(
        """
CREATE TABLE position (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	account_id UUID NOT NULL, 
	instrument_id UUID NOT NULL, 
	strategy_instance_id UUID NOT NULL, 
	qty NUMERIC NOT NULL, 
	avg_entry_price NUMERIC NOT NULL, 
	realized_pnl NUMERIC NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_position_account_instrument_strategy_instance UNIQUE (account_id, instrument_id, strategy_instance_id), 
	FOREIGN KEY(account_id) REFERENCES account (id), 
	FOREIGN KEY(instrument_id) REFERENCES instrument (id), 
	FOREIGN KEY(strategy_instance_id) REFERENCES strategy_instance (id)
)
        """
    )
    op.execute(
        """
CREATE TABLE equity_snapshot (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	account_id UUID NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	cash NUMERIC NOT NULL, 
	positions_value NUMERIC NOT NULL, 
	total_equity NUMERIC NOT NULL, 
	drawdown_pct NUMERIC NOT NULL, 
	CONSTRAINT pk_equity_snapshot PRIMARY KEY (id, ts), 
	FOREIGN KEY(account_id) REFERENCES account (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_equity_snapshot_account_id_ts ON equity_snapshot (account_id, ts DESC)
        """
    )
    op.execute(
        """
SELECT create_hypertable('equity_snapshot', 'ts', if_not_exists => TRUE)
        """
    )
    op.execute(
        """
CREATE TABLE universe_snapshot (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	snapshot_date DATE NOT NULL, 
	venue_id UUID NOT NULL, 
	instrument_id UUID NOT NULL, 
	is_tradeable BOOLEAN NOT NULL, 
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_universe_snapshot_date_venue_instrument UNIQUE (snapshot_date, venue_id, instrument_id), 
	FOREIGN KEY(venue_id) REFERENCES venue (id), 
	FOREIGN KEY(instrument_id) REFERENCES instrument (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_universe_snapshot_date_venue ON universe_snapshot (snapshot_date, venue_id)
        """
    )
    op.execute(
        """
CREATE TABLE signal (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	strategy_instance_id UUID NOT NULL, 
	instrument_id UUID NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	direction signal_direction NOT NULL, 
	strength NUMERIC NOT NULL, 
	metadata JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_signal_strength_bounded CHECK (strength BETWEEN 0 AND 1), 
	FOREIGN KEY(strategy_instance_id) REFERENCES strategy_instance (id), 
	FOREIGN KEY(instrument_id) REFERENCES instrument (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_signal_strategy_instance_ts ON signal (strategy_instance_id, ts DESC)
        """
    )
    op.execute(
        """
CREATE TABLE order_intent (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	signal_id UUID, 
	strategy_instance_id UUID NOT NULL, 
	instrument_id UUID NOT NULL, 
	side order_side NOT NULL, 
	target_qty NUMERIC NOT NULL, 
	order_type order_type NOT NULL, 
	limit_price NUMERIC, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_order_intent_target_qty_nonzero CHECK (target_qty <> 0), 
	CONSTRAINT ck_order_intent_limit_price_required CHECK (order_type <> 'limit' OR limit_price IS NOT NULL), 
	FOREIGN KEY(signal_id) REFERENCES signal (id), 
	FOREIGN KEY(strategy_instance_id) REFERENCES strategy_instance (id), 
	FOREIGN KEY(instrument_id) REFERENCES instrument (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_order_intent_strategy_instance_created_at ON order_intent (strategy_instance_id, created_at DESC)
        """
    )
    op.execute(
        """
CREATE TABLE risk_decision (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	order_intent_id UUID NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	approved BOOLEAN NOT NULL, 
	rules_evaluated JSONB NOT NULL, 
	rejection_reason VARCHAR, 
	limits_config_version VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_risk_decision_rejection_reason_required CHECK (approved OR rejection_reason IS NOT NULL), 
	UNIQUE (order_intent_id), 
	FOREIGN KEY(order_intent_id) REFERENCES order_intent (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_risk_decision_ts ON risk_decision (ts DESC)
        """
    )
    op.execute(
        """
CREATE INDEX ix_risk_decision_approved_ts ON risk_decision (approved, ts DESC)
        """
    )
    op.execute(
        """
CREATE TABLE "order" (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	client_order_id VARCHAR NOT NULL, 
	venue_order_id VARCHAR, 
	venue_id UUID NOT NULL, 
	instrument_id UUID NOT NULL, 
	strategy_instance_id UUID NOT NULL, 
	risk_decision_id UUID NOT NULL, 
	side order_side NOT NULL, 
	order_type order_type NOT NULL, 
	qty NUMERIC NOT NULL, 
	limit_price NUMERIC, 
	filled_qty NUMERIC DEFAULT 0 NOT NULL, 
	avg_fill_price NUMERIC, 
	status order_status NOT NULL, 
	tif order_tif NOT NULL, 
	parent_order_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_order_venue_id_client_order_id UNIQUE (venue_id, client_order_id), 
	CONSTRAINT ck_order_filled_qty_le_qty CHECK (filled_qty <= qty), 
	CONSTRAINT ck_order_filled_qty_nonneg CHECK (filled_qty >= 0), 
	FOREIGN KEY(venue_id) REFERENCES venue (id), 
	FOREIGN KEY(instrument_id) REFERENCES instrument (id), 
	FOREIGN KEY(strategy_instance_id) REFERENCES strategy_instance (id), 
	FOREIGN KEY(risk_decision_id) REFERENCES risk_decision (id), 
	FOREIGN KEY(parent_order_id) REFERENCES "order" (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_order_strategy_instance_created_at ON "order" (strategy_instance_id, created_at DESC)
        """
    )
    op.execute(
        """
CREATE INDEX ix_order_status_open ON "order" (status) WHERE status IN ('pending_new', 'sent', 'acked', 'partially_filled', 'pending_cancel', 'unknown')
        """
    )
    op.execute(
        """
CREATE TABLE system_halt_event (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	tier system_halt_tier NOT NULL, 
	trigger_reason VARCHAR NOT NULL, 
	triggered_by system_halt_triggered_by NOT NULL, 
	triggered_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	risk_decision_id UUID, 
	cleared_at TIMESTAMP WITH TIME ZONE, 
	cleared_by VARCHAR, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_system_halt_event_cleared_by_required CHECK (cleared_at IS NULL OR cleared_by IS NOT NULL), 
	FOREIGN KEY(risk_decision_id) REFERENCES risk_decision (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_system_halt_event_triggered_at ON system_halt_event (triggered_at DESC)
        """
    )
    op.execute(
        """
CREATE INDEX ix_system_halt_event_uncleared ON system_halt_event (cleared_at) WHERE cleared_at IS NULL
        """
    )
    op.execute(
        """
CREATE TABLE order_event (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	order_id UUID NOT NULL, 
	seq INTEGER NOT NULL, 
	event_type order_event_type NOT NULL, 
	payload JSONB NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_order_event_order_id_seq UNIQUE (order_id, seq), 
	FOREIGN KEY(order_id) REFERENCES "order" (id)
)
        """
    )
    op.execute(
        """
CREATE TABLE fill (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	order_id UUID NOT NULL, 
	venue_id UUID NOT NULL, 
	venue_fill_id VARCHAR NOT NULL, 
	qty NUMERIC NOT NULL, 
	price NUMERIC NOT NULL, 
	fee NUMERIC NOT NULL, 
	fee_currency VARCHAR NOT NULL, 
	is_maker BOOLEAN NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_fill_venue_id_venue_fill_id UNIQUE (venue_id, venue_fill_id), 
	CONSTRAINT ck_fill_qty_positive CHECK (qty > 0), 
	CONSTRAINT ck_fill_price_positive CHECK (price > 0), 
	FOREIGN KEY(order_id) REFERENCES "order" (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_fill_order_id ON fill (order_id)
        """
    )
    op.execute(
        """
CREATE TABLE ledger_entry (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	account_id UUID NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	debit_account VARCHAR NOT NULL, 
	credit_account VARCHAR NOT NULL, 
	amount NUMERIC NOT NULL, 
	currency VARCHAR NOT NULL, 
	ref_fill_id UUID, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_ledger_entry_amount_positive CHECK (amount > 0), 
	FOREIGN KEY(account_id) REFERENCES account (id), 
	FOREIGN KEY(ref_fill_id) REFERENCES fill (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_ledger_entry_account_id_ts ON ledger_entry (account_id, ts DESC)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
DROP TABLE ledger_entry
        """
    )
    op.execute(
        """
DROP TABLE fill
        """
    )
    op.execute(
        """
DROP TABLE order_event
        """
    )
    op.execute(
        """
DROP TABLE system_halt_event
        """
    )
    op.execute(
        """
DROP TABLE "order"
        """
    )
    op.execute(
        """
DROP TABLE risk_decision
        """
    )
    op.execute(
        """
DROP TABLE order_intent
        """
    )
    op.execute(
        """
DROP TABLE signal
        """
    )
    op.execute(
        """
DROP TABLE universe_snapshot
        """
    )
    op.execute(
        """
DROP TABLE equity_snapshot
        """
    )
    op.execute(
        """
DROP TABLE position
        """
    )
    op.execute(
        """
DROP TABLE trade_tick
        """
    )
    op.execute(
        """
DROP TABLE candle
        """
    )
    op.execute(
        """
DROP TABLE reconciliation_check
        """
    )
    op.execute(
        """
DROP TABLE backtest_metrics
        """
    )
    op.execute(
        """
DROP TABLE backtest_trade
        """
    )
    op.execute(
        """
DROP TABLE strategy_instance
        """
    )
    op.execute(
        """
DROP TABLE instrument
        """
    )
    op.execute(
        """
DROP TABLE account
        """
    )
    op.execute(
        """
DROP TABLE backtest_run
        """
    )
    op.execute(
        """
DROP TABLE strategy
        """
    )
    op.execute(
        """
DROP TABLE venue
        """
    )
    op.execute(
        """
DROP TABLE dataset_version
        """
    )
    op.execute(
        """
DROP TABLE event_log
        """
    )
    op.execute(
        """
DROP TYPE order_side
        """
    )
    op.execute(
        """
DROP TYPE order_type
        """
    )
    op.execute(
        """
DROP TYPE order_status
        """
    )
    op.execute(
        """
DROP TYPE order_tif
        """
    )
    op.execute(
        """
DROP TYPE order_event_type
        """
    )
    op.execute(
        """
DROP TYPE reconciliation_resolution
        """
    )
    op.execute(
        """
DROP TYPE venue_type
        """
    )
    op.execute(
        """
DROP TYPE venue_status
        """
    )
    op.execute(
        """
DROP TYPE asset_class
        """
    )
    op.execute(
        """
DROP TYPE instrument_status
        """
    )
    op.execute(
        """
DROP TYPE system_halt_tier
        """
    )
    op.execute(
        """
DROP TYPE system_halt_triggered_by
        """
    )
    op.execute(
        """
DROP TYPE strategy_instance_status
        """
    )
    op.execute(
        """
DROP TYPE signal_direction
        """
    )
