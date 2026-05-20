"""Canonical strategy catalog.

Single source of truth for all supported strategy types. StrategyFactory,
StrategyConfig, and the CLI all derive their type lists from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyCatalogEntry:
    strategy_type: str
    display_name: str
    family: str
    debug: bool
    default_parameters: dict[str, Any]
    description: str


_CATALOG: list[StrategyCatalogEntry] = [
    # ------------------------------------------------------------------
    # Debug / test strategies
    # ------------------------------------------------------------------
    StrategyCatalogEntry(
        strategy_type="stub",
        display_name="Stub",
        family="debug",
        debug=True,
        default_parameters={"price_change_threshold": 0.0},
        description="Minimal placeholder that validates the strategy architecture.",
    ),
    StrategyCatalogEntry(
        strategy_type="intentional_loser",
        display_name="Intentional Loser",
        family="debug",
        debug=True,
        default_parameters={"price_change_threshold": 0.0},
        description="Always trades against the signal; used to confirm negative-edge detection.",
    ),
    StrategyCatalogEntry(
        strategy_type="random",
        display_name="Random",
        family="debug",
        debug=True,
        default_parameters={
            "signal_probability": 0.33,
            "buy_probability": 0.5,
        },
        description="Random baseline; expected edge ≈ 0 before costs.",
    ),
    # ------------------------------------------------------------------
    # Production strategies
    # ------------------------------------------------------------------
    StrategyCatalogEntry(
        strategy_type="moving_average_crossover",
        display_name="Moving Average Crossover",
        family="trend",
        debug=False,
        default_parameters={"short_window": 10, "long_window": 30},
        description="Buys when the short MA crosses above the long MA, sells on the reverse.",
    ),
    StrategyCatalogEntry(
        strategy_type="momentum",
        display_name="Momentum",
        family="trend",
        debug=False,
        default_parameters={"lookback": 5, "buy_above": 0.0, "sell_below": 0.0},
        description="Buys on positive n-period return, sells on negative return.",
    ),
    StrategyCatalogEntry(
        strategy_type="mean_reversion",
        display_name="Mean Reversion",
        family="mean_reversion",
        debug=False,
        default_parameters={"window": 20, "buy_below_z": -2.0, "sell_above_z": 2.0},
        description="Buys when price is two standard deviations below the rolling mean.",
    ),
    StrategyCatalogEntry(
        strategy_type="factor_based",
        display_name="Factor Based",
        family="factor",
        debug=False,
        default_parameters={
            "momentum_lookback": 5,
            "mean_reversion_window": 20,
            "volatility_window": 20,
            "volume_window": 20,
            "buy_score_threshold": 0.5,
            "sell_score_threshold": -0.5,
            "momentum_weight": 0.4,
            "mean_reversion_weight": 0.3,
            "volume_weight": 0.2,
            "volatility_weight": 0.1,
        },
        description="Scores each bar across momentum, mean-reversion, volume, and volatility factors.",
    ),
]

_BY_TYPE: dict[str, StrategyCatalogEntry] = {e.strategy_type: e for e in _CATALOG}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def list_strategy_types() -> list[str]:
    """All cataloged strategy type strings."""
    return [e.strategy_type for e in _CATALOG]


def list_production_strategy_types() -> list[str]:
    """Strategy types that are not flagged as debug."""
    return [e.strategy_type for e in _CATALOG if not e.debug]


def list_debug_strategy_types() -> list[str]:
    """Strategy types flagged as debug/test-only."""
    return [e.strategy_type for e in _CATALOG if e.debug]


def get_strategy_definition(strategy_type: str) -> StrategyCatalogEntry:
    """Return the catalog entry for *strategy_type*.

    Raises KeyError if the type is not cataloged.
    """
    try:
        return _BY_TYPE[strategy_type]
    except KeyError:
        raise KeyError(f"Unknown strategy type: {strategy_type!r}") from None


def strategy_type_exists(strategy_type: str) -> bool:
    """True if *strategy_type* is present in the catalog."""
    return strategy_type in _BY_TYPE
