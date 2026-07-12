"""CI check: `docs/adr/` is append-only — no ADR file may ever be deleted or
moved out of the directory.

ARCHITECTURE.md's ADR convention (TASKS.md T-P0-12): "These are living
documents — never deleted, only superseded with a forward link." A
decision that changes gets a *new* ADR whose `Supersedes` field points
back at the old one; the old file's `Superseded-by` field is updated to
point forward. The old file's own history stays untouched. This script
enforces that invariant at the git-history level (which files exist),
not by inspecting ADR contents — see `tests/test_adr_files.py` for the
content/template checks.

Compares two git refs with `git diff --name-status` and fails if a path
under `docs/adr/` was deleted (status `D`) or renamed such that its new
path is no longer under `docs/adr/` (status `Rxxx` with a destination
outside the directory). An ordinary edit to an *existing* ADR's contents
(fixing a typo, adding a missing reference) is not itself banned by this
check — only removing the file from `docs/adr/` is.

Usage:
    python scripts/check_adr_not_deleted.py <base-ref> <head-ref>

Exits 0 if clean, 1 if any docs/adr/ file was deleted or moved out,
2 on a usage error.
"""

from __future__ import annotations

import subprocess
import sys

ADR_DIR_PREFIX = "docs/adr/"


def find_violations(diff_lines: list[str]) -> list[str]:
    """Parse `git diff --name-status` output lines and return one message
    per docs/adr/ path removed by a delete or a rename-out.

    Each `diff_lines` entry is a raw, tab-separated line as `git diff
    --name-status` emits it: `"D\tpath"` for a delete, or
    `"R100\told_path\tnew_path"` for a rename. Blank lines are ignored so
    callers can pass `subprocess_result.stdout.splitlines()` directly.
    """
    violations: list[str] = []
    for raw_line in diff_lines:
        line = raw_line.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status == "D":
            path = parts[1]
            if path.startswith(ADR_DIR_PREFIX):
                violations.append(f"deleted: {path}")
        elif status.startswith("R") and len(parts) == 3:
            old_path, new_path = parts[1], parts[2]
            if old_path.startswith(ADR_DIR_PREFIX) and not new_path.startswith(ADR_DIR_PREFIX):
                violations.append(f"moved out of docs/adr/: {old_path} -> {new_path}")
    return violations


def git_diff_name_status(base_ref: str, head_ref: str) -> list[str]:
    """Run the real `git diff --name-status` scoped to docs/adr/ and return its lines."""
    result = subprocess.run(
        ["git", "diff", "--name-status", f"{base_ref}...{head_ref}", "--", "docs/adr"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/check_adr_not_deleted.py <base-ref> <head-ref>")
        return 2

    base_ref, head_ref = argv
    diff_lines = git_diff_name_status(base_ref, head_ref)
    violations = find_violations(diff_lines)
    if violations:
        for violation in violations:
            print(violation)
        print(
            f"\nFound {len(violations)} docs/adr/ file(s) removed between "
            f"{base_ref} and {head_ref}. ADRs are living documents — never "
            "delete or move one out of docs/adr/; add a new ADR and mark the "
            "old one 'Superseded-by' instead."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
