"""The single `MetaData` instance every table in this package registers onto.

All 24 tables must share exactly one `MetaData` object: SQLAlchemy
resolves a string `ForeignKey("venue.id")` by looking up the table named
"venue" within the *same* `MetaData` the referencing table belongs to.
Separate `MetaData()` instances per file would silently break every
cross-file foreign key and leave Alembic's `target_metadata` seeing only
whichever table module happened to be imported last.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()
