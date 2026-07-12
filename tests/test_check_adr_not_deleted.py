"""Tests for scripts/check_adr_not_deleted.py — the docs/adr/ append-only
check wired in via `make adr-check` (T-P0-12).

Two layers, mirroring the file's own split between pure logic and git
plumbing:
  - `find_violations()` is tested directly against synthetic
    `git diff --name-status` lines — no git process involved.
  - `git_diff_name_status()` is exercised against a *real*, disposable git
    repository created in `tmp_path`, so the git-plumbing half of the
    script is genuinely runtime-verified here (not mocked), independent
    of whether the CI workflow itself has actually run.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check_adr_not_deleted.py"
_SPEC = importlib.util.spec_from_file_location("check_adr_not_deleted", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
check_adr_not_deleted = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_adr_not_deleted)

find_violations = check_adr_not_deleted.find_violations
git_diff_name_status = check_adr_not_deleted.git_diff_name_status
main = check_adr_not_deleted.main


# --- find_violations(): pure parsing logic, no git involved ----------------


def test_no_diff_lines_is_clean() -> None:
    assert find_violations([]) == []


def test_blank_lines_are_ignored() -> None:
    assert find_violations(["", "\n", ""]) == []


def test_adding_a_new_adr_is_not_a_violation() -> None:
    diff = ["A\tdocs/adr/ADR-005-new-decision.md"]
    assert find_violations(diff) == []


def test_editing_an_existing_adr_is_not_a_violation() -> None:
    diff = ["M\tdocs/adr/ADR-001-primary-language-python.md"]
    assert find_violations(diff) == []


def test_deleting_an_adr_is_a_violation() -> None:
    diff = ["D\tdocs/adr/ADR-002-primary-database-postgres-timescaledb.md"]
    violations = find_violations(diff)
    assert len(violations) == 1
    assert "deleted" in violations[0]
    assert "ADR-002" in violations[0]


def test_deleting_a_non_adr_file_under_docs_adr_prefix_check_is_scoped() -> None:
    """The diff is already scoped to docs/adr/ by the `git diff -- docs/adr`
    invocation, but find_violations() re-checks the prefix defensively —
    a delete reported for any other path is simply not flagged."""
    diff = ["D\tdocs/adr/README.md"]
    violations = find_violations(diff)
    assert len(violations) == 1
    assert "README.md" in violations[0]


def test_renaming_an_adr_out_of_docs_adr_is_a_violation() -> None:
    diff = ["R100\tdocs/adr/ADR-003-message-bus-asyncio-to-nats.md\tdocs/archive/ADR-003.md"]
    violations = find_violations(diff)
    assert len(violations) == 1
    assert "moved out of docs/adr/" in violations[0]


def test_renaming_within_docs_adr_is_not_a_violation() -> None:
    """A rename that keeps the file under docs/adr/ (e.g. a filename typo
    fix) is not a removal."""
    diff = [
        "R100\tdocs/adr/ADR-004-orm-sqlalchemy-core-no-domain-orm.md\t"
        "docs/adr/ADR-004-orm-sqlalchemy-core.md"
    ]
    assert find_violations(diff) == []


def test_multiple_violations_are_all_reported() -> None:
    diff = [
        "D\tdocs/adr/ADR-000-mid-frequency-scope-not-hft.md",
        "D\tdocs/adr/ADR-001-primary-language-python.md",
        "A\tdocs/adr/ADR-005-something-new.md",
    ]
    violations = find_violations(diff)
    assert len(violations) == 2


# --- git_diff_name_status() + main(): real git, disposable temp repo ------


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def _init_repo_with_adrs(repo: Path) -> str:
    """Create a throwaway git repo with two ADR files, committed. Returns
    the commit sha of that initial commit."""
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q"], cwd=repo)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=repo)
    _run_git(["config", "user.name", "Test"], cwd=repo)

    adr_dir = repo / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "ADR-000-example.md").write_text("# ADR-000\n\nExample.\n")
    (adr_dir / "ADR-001-example.md").write_text("# ADR-001\n\nExample.\n")

    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "initial ADRs"], cwd=repo)
    result = _run_git(["rev-parse", "HEAD"], cwd=repo)
    return result.stdout.strip()


@pytest.fixture
def adr_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    base_sha = _init_repo_with_adrs(repo)
    monkeypatch.chdir(repo)
    return repo, base_sha


def test_git_diff_name_status_reports_no_lines_when_only_adding(
    adr_repo: tuple[Path, str],
) -> None:
    repo, base_sha = adr_repo
    (repo / "docs" / "adr" / "ADR-002-example.md").write_text("# ADR-002\n\nNew.\n")
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "add ADR-002"], cwd=repo)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()

    diff_lines = git_diff_name_status(base_sha, head_sha)
    assert find_violations(diff_lines) == []


def test_git_diff_name_status_reports_a_real_deletion(adr_repo: tuple[Path, str]) -> None:
    repo, base_sha = adr_repo
    (repo / "docs" / "adr" / "ADR-001-example.md").unlink()
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "delete ADR-001"], cwd=repo)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()

    diff_lines = git_diff_name_status(base_sha, head_sha)
    violations = find_violations(diff_lines)
    assert len(violations) == 1
    assert "ADR-001-example.md" in violations[0]


def test_main_exits_zero_when_only_adding(adr_repo: tuple[Path, str]) -> None:
    repo, base_sha = adr_repo
    (repo / "docs" / "adr" / "ADR-002-example.md").write_text("# ADR-002\n\nNew.\n")
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "add ADR-002"], cwd=repo)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()

    assert main([base_sha, head_sha]) == 0


def test_main_exits_one_on_a_real_deletion(adr_repo: tuple[Path, str]) -> None:
    repo, base_sha = adr_repo
    (repo / "docs" / "adr" / "ADR-000-example.md").unlink()
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "delete ADR-000"], cwd=repo)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()

    assert main([base_sha, head_sha]) == 1


def test_main_usage_error_returns_two() -> None:
    assert main(["only-one-arg"]) == 2
