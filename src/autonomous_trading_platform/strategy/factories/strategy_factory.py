from __future__ import annotations

from typing import cast

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.registry import get_registry


class StrategyFactory:
    def build(self, config: StrategyConfig) -> BaseStrategy:
        defn = get_registry().get_definition(config.type)
        parameters = defn.normalize_parameters(config.parameters)
        return cast(BaseStrategy, defn.builder(config.strategy_id, parameters))
