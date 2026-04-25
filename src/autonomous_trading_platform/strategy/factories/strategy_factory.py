from __future__ import annotations

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.factor_based_strategy import (
    FactorBasedStrategy,
)
from autonomous_trading_platform.strategy.implementations.mean_reversion_strategy import (
    MeanReversionStrategy,
)
from autonomous_trading_platform.strategy.implementations.momentum_strategy import (
    MomentumStrategy,
)
from autonomous_trading_platform.strategy.implementations.moving_average_crossover_strategy import (
    MovingAverageCrossoverStrategy,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy


class StrategyFactory:
    def build(self, config: StrategyConfig) -> BaseStrategy:
        if config.type == "stub":
            return StubStrategy(
                strategy_id=config.strategy_id,
                price_change_threshold=float(config.parameters.get("price_change_threshold", 0.0)),
            )

        if config.type == "moving_average_crossover":
            return MovingAverageCrossoverStrategy(
                strategy_id=config.strategy_id,
                short_window=int(config.parameters.get("short_window", 10)),
                long_window=int(config.parameters.get("long_window", 30)),
            )

        if config.type == "momentum":
            return MomentumStrategy(
                strategy_id=config.strategy_id,
                lookback=int(config.parameters.get("lookback", 5)),
                buy_above=float(config.parameters.get("buy_above", 0.0)),
                sell_below=float(config.parameters.get("sell_below", 0.0)),
            )

        if config.type == "mean_reversion":
            return MeanReversionStrategy(
                strategy_id=config.strategy_id,
                window=int(config.parameters.get("window", 20)),
                buy_below_z=float(config.parameters.get("buy_below_z", -2.0)),
                sell_above_z=float(config.parameters.get("sell_above_z", 2.0)),
            )

        if config.type == "factor_based":
            return FactorBasedStrategy(
                strategy_id=config.strategy_id,
                momentum_lookback=int(config.parameters.get("momentum_lookback", 5)),
                mean_reversion_window=int(config.parameters.get("mean_reversion_window", 20)),
                volatility_window=int(config.parameters.get("volatility_window", 20)),
                volume_window=int(config.parameters.get("volume_window", 20)),
                buy_score_threshold=float(config.parameters.get("buy_score_threshold", 0.5)),
                sell_score_threshold=float(config.parameters.get("sell_score_threshold", -0.5)),
                momentum_weight=float(config.parameters.get("momentum_weight", 0.4)),
                mean_reversion_weight=float(config.parameters.get("mean_reversion_weight", 0.3)),
                volume_weight=float(config.parameters.get("volume_weight", 0.2)),
                volatility_weight=float(config.parameters.get("volatility_weight", 0.1)),
            )

        raise ValueError(f"Unsupported strategy type: {config.type}")
