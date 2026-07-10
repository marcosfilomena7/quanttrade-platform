"""Smoke test: proves the test harness (pytest, package imports) is wired up correctly.

This is intentionally the only test in Phase 0 — there is no domain logic yet
to exercise. It exists so `make test` has something to collect and pass on
an otherwise empty project, rather than exiting non-zero with "no tests
collected".
"""

import application
import domain
import infrastructure


def test_layers_are_importable() -> None:
    """The three top-level layers exist and import without error."""
    assert domain is not None
    assert application is not None
    assert infrastructure is not None
