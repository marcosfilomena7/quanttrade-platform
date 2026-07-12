"""data quality event

TASKS.md T-P1-05: "Violations write to a `data_quality_event` log table
and emit metrics. Data is quarantined, never silently dropped."
`data_quality_event` is not one of DATABASE.md's original entities — see
`infrastructure/db/tables/data_quality.py`'s docstring for why a new
table is the correct, required implementation of this acceptance
criterion.

Generated the same way as the two prior migrations: from
`infrastructure/db/tables` (the single shared `MetaData`) via
SQLAlchemy's DDL compiler, embedded here as frozen, literal SQL.

Revision ID: 652e22543f17
Revises: a254e7c6ba7c
Create Date: 2026-07-12 13:44:39.223103

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '652e22543f17'
down_revision: str | Sequence[str] | None = 'a254e7c6ba7c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
CREATE TYPE data_quality_event_severity AS ENUM ('quarantined', 'flagged')
        """
    )
    op.execute(
        """
CREATE TABLE data_quality_event (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	instrument_id UUID NOT NULL,
	interval VARCHAR NOT NULL,
	check_name VARCHAR NOT NULL,
	severity data_quality_event_severity NOT NULL,
	open_time TIMESTAMP WITH TIME ZONE NOT NULL,
	details JSONB NOT NULL,
	detected_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(instrument_id) REFERENCES instrument (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_data_quality_event_instrument_detected_at ON data_quality_event (instrument_id, detected_at DESC)
        """
    )
    op.execute(
        """
CREATE INDEX ix_data_quality_event_check_name ON data_quality_event (check_name)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
DROP TABLE data_quality_event
        """
    )
    op.execute(
        """
DROP TYPE data_quality_event_severity
        """
    )
