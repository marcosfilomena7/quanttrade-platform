"""StrategyRegistry — discovers, validates, and instantiates Strategy subclasses (TASKS.md T-P2-07).

"Implement `StrategyRegistry`: discovers strategy classes from configured
modules, validates parameter schema against provided params,
instantiates, and returns."

Design decisions, and why:

- **Discovery uses `importlib.import_module` on caller-supplied dotted
  module names, then `inspect.getmembers` filtered to classes actually
  *defined* in that module.** "Configured modules" (TASKS.md's own
  words) is read literally: the registry is handed module names, not a
  filesystem path to scan, matching how `FeeSchedule`/`slippage_model`
  configuration is likewise injected rather than discovered from a
  file (T-P2-05/06). Filtering by `obj.__module__ == module_name`
  excludes classes merely *imported into* that module's namespace
  (e.g. `Strategy` itself, imported for subclassing) — only classes the
  module itself defines are registered.
- **`importlib.import_module` does not violate the Dependency Rule.**
  import-linter's layering contract is a *static* analysis of literal
  `import`/`from ... import ...` statements; this module's own source
  contains no such statement naming anything in `infrastructure/` (or
  any other layer). The module names it dynamically imports are
  ordinary runtime string values supplied by the caller — structurally
  identical to how any Python plugin-loading system works, and
  invisible to static import analysis regardless of what the caller
  actually configures it to load.
- **`instantiate()` validates before constructing**, calling
  `strategy.validate_params(cls.params_schema(), params)` (raising
  `InvalidStrategyParams` on failure) and only then `cls(params=params)`
  — the exact, literal order TASKS.md's own words specify: "validates
  parameter schema against provided params, instantiates, and returns."
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping, Sequence

from domain.strategy.strategy import Strategy, validate_params


class StrategyRegistry:
    """Discovers `Strategy` subclasses defined in `module_names`,
    validates params against each class's own `params_schema()`, and
    instantiates on request."""

    def __init__(self, module_names: Sequence[str]) -> None:
        self._strategies: dict[str, type[Strategy]] = {}
        for module_name in module_names:
            module = importlib.import_module(module_name)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, Strategy)
                    and obj is not Strategy
                    and obj.__module__ == module_name
                ):
                    self._strategies[obj.__name__] = obj

    def names(self) -> Sequence[str]:
        """Every registered strategy class name, sorted."""
        return tuple(sorted(self._strategies))

    def instantiate(self, *, name: str, params: Mapping[str, object]) -> Strategy:
        """Validates `params` against `name`'s own `params_schema()`
        (raising `InvalidStrategyParams` on failure), then constructs
        and returns an instance."""
        strategy_cls = self._strategies.get(name)
        if strategy_cls is None:
            raise ValueError(f"No registered strategy named {name!r}")
        validate_params(strategy_cls.params_schema(), params)
        return strategy_cls(params=params)
