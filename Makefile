# QuantTrade Platform — developer entry points.
#
# A new engineer: `make install` then `make test`. That's the whole setup.

VENV_DIR := .venv

ifeq ($(OS),Windows_NT)
	VENV_BIN   := $(VENV_DIR)/Scripts
	PY_BOOT    := py -3
	EXE        := .exe
else
	VENV_BIN   := $(VENV_DIR)/bin
	PY_BOOT    := python3
	EXE        :=
endif

PYTHON      := $(VENV_BIN)/python$(EXE)
RUFF        := $(VENV_BIN)/ruff$(EXE)
MYPY        := $(VENV_BIN)/mypy$(EXE)
PYTEST      := $(VENV_BIN)/pytest$(EXE)
LINT_IMPORTS := $(VENV_BIN)/lint-imports$(EXE)

.PHONY: venv install lint typecheck test check clean dev-up dev-down

## Create the virtual environment if it doesn't already exist.
venv:
	$(PY_BOOT) -m venv $(VENV_DIR)

## Create the venv (if needed) and install the project in editable mode with dev tools.
install: venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

## Static analysis: style/correctness (ruff) + architectural layering (import-linter)
## + the float ban on domain/ and application/ (ARCHITECTURE.md §3.5) + the
## os.environ secrets ban outside infrastructure/secrets/ (ARCHITECTURE.md §3.6).
lint:
	$(RUFF) check .
	$(LINT_IMPORTS)
	$(PYTHON) scripts/check_no_float.py domain application
	$(PYTHON) scripts/check_no_env_secrets.py domain application infrastructure

## Strict static typing on domain/ and application/ (see pyproject.toml).
typecheck:
	$(MYPY)

## Run the test suite.
test:
	$(PYTEST)

## Everything CI runs, in one target.
check: lint typecheck test

## Remove the venv and all tool caches.
clean:
	rm -rf $(VENV_DIR) .mypy_cache .pytest_cache .ruff_cache *.egg-info

## Start the local dev infrastructure (Postgres+Timescale, Redis, Grafana,
## a test-only Postgres) and block until every service reports healthy.
dev-up:
	docker compose up -d --wait

## Stop the local dev infrastructure and remove its volumes — a clean slate.
dev-down:
	docker compose down --volumes
