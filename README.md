# QuantTrade Platform

Institutional systematic trading platform. Internal monorepo.

Architecture, database design, and the full implementation task registry
are the source of truth and live under [`docs/`](docs/):

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/DATABASE.md`](docs/DATABASE.md)
- [`docs/TASKS.md`](docs/TASKS.md)

## Layout

```
domain/          Pure business logic. Zero dependencies on anything below.
application/     Use cases, orchestrators, ports. Depends on domain only.
infrastructure/  Adapters (DB, venues, secrets, ...). Implements application ports.
tests/           Test suite (mirrors the three layers above).
```

The dependency rule `domain <- application <- infrastructure` is enforced
mechanically by `import-linter` — a violation fails `make lint`, not just
code review.

## Setup

Requires Python 3.12+.

```
make install
make test
```

That's the entire setup. `make install` creates `.venv`, installs the
project in editable mode, and installs the dev toolchain (ruff, mypy,
pytest, import-linter).

## Day-to-day

```
make lint        # ruff + import-linter (architectural layering)
make typecheck    # mypy --strict on domain/ and application/
make test         # pytest
make check        # all three, in order — what CI runs
```
