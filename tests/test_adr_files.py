"""Structural tests for docs/adr/ — no database or Docker required.

Verifies the T-P0-12 acceptance criteria that are properties of the ADR
files themselves: exactly five exist, they follow a consistent template,
and each cites the ARCHITECTURE.md section it is drawn from. The
"no file is ever deleted" invariant is a *history* property, not a
file-contents property, and is covered separately by
tests/test_check_adr_not_deleted.py and `make adr-check`.
"""

from __future__ import annotations

from pathlib import Path

ADR_DIR = Path(__file__).parent.parent / "docs" / "adr"

REQUIRED_TEMPLATE_FIELDS = ("Status", "Supersedes", "Superseded-by")
REQUIRED_TEMPLATE_SECTIONS = ("## Context", "## Decision", "## Consequences", "## Reference")

EXPECTED_ADR_FILES = {
    "ADR-000-mid-frequency-scope-not-hft.md",
    "ADR-001-primary-language-python.md",
    "ADR-002-primary-database-postgres-timescaledb.md",
    "ADR-003-message-bus-asyncio-to-nats.md",
    "ADR-004-orm-sqlalchemy-core-no-domain-orm.md",
}


def _adr_files() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("ADR-*.md"))


def test_exactly_five_adr_files_exist() -> None:
    assert {p.name for p in _adr_files()} == EXPECTED_ADR_FILES


def test_every_adr_follows_the_consistent_template() -> None:
    for path in _adr_files():
        text = path.read_text(encoding="utf-8")
        for field in REQUIRED_TEMPLATE_FIELDS:
            assert f"| {field} |" in text, f"{path.name} is missing template field {field!r}"
        for section in REQUIRED_TEMPLATE_SECTIONS:
            assert section in text, f"{path.name} is missing template section {section!r}"


def test_every_adr_references_architecture_md() -> None:
    for path in _adr_files():
        text = path.read_text(encoding="utf-8")
        assert "ARCHITECTURE.md §" in text, (
            f"{path.name} does not cite a specific ARCHITECTURE.md section"
        )


def test_every_adr_title_matches_its_filename_number() -> None:
    for path in _adr_files():
        number = path.name.split("-")[1]
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith(f"# ADR-{number}:"), (
            f"{path.name}'s first line {first_line!r} doesn't start with '# ADR-{number}:'"
        )


def test_docs_adr_readme_documents_the_append_only_rule() -> None:
    readme = ADR_DIR / "README.md"
    assert readme.exists()
    text = readme.read_text(encoding="utf-8")
    assert "never deleted" in text.lower() or "append-only" in text.lower()
    assert "check_adr_not_deleted.py" in text
