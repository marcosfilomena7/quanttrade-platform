"""Tests for scripts/check_no_env_secrets.py — the AST-based os.environ
secrets ban wired into `make lint`."""

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check_no_env_secrets.py"
_SPEC = importlib.util.spec_from_file_location("check_no_env_secrets", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
check_no_env_secrets = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_no_env_secrets)

check_file = check_no_env_secrets.check_file
check_paths = check_no_env_secrets.check_paths


def test_clean_source_has_no_violations(tmp_path: Path) -> None:
    source = tmp_path / "clean.py"
    source.write_text(
        "from infrastructure.secrets.client import SecretsClient\n\n"
        "def load(client: SecretsClient) -> str:\n"
        "    return client.get('BINANCE_API_KEY')\n"
    )
    assert check_file(source) == []


def test_os_environ_subscript_with_key_literal_is_flagged(tmp_path: Path) -> None:
    """The exact acceptance-criterion example: TASKS.md T-P0-09 — "A
    deliberate os.environ["BINANCE_API_KEY"] in application/ fails the CI
    check."""
    source = tmp_path / "bad_subscript.py"
    source.write_text("import os\nkey = os.environ['BINANCE_API_KEY']\n")
    violations = check_file(source)
    assert len(violations) == 1
    assert "BINANCE_API_KEY" in violations[0]
    assert "SecretsClient" in violations[0]


def test_os_environ_get_with_key_literal_is_flagged(tmp_path: Path) -> None:
    source = tmp_path / "bad_get.py"
    source.write_text("import os\nkey = os.environ.get('BINANCE_API_KEY')\n")
    violations = check_file(source)
    assert len(violations) == 1
    assert "BINANCE_API_KEY" in violations[0]


def test_bare_environ_import_form_is_also_detected(tmp_path: Path) -> None:
    source = tmp_path / "bad_bare_environ.py"
    source.write_text("from os import environ\nkey = environ['BINANCE_API_KEY']\n")
    violations = check_file(source)
    assert len(violations) == 1
    assert "BINANCE_API_KEY" in violations[0]


def test_key_detection_is_case_insensitive(tmp_path: Path) -> None:
    source = tmp_path / "lowercase_key.py"
    source.write_text("import os\nkey = os.environ['binance_api_key']\n")
    violations = check_file(source)
    assert len(violations) == 1


def test_env_var_without_key_in_its_name_is_not_flagged(tmp_path: Path) -> None:
    source = tmp_path / "not_a_secret.py"
    source.write_text("import os\nlevel = os.environ['LOG_LEVEL']\n")
    assert check_file(source) == []


def test_dynamic_key_variable_is_not_flagged(tmp_path: Path) -> None:
    """A runtime-computed key — exactly EnvSecretsClient's own pattern —
    is not a literal the checker can (or should) inspect."""
    source = tmp_path / "dynamic_key.py"
    source.write_text(
        "import os\n\n\ndef load(key: str) -> str | None:\n"
        "    return os.environ.get(key)\n"
    )
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
    (nested / "dirty.py").write_text("import os\nk = os.environ['MY_SECRET_KEY']\n")
    violations = check_paths([str(tmp_path)])
    assert len(violations) == 1
    assert "dirty.py" in violations[0]


def test_check_paths_ignores_nonexistent_path() -> None:
    assert check_paths(["/definitely/does/not/exist"]) == []


def test_files_under_infrastructure_secrets_are_exempt(tmp_path: Path) -> None:
    exempt_dir = tmp_path / "infrastructure" / "secrets"
    exempt_dir.mkdir(parents=True)
    (exempt_dir / "env.py").write_text("import os\nk = os.environ['SOME_API_KEY']\n")
    assert check_paths([str(tmp_path)]) == []


def test_files_outside_infrastructure_secrets_are_not_exempt(tmp_path: Path) -> None:
    other_dir = tmp_path / "infrastructure" / "venues"
    other_dir.mkdir(parents=True)
    (other_dir / "binance.py").write_text("import os\nk = os.environ['SOME_API_KEY']\n")
    violations = check_paths([str(tmp_path)])
    assert len(violations) == 1
