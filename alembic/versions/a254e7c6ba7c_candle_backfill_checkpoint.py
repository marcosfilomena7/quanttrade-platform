"""candle backfill checkpoint

TASKS.md T-P1-04: "log progress to a checkpoint table so interrupted
runs resume from the last successful chunk." `candle_backfill_checkpoint`
is not one of DATABASE.md's original 23 entities (+ trade_tick, the
baseline migration's 24th) — see
`infrastructure/db/tables/backfill.py`'s docstring for why a new table
is the correct, required implementation of this acceptance criterion,
not scope creep.

Generated the same way as the baseline migration (e01343649d57): from
`infrastructure/db/tables` (the single shared `MetaData`) via
SQLAlchemy's DDL compiler, embedded here as frozen, literal SQL rather
than re-imported at migration run time — this file must represent a
fixed historical step, unaffected by any later edit to the live table
definition.

Revision ID: a254e7c6ba7c
Revises: e01343649d57
Create Date: 2026-07-12 13:17:18.561198

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a254e7c6ba7c'
down_revision: str | Sequence[str] | None = 'e01343649d57'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
CREATE TYPE backfill_checkpoint_status AS ENUM ('in_progress', 'completed')
        """
    )
    op.execute(
        """
CREATE TABLE candle_backfill_checkpoint (
	venue_id UUID NOT NULL,
	instrument_id UUID NOT NULL,
	interval VARCHAR NOT NULL,
	range_start TIMESTAMP WITH TIME ZONE NOT NULL,
	range_end TIMESTAMP WITH TIME ZONE NOT NULL,
	last_completed_open_time TIMESTAMP WITH TIME ZONE,
	status backfill_checkpoint_status NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_candle_backfill_checkpoint PRIMARY KEY (venue_id, instrument_id, interval, range_start, range_end),
	FOREIGN KEY(venue_id) REFERENCES venue (id),
	FOREIGN KEY(instrument_id) REFERENCES instrument (id)
)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
DROP TABLE candle_backfill_checkpoint
        """
    )
    op.execute(
        """
DROP TYPE backfill_checkpoint_status
        """
    )
