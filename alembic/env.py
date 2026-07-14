"""Alembic migration environment.

Reads the connection string from the `DATABASE_URL` environment variable
rather than a hardcoded value in `alembic.ini` — falls back to the local
dev Postgres from `docker-compose.yml` (T-P0-10) so `alembic upgrade head`
works out of the box after `make dev-up`, with no extra configuration.

That `DATABASE_URL`/default fallback only applies if `sqlalchemy.url`
isn't already set on the `Config` object. Callers that run migrations
programmatically against their own database — every integration test's
`db_engine` fixture, which points an ephemeral, per-test TimescaleDB
container by calling `config.set_main_option("sqlalchemy.url", ...)`
before invoking `command.upgrade(...)` — must have that value preserved
here, not silently overwritten back to the local dev Postgres default.
`alembic.ini`'s own `sqlalchemy.url` is left commented out precisely so
this module is the one place resolving it, for either caller.

`target_metadata` is `infrastructure.db.tables.metadata` — the single
shared `MetaData` every table in that package registers onto (see
`infrastructure/db/tables/_metadata.py`).
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from infrastructure.db.tables import metadata as target_metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_DEFAULT_DATABASE_URL = "postgresql+psycopg://quanttrade:quanttrade_dev_password@localhost:5432/quanttrade"
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
