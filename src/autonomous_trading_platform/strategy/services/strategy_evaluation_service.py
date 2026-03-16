from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autonomous_trading_platform.contracts.trading.signal import Signal


@dataclass(frozen=True)
class StrategyEvaluationResult:
    strategy_id: str
    bar_timestamp: datetime
    signals: list[Signal]


class MarketBarReaderProtocol:
    def get_bars_up_to_timestamp(
        self,
        symbol: str,
        end_timestamp: datetime,
        lookback_bars: int,
    ) -> list[Any]:
        raise NotImplementedError


class UniverseMembershipReaderProtocol:
    def get_symbols_for_timestamp(self, as_of: datetime) -> list[str]:
        raise NotImplementedError


class StrategyModuleProtocol:
    strategy_id: str

    def evaluate_symbol(
        self,
        symbol: str,
        bars: list[Any],
        bar_timestamp: datetime,
    ) -> Signal | None:
        raise NotImplementedError


class StrategyEvaluationService:
    def __init__(
        self,
        market_bar_reader: MarketBarReaderProtocol,
        universe_reader: UniverseMembershipReaderProtocol,
        strategy_module: StrategyModuleProtocol,
        lookback_bars: int = 20,
    ) -> None:
        self.market_bar_reader = market_bar_reader
        self.universe_reader = universe_reader
        self.strategy_module = strategy_module
        self.lookback_bars = lookback_bars

    def evaluate(self, bar_timestamp: datetime) -> StrategyEvaluationResult:
        symbols = self.universe_reader.get_symbols_for_timestamp(bar_timestamp)
        signals: list[Signal] = []

        for symbol in symbols:
            bars = self.market_bar_reader.get_bars_up_to_timestamp(
                symbol=symbol,
                end_timestamp=bar_timestamp,
                lookback_bars=self.lookback_bars,
            )

            if len(bars) < self.lookback_bars:
                continue

            signal = self.strategy_module.evaluate_symbol(
                symbol=symbol,
                bars=bars,
                bar_timestamp=bar_timestamp,
            )
            if signal is not None:
                signals.append(signal)

        return StrategyEvaluationResult(
            strategy_id=self.strategy_module.strategy_id,
            bar_timestamp=bar_timestamp,
            signals=signals,
        )
