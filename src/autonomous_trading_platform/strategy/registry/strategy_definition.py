"""StrategyDefinition: canonical metadata model for a registered strategy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .parameter_metadata import ParameterSpec
from .strategy_family import StrategyFamily

if TYPE_CHECKING:
    pass


@dataclass
class StrategyDefinition:
    """Complete metadata record for one registered strategy type.

    Fields are logically grouped:
    - Core identity: type, name, description, family, class
    - Classification: debug / production_ready flags
    - Parameter metadata: defaults, validator, typed specs
    - Warmup/dependency: warmup_bars_fn, required_indicators, required_persisted_features
    - Generation metadata: parameter_specs with search-space ranges
    - Compatibility: price basis, execution modes
    - Operational: determinism, builder function
    """

    # -- Core identity --
    strategy_type: str
    display_name: str
    description: str
    family: StrategyFamily
    implementation_class: type  # type[BaseStrategy]

    # -- Classification --
    debug: bool
    production_ready: bool

    # -- Parameter metadata --
    default_parameters: dict[str, Any]
    parameter_validator: Callable[[dict[str, Any]], None]

    # -- Warmup/dependency metadata --
    warmup_bars_fn: Callable[[dict[str, Any]], int]
    required_indicators: tuple[str, ...]
    required_persisted_features: tuple[str, ...]

    # -- Generation / search-space metadata --
    parameter_specs: tuple[ParameterSpec, ...]

    # -- Builder: (strategy_id, parameters) -> BaseStrategy --
    builder: Callable[..., Any]

    # -- Compatibility metadata --
    supports_long_only: bool = True
    supports_shorting: bool = True
    supports_intraday: bool = True
    supports_daily: bool = True
    supports_adjusted_prices: bool = True
    supports_raw_prices: bool = True

    # -- Operational metadata --
    deterministic: bool = True

    def compute_warmup_bars(self, parameters: dict[str, Any] | None = None) -> int:
        """Return warmup bars required for the given parameters.

        Falls back to default_parameters when *parameters* is None.
        """
        params = parameters if parameters is not None else self.default_parameters
        return self.warmup_bars_fn(params)
