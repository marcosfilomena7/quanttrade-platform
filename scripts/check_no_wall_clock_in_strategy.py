"""Static check: bans wall-clock access inside any Strategy subclass's own methods.

ARCHITECTURE.md §4.7: "The strategy cannot access a wall clock. It
receives time from the injected `Clock` port. A strategy that calls
`datetime.now()` is nondeterministic and will produce a different
backtest on every run." TASKS.md T-P2-07's own words: "No network, no
database, no wall clock in any strategy method — enforced by the port
contract and lint."

This is a plain `ast`-based check, mirroring `check_no_float.py`'s and
`check_no_env_secrets.py`'s own approach, for the same underlying
reason those exist: ruff has no user-defined-rule mechanism, and a full
mypy plugin would be disproportionate. The check is deliberately scoped
to methods of classes that structurally subclass `Strategy` — never a
blanket ban on `datetime.now()`/`datetime.utcnow()` across the whole
codebase, which would immediately and incorrectly flag
`infrastructure/clock.py::RealClock`'s own legitimate
`datetime.now(UTC)` call. Wired into `make lint`.

Flags, inside any method (a function defined directly in the body) of
any class whose bases include a name `Strategy`:
  - `datetime.now(...)`
  - `datetime.utcnow(...)`

Usage:
    python scripts/check_no_wall_clock_in_strategy.py [PATH ...]

Exits 0 if clean, 1 if any violation is found (each printed as
`file:line:col: message`).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_BANNED_DATETIME_METHODS = {"now", "utcnow"}


def _is_strategy_subclass(node: ast.ClassDef) -> bool:
    return any(isinstance(base, ast.Name) and base.id == "Strategy" for base in node.bases)


def _is_banned_wall_clock_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _BANNED_DATETIME_METHODS:
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "datetime"


class StrategyWallClockVisitor(ast.NodeVisitor):
    """Collects every banned wall-clock call inside a Strategy subclass's own methods."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if _is_strategy_subclass(node):
            for method in node.body:
                if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                    self._check_method(method)
        self.generic_visit(node)

    def _check_method(self, method: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for call_node in ast.walk(method):
            if isinstance(call_node, ast.Call) and _is_banned_wall_clock_call(call_node):
                self.violations.append(
                    f"{self.filename}:{call_node.lineno}:{call_node.col_offset}: "
                    f"Strategy method {method.name!r} calls the wall clock directly "
                    f"(datetime.now()/datetime.utcnow()) — receive time from the "
                    f"injected Clock via StrategyContext instead"
                )


def check_file(path: Path) -> list[str]:
    """Return every violation found in a single Python source file."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: SyntaxError: {exc.msg}"]

    visitor = StrategyWallClockVisitor(str(path))
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
    paths = argv if argv else ["domain", "application", "infrastructure"]
    violations = check_paths(paths)
    if violations:
        for violation in violations:
            print(violation)
        print(f"\nFound {len(violations)} strategy wall-clock access violation(s).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
