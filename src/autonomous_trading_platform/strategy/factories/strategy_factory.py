from __future__ import annotations

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy


class StrategyFactory:
    def build(self, config: StrategyConfig) -> BaseStrategy:
        if config.type == "stub":
            return StubStrategy(
                strategy_id=config.strategy_id,
                price_change_threshold=float(config.parameters.get("price_change_threshold", 0.0)),
            )

        raise ValueError(f"Unsupported strategy type: {config.type}")
