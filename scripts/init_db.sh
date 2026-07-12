#!/usr/bin/env bash
# Runs automatically via Postgres's own docker-entrypoint-initdb.d/
# mechanism on first container startup — mounted read-only in
# docker-compose.yml, for both the app and test Postgres instances. By the
# time this script runs, the official Postgres image's own bootstrap has
# already created $POSTGRES_USER and $POSTGRES_DB from the container's
# environment (POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB); this
# script finishes that initialization by enabling the TimescaleDB
# extension on the database it just created.
#
# TASKS.md T-P0-10: "Add a scripts/init_db.sh that creates the app user
# and database." ARCHITECTURE.md ADR-002: "PostgreSQL 16 + TimescaleDB."
#
# Reads $POSTGRES_USER / $POSTGRES_DB rather than hardcoding a name so the
# same script is correct for both the app database (quanttrade) and the
# test database (quanttrade_test) — see docker-compose.yml.

set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS timescaledb;
EOSQL
