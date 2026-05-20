from __future__ import annotations

from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
)
from autonomous_trading_platform.strategy.registry import StrategyDefinition


def strategy_is_generation_compatible(
    defn: StrategyDefinition,
    options: GenerationOptions,
) -> tuple[bool, str | None]:
    if defn.debug and not options.include_debug:
        return False, "debug_excluded"
    if not defn.production_ready and not options.include_experimental:
        return False, "experimental_excluded"
    if options.allowed_families and defn.family.value not in options.allowed_families:
        return False, "family_not_allowed"
    if options.excluded_families and defn.family.value in options.excluded_families:
        return False, "family_excluded"
    if options.allowed_strategy_types and defn.strategy_type not in options.allowed_strategy_types:
        return False, "strategy_type_not_allowed"
    if options.excluded_strategy_types and defn.strategy_type in options.excluded_strategy_types:
        return False, "strategy_type_excluded"
    if options.execution_mode == "intraday" and not defn.supports_intraday:
        return False, "intraday_not_supported"
    if options.execution_mode == "daily" and not defn.supports_daily:
        return False, "daily_not_supported"
    if options.price_basis == "raw" and not defn.supports_raw_prices:
        return False, "raw_prices_not_supported"
    if options.price_basis == "adjusted" and not defn.supports_adjusted_prices:
        return False, "adjusted_prices_not_supported"
    return True, None
