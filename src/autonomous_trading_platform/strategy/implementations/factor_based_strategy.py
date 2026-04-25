from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.base_strategy_helpers import (
    build_signal_id,
    extract_closes,
    extract_volumes,
)
from autonomous_trading_platform.strategy.indicators.mean_reversion import z_score
from autonomous_trading_platform.strategy.indicators.momentum import momentum
from autonomous_trading_platform.strategy.indicators.volatility import rolling_standard_deviation
from autonomous_trading_platform.strategy.indicators.volume import volume_ratio


class FactorBasedStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str,
        momentum_lookback: int = 5,
        mean_reversion_window: int = 20,
        volatility_window: int = 20,
        volume_window: int = 20,
        buy_score_threshold: float = 0.5,
        sell_score_threshold: float = -0.5,
        momentum_weight: float = 0.4,
        mean_reversion_weight: float = 0.3,
        volume_weight: float = 0.2,
        volatility_weight: float = 0.1,
    ) -> None:
        if momentum_lookback <= 0:
            raise ValueError("momentum_lookback must be positive")
        if mean_reversion_window <= 0:
            raise ValueError("mean_reversion_window must be positive")
        if volatility_window <= 0:
            raise ValueError("volatility_window must be positive")
        if volume_window <= 0:
            raise ValueError("volume_window must be positive")
        if sell_score_threshold >= buy_score_threshold:
            raise ValueError("sell_score_threshold must be less than buy_score_threshold")

        self._strategy_id = strategy_id
        self.momentum_lookback = momentum_lookback
        self.mean_reversion_window = mean_reversion_window
        self.volatility_window = volatility_window
        self.volume_window = volume_window
        self.buy_score_threshold = buy_score_threshold
        self.sell_score_threshold = sell_score_threshold
        self.momentum_weight = momentum_weight
        self.mean_reversion_weight = mean_reversion_weight
        self.volume_weight = volume_weight
        self.volatility_weight = volatility_weight

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        closes = extract_closes(context.bars)
        volumes = extract_volumes(context.bars)

        momentum_value = momentum(closes, self.momentum_lookback)
        z_value = z_score(closes, self.mean_reversion_window)
        volatility_value = rolling_standard_deviation(closes, self.volatility_window)
        volume_value = volume_ratio(volumes, self.volume_window)

        if (
            momentum_value is None
            or z_value is None
            or volatility_value is None
            or volume_value is None
        ):
            return None

        momentum_score = self._normalize_signed(momentum_value)
        mean_reversion_score = self._score_mean_reversion(z_value)
        volume_score = self._score_volume(volume_value)
        volatility_score = self._score_volatility(volatility_value)

        weighted_score = (
            momentum_score * self.momentum_weight
            + mean_reversion_score * self.mean_reversion_weight
            + volume_score * self.volume_weight
            + volatility_score * self.volatility_weight
        )

        if weighted_score >= self.buy_score_threshold:
            direction = SignalDirection.BUY
        elif weighted_score <= self.sell_score_threshold:
            direction = SignalDirection.SELL
        else:
            return None

        confidence = min(abs(weighted_score), 1.0)

        params = {
            "strategy_type": "factor_based",
            "momentum_lookback": self.momentum_lookback,
            "mean_reversion_window": self.mean_reversion_window,
            "volatility_window": self.volatility_window,
            "volume_window": self.volume_window,
            "momentum_value": momentum_value,
            "z_score": z_value,
            "volatility": volatility_value,
            "volume_ratio": volume_value,
            "momentum_score": momentum_score,
            "mean_reversion_score": mean_reversion_score,
            "volume_score": volume_score,
            "volatility_score": volatility_score,
            "weighted_score": weighted_score,
            "buy_score_threshold": self.buy_score_threshold,
            "sell_score_threshold": self.sell_score_threshold,
        }

        return Signal(
            signal_id=build_signal_id(
                run_id=context.run_id,
                strategy_id=context.strategy_id,
                symbol=context.symbol,
                bar_timestamp=context.bar_timestamp,
                direction=direction,
                params=params,
            ),
            run_id=context.run_id,
            timestamp=context.evaluation_timestamp,
            bar_timestamp=context.bar_timestamp,
            strategy_id=context.strategy_id,
            symbol=context.symbol,
            direction=direction,
            confidence=confidence,
            params=params,
        )

    @staticmethod
    def _normalize_signed(value: float) -> float:
        if value > 0:
            return 1.0
        if value < 0:
            return -1.0
        return 0.0

    @staticmethod
    def _score_mean_reversion(z_value: float) -> float:
        if z_value <= -2.0:
            return 1.0
        if z_value >= 2.0:
            return -1.0
        return 0.0

    @staticmethod
    def _score_volume(volume_value: float) -> float:
        if volume_value >= 1.5:
            return 1.0
        if volume_value <= 0.5:
            return -0.5
        return 0.0

    @staticmethod
    def _score_volatility(volatility_value: float) -> float:
        if volatility_value <= 0:
            return 0.0

        return -0.25
