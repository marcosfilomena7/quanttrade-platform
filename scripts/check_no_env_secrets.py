"""Static check: bans `os.environ["*KEY*"]`-style literal secret access
outside infrastructure/secrets/.

ARCHITECTURE.md §3.6 / TASKS.md T-P0-09: "Config loading must call
`SecretsClient`, never read `os.environ` directly for credentials." This
is a plain `ast`-based check, mirroring `scripts/check_no_float.py`'s
approach for the same reason: ruff has no user-defined "banned literal
pattern" rule, and a full custom ruff plugin would be disproportionate to
detecting a string literal containing "KEY" passed to `os.environ[...]`
or `os.environ.get(...)`. Wired into `make lint`.

Flags, anywhere outside `infrastructure/secrets/`:
  - `os.environ["SOME_KEY"]` / `environ["SOME_KEY"]`
  - `os.environ.get("SOME_KEY")` / `environ.get("SOME_KEY")`
  where the literal key contains "KEY" (case-insensitive).

Only `infrastructure/secrets/` is exempt — that is the one place allowed
to read environment variables for a credential (`EnvSecretsClient`, for
local development only, never in prod).

Usage:
    python scripts/check_no_env_secrets.py [PATH ...]

Exits 0 if clean, 1 if any violation is found (each printed as
`file:line:col: message`).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

EXEMPT_PATH_PARTS = ("infrastructure", "secrets")
SECRET_MARKER = "KEY"


def _is_exempt(path: Path) -> bool:
    parts = path.parts
    return any(
        parts[i : i + len(EXEMPT_PATH_PARTS)] == EXEMPT_PATH_PARTS
        for i in range(len(parts) - len(EXEMPT_PATH_PARTS) + 1)
    )


def _is_environ_expr(node: ast.expr) -> bool:
    """True for `os.environ` or a bare `environ` (from `from os import environ`)."""
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id == "environ"


def _string_literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class EnvSecretUsageVisitor(ast.NodeVisitor):
    """Collects every banned literal-keyed `os.environ` secret access in a module."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []

    def _check_key(self, node: ast.expr, lineno: int, col_offset: int) -> None:
        key = _string_literal(node)
        if key is not None and SECRET_MARKER in key.upper():
            self.violations.append(
                f"{self.filename}:{lineno}:{col_offset}: "
                f"os.environ access with literal key {key!r} — read secrets via "
                f"SecretsClient (infrastructure/secrets/), not os.environ"
            )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_environ_expr(node.value):
            self._check_key(node.slice, node.lineno, node.col_offset)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        is_environ_get = (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and _is_environ_expr(func.value)
        )
        if is_environ_get and node.args:
            self._check_key(node.args[0], node.lineno, node.col_offset)
        self.generic_visit(node)


def check_file(path: Path) -> list[str]:
    """Return every violation found in a single Python source file."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: SyntaxError: {exc.msg}"]

    visitor = EnvSecretUsageVisitor(str(path))
    visitor.visit(tree)
    return visitor.violations


def check_paths(paths: list[str]) -> list[str]:
    """Return every violation found under the given files/directories,
    skipping anything under infrastructure/secrets/."""
    violations: list[str] = []
    for raw_path in paths:
        root = Path(raw_path)
        if not root.exists():
            continue
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for file_path in files:
            if _is_exempt(file_path):
                continue
            violations.extend(check_file(file_path))
    return violations


def main(argv: list[str]) -> int:
    paths = argv if argv else ["domain", "application", "infrastructure"]
    violations = check_paths(paths)
    if violations:
        for violation in violations:
            print(violation)
        print(f"\nFound {len(violations)} banned os.environ secret access(es).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
