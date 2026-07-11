"""Tests for scripts/check_no_float.py — the AST-based float ban wired into `make lint`."""

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check_no_float.py"
_SPEC = importlib.util.spec_from_file_location("check_no_float", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
check_no_float = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_no_float)

check_file = check_no_float.check_file
check_paths = check_no_float.check_paths


def test_clean_source_has_no_violations(tmp_path: Path) -> None:
    source = tmp_path / "clean.py"
    source.write_text(
        "from decimal import Decimal\n\n\ndef add(a: Decimal, b: Decimal) -> Decimal:\n"
        "    return a + b\n"
    )
    assert check_file(source) == []


def test_float_type_annotation_is_flagged(tmp_path: Path) -> None:
    source = tmp_path / "bad_annotation.py"
    source.write_text("def add(a: float, b: float) -> float:\n    return a + b\n")
    violations = check_file(source)
    assert len(violations) == 3  # two params + one return annotation
    assert all("banned name 'float'" in v for v in violations)


def test_float_literal_is_flagged(tmp_path: Path) -> None:
    source = tmp_path / "bad_literal.py"
    source.write_text("x = 1.5\n")
    violations = check_file(source)
    assert len(violations) == 1
    assert "1.5" in violations[0]
    assert "banned" in violations[0]


def test_float_guard_marker_suppresses_name_violation(tmp_path: Path) -> None:
    """The one legitimate reason to reference `float` in banned code is a
    runtime type guard that rejects it, e.g. `isinstance(x, float)` inside
    a validator. That line may opt out with a trailing `# float-guard`."""
    source = tmp_path / "guarded.py"
    source.write_text(
        "def reject(x: object) -> None:\n"
        "    if isinstance(x, float):  # float-guard\n"
        "        raise TypeError('no floats')\n"
    )
    assert check_file(source) == []


def test_float_guard_marker_does_not_suppress_other_lines(tmp_path: Path) -> None:
    """The suppression is per-line, not per-file — an unmarked float usage
    elsewhere in the same file must still be flagged."""
    source = tmp_path / "partially_guarded.py"
    source.write_text(
        "def reject(x: object) -> None:\n"
        "    if isinstance(x, float):  # float-guard\n"
        "        raise TypeError('no floats')\n"
        "\n"
        "\n"
        "y: float = 1.0\n"
    )
    violations = check_file(source)
    assert len(violations) == 2  # the annotation and the literal on the unmarked line
    assert all(":2:" not in v for v in violations)  # the guarded line must not appear
    assert all(":6:" in v for v in violations)  # both violations are on the unmarked line


def test_float_call_is_flagged(tmp_path: Path) -> None:
    source = tmp_path / "bad_call.py"
    source.write_text("x = float('1.5')\n")
    violations = check_file(source)
    assert any("banned name 'float'" in v for v in violations)


def test_generic_parameter_float_is_flagged(tmp_path: Path) -> None:
    source = tmp_path / "bad_generic.py"
    source.write_text("x: list[float] = []\n")
    violations = check_file(source)
    assert any("banned name 'float'" in v for v in violations)


def test_decimal_string_construction_is_not_flagged(tmp_path: Path) -> None:
    source = tmp_path / "decimal_ok.py"
    source.write_text("from decimal import Decimal\nx = Decimal('1.5')\n")
    assert check_file(source) == []


def test_int_literal_is_not_flagged(tmp_path: Path) -> None:
    source = tmp_path / "int_ok.py"
    source.write_text("x = 15\n")
    assert check_file(source) == []


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
    (nested / "dirty.py").write_text("x = 1.0\n")
    violations = check_paths([str(tmp_path)])
    assert len(violations) == 1
    assert "dirty.py" in violations[0]


def test_check_paths_ignores_nonexistent_path() -> None:
    assert check_paths(["/definitely/does/not/exist"]) == []
