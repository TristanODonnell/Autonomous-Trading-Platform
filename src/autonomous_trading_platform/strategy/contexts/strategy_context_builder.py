from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext


class MarketBarReaderProtocol(Protocol):
    def get_bars_up_to_timestamp(
        self,
        symbol: str,
        end_timestamp: datetime,
        lookback_bars: int,
    ) -> list[Any]: ...


class StrategyContextBuilder:
    def __init__(
        self,
        *,
        market_bar_reader: MarketBarReaderProtocol,
        lookback_bars: int = 20,
    ) -> None:
        self.market_bar_reader = market_bar_reader
        self.lookback_bars = lookback_bars

    def build(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        symbol: str,
        bar_timestamp: datetime,
        evaluation_timestamp: datetime,
    ) -> StrategyContext | None:
        bars = self.market_bar_reader.get_bars_up_to_timestamp(
            symbol=symbol,
            end_timestamp=bar_timestamp,
            lookback_bars=self.lookback_bars,
        )

        if len(bars) < self.lookback_bars:
            return None

        return StrategyContext(
            run_id=run_id,
            strategy_id=strategy_id,
            symbol=symbol,
            bar_timestamp=bar_timestamp,
            evaluation_timestamp=evaluation_timestamp,
            bars=bars,
        )
