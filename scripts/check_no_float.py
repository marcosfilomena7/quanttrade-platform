"""Static check: bans `float` from domain/ and application/.

ARCHITECTURE.md §3.3 / §3.5: "Decimal everywhere. Float is banned in any
code path touching price, quantity, or balance. Enforced by lint rule and
type checker, not by discipline."

This is a plain `ast`-based check rather than a ruff plugin or a mypy
plugin: ruff does not support user-defined rules, and a full mypy plugin
would be disproportionate to detecting a banned identifier and a banned
literal type in source text. Wired into `make lint`.

Flags, anywhere under the given paths:
  - a reference to the built-in name `float` (type annotation, `float(x)`
    call, base class, generic parameter such as `list[float]`, etc.)
  - a float literal (e.g. `1.5`, `.1`, `1e3`)

Suppression: a line carrying the trailing marker comment `# float-guard`
is exempt. This exists for the one legitimate reason to reference `float`
in banned code — a runtime type guard that *rejects* it, e.g.
`isinstance(x, float)` inside a validator. It is a deliberate, visible,
per-line opt-in, not a way to sneak an actual float value past the check.

Usage:
    python scripts/check_no_float.py [PATH ...]

Exits 0 if clean, 1 if any violation is found (each printed as
`file:line:col: message`).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SUPPRESSION_MARKER = "float-guard"


class FloatUsageVisitor(ast.NodeVisitor):
    """Collects every banned `float` reference and float literal in a module."""

    def __init__(self, filename: str, lines: list[str]) -> None:
        self.filename = filename
        self.lines = lines
        self.violations: list[str] = []

    def _is_suppressed(self, lineno: int) -> bool:
        if 1 <= lineno <= len(self.lines):
            return SUPPRESSION_MARKER in self.lines[lineno - 1]
        return False

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "float" and not self._is_suppressed(node.lineno):
            self.violations.append(
                f"{self.filename}:{node.lineno}:{node.col_offset}: "
                f"use of banned name 'float'"
            )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, float) and not self._is_suppressed(node.lineno):
            self.violations.append(
                f"{self.filename}:{node.lineno}:{node.col_offset}: "
                f"float literal {node.value!r} is banned; use Decimal"
            )
        self.generic_visit(node)


def check_file(path: Path) -> list[str]:
    """Return every violation found in a single Python source file."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: SyntaxError: {exc.msg}"]

    visitor = FloatUsageVisitor(str(path), source.splitlines())
    visitor.visit(tree)
    return visitor.violations


def check_paths(paths: list[str]) -> list[str]:
    """Return every violation found under the given files/directories."""
    violations: list[str] = []
    for raw_path in paths:
        root = Path(raw_path)
        if not root.exists():
            continue
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for file_path in files:
            violations.extend(check_file(file_path))
    return violations


def main(argv: list[str]) -> int:
    paths = argv if argv else ["domain", "application"]
    violations = check_paths(paths)
    if violations:
        for violation in violations:
            print(violation)
        print(f"\nFound {len(violations)} banned float usage(s).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
