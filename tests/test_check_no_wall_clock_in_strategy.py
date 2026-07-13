"""Tests for scripts/check_no_wall_clock_in_strategy.py — the AST-based
Strategy wall-clock ban wired into `make lint` (TASKS.md T-P2-07 AC3)."""

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check_no_wall_clock_in_strategy.py"
_SPEC = importlib.util.spec_from_file_location("check_no_wall_clock_in_strategy", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
check_no_wall_clock_in_strategy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_no_wall_clock_in_strategy)

check_file = check_no_wall_clock_in_strategy.check_file
check_paths = check_no_wall_clock_in_strategy.check_paths


# --- acceptance criterion 3: a strategy calling datetime.now() in on_data is flagged --


def test_a_strategy_calling_datetime_now_in_on_data_is_flagged(tmp_path: Path) -> None:
    """TASKS.md T-P2-07 acceptance criterion, verbatim: "A strategy that
    calls `datetime.now()` in `on_data` is flagged by the custom lint
    rule.\""""
    source = tmp_path / "bad_strategy.py"
    source.write_text(
        "from datetime import datetime\n"
        "from domain.strategy import Strategy\n\n"
        "class MyStrategy(Strategy):\n"
        "    def on_data(self, event, view):\n"
        "        return datetime.now()\n"
    )
    violations = check_file(source)
    assert len(violations) == 1
    assert "on_data" in violations[0]


def test_a_strategy_calling_datetime_utcnow_is_also_flagged(tmp_path: Path) -> None:
    source = tmp_path / "bad_strategy_utcnow.py"
    source.write_text(
        "from datetime import datetime\n"
        "from domain.strategy import Strategy\n\n"
        "class MyStrategy(Strategy):\n"
        "    def on_start(self, context):\n"
        "        datetime.utcnow()\n"
    )
    violations = check_file(source)
    assert len(violations) == 1
    assert "on_start" in violations[0]


def test_datetime_now_outside_any_strategy_subclass_is_not_flagged(tmp_path: Path) -> None:
    """A blanket ban would break infrastructure/clock.py::RealClock's own
    legitimate `datetime.now(UTC)` call — the ban must be scoped to
    Strategy subclasses only."""
    source = tmp_path / "real_clock.py"
    source.write_text(
        "from datetime import UTC, datetime\n\n"
        "class RealClock:\n"
        "    def now(self):\n"
        "        return datetime.now(UTC)\n"
    )
    assert check_file(source) == []


def test_datetime_now_in_a_non_strategy_method_of_a_strategy_subclass_is_still_flagged(
    tmp_path: Path,
) -> None:
    """The ban applies to every method of a Strategy subclass, not just
    `on_data` — matching TASKS.md's broader "no wall clock in any
    strategy method" wording; AC3 names `on_data` only as its tested
    example."""
    source = tmp_path / "bad_strategy_other_method.py"
    source.write_text(
        "from datetime import datetime\n"
        "from domain.strategy import Strategy\n\n"
        "class MyStrategy(Strategy):\n"
        "    def on_stop(self):\n"
        "        datetime.now()\n"
    )
    violations = check_file(source)
    assert len(violations) == 1
    assert "on_stop" in violations[0]


def test_a_strategy_using_the_injected_clock_is_not_flagged(tmp_path: Path) -> None:
    source = tmp_path / "good_strategy.py"
    source.write_text(
        "from domain.strategy import Strategy\n\n"
        "class MyStrategy(Strategy):\n"
        "    def on_start(self, context):\n"
        "        self._clock = context.clock\n"
        "    def on_data(self, event, view):\n"
        "        return self._clock.now()\n"
    )
    assert check_file(source) == []


def test_a_nested_closure_inside_a_strategy_method_is_still_checked(tmp_path: Path) -> None:
    source = tmp_path / "bad_strategy_nested.py"
    source.write_text(
        "from datetime import datetime\n"
        "from domain.strategy import Strategy\n\n"
        "class MyStrategy(Strategy):\n"
        "    def on_data(self, event, view):\n"
        "        def helper():\n"
        "            return datetime.now()\n"
        "        return helper()\n"
    )
    violations = check_file(source)
    assert len(violations) == 1


def test_syntax_error_is_reported_not_raised(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    source.write_text("def f(:\n")
    violations = check_file(source)
    assert len(violations) == 1
    assert "SyntaxError" in violations[0]


def test_check_paths_walks_directory_recursively(tmp_path: Path) -> None:
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "clean.py").write_text("x = 1\n")
    (nested / "dirty.py").write_text(
        "from datetime import datetime\n"
        "from domain.strategy import Strategy\n\n"
        "class MyStrategy(Strategy):\n"
        "    def on_data(self, event, view):\n"
        "        return datetime.now()\n"
    )
    violations = check_paths([str(tmp_path)])
    assert len(violations) == 1
    assert "dirty.py" in violations[0]


def test_check_paths_ignores_nonexistent_path() -> None:
    assert check_paths(["/definitely/does/not/exist"]) == []
