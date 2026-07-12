"""Persistence layer: SQLAlchemy Core schema and Alembic migrations.

ARCHITECTURE.md §6.4 (ADR-004): "SQLAlchemy 2.0 Core for repositories...
The domain layer never imports SQLAlchemy." Nothing under `domain/` or
`application/` imports from here; this package only imports outward to
`sqlalchemy` itself.
"""
