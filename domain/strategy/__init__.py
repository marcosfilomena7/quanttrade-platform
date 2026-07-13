"""Strategy port and registry (TASKS.md T-P2-07).

Re-exports the public surface of `domain/strategy/`: the `Strategy` ABC
every strategy implements, its supporting types (`ParamSpec`,
`StrategyContext`, `InvalidStrategyParams`, `validate_params`), and
`StrategyRegistry`, which discovers, validates, and instantiates
concrete `Strategy` subclasses from configured modules.
"""

from __future__ import annotations

from domain.strategy.registry import StrategyRegistry
from domain.strategy.strategy import (
    InvalidStrategyParams,
    ParamSpec,
    Strategy,
    StrategyContext,
    validate_params,
)

__all__ = [
    "InvalidStrategyParams",
    "ParamSpec",
    "Strategy",
    "StrategyContext",
    "StrategyRegistry",
    "validate_params",
]
