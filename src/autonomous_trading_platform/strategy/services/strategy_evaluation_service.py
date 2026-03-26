from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from autonomous_trading_platform.contracts.trading.signal import Signal

from ..contracts.strategy_context import StrategyContext
from ..contracts.strategy_evaluation_result import StrategyEvaluationResult
from ..implementations.base_strategy import BaseStrategy


class MarketBarReaderProtocol(Protocol):
    def get_bars_up_to_timestamp(
        self,
        symbol: str,
        end_timestamp: datetime,
        lookback_bars: int,
    ) -> list[Any]: ...


class UniverseMembershipReaderProtocol(Protocol):
    def get_symbols_for_timestamp(self, as_of: datetime) -> list[str]: ...


class StrategyProtocol(Protocol):
    strategy_id: str

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None: ...


class StrategyEvaluationService:
    def __init__(
        self,
        market_bar_reader: MarketBarReaderProtocol,
        universe_reader: UniverseMembershipReaderProtocol,
        strategy: BaseStrategy,
        lookback_bars: int = 20,
    ) -> None:
        self.market_bar_reader = market_bar_reader
        self.universe_reader = universe_reader
        self.strategy = strategy
        self.lookback_bars = lookback_bars

    def evaluate(
        self,
        bar_timestamp: datetime,
        run_id: UUID,
        evaluation_timestamp: datetime,
    ) -> StrategyEvaluationResult:
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

            context = StrategyContext(
                run_id=run_id,
                strategy_id=self.strategy.strategy_id,
                symbol=symbol,
                bar_timestamp=bar_timestamp,
                evaluation_timestamp=evaluation_timestamp,
                bars=bars,
            )

            signal = self.strategy.evaluate_symbol(context)

            if signal is not None:
                signals.append(signal)

        return StrategyEvaluationResult(
            strategy_id=self.strategy.strategy_id,
            bar_timestamp=bar_timestamp,
            signals=signals,
        )
