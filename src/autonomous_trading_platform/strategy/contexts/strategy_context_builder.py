from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowData,
)
from autonomous_trading_platform.storage.parquet.datasets import ParquetDataset
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext


class StrategyContextBuilder:
    def __init__(
        self,
        *,
        market_bar_reader: HistoricalBarDatasetReader,
        bars_dataset: ParquetDataset,
        lookback_bars: int = 20,
        lookahead_guard_service: LookaheadGuardService | None = None,
    ) -> None:
        self.market_bar_reader = market_bar_reader
        self.bars_dataset = bars_dataset
        self.lookback_bars = lookback_bars
        self.lookahead_guard_service = lookahead_guard_service or LookaheadGuardService()

    def build_from_window(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        symbol: str,
        timestamp: datetime,
        window: SimulationWindowData,
        positions: dict[str, int],
        state: dict[str, Any],
    ) -> StrategyContext | None:
        symbol_bars = window.bars_by_symbol.get(symbol, [])

        bars_up_to = sorted(
            [b for b in symbol_bars if b.timestamp < timestamp],
            key=lambda b: b.timestamp,
        )

        if len(bars_up_to) < self.lookback_bars:
            return None

        context_bars = bars_up_to[-self.lookback_bars :]

        self.lookahead_guard_service.assert_historical_only(
            symbol=symbol,
            simulation_timestamp=timestamp,
            bars=context_bars,
        )

        return StrategyContext(
            run_id=run_id,
            strategy_id=strategy_id,
            symbol=symbol,
            bar_timestamp=timestamp,
            evaluation_timestamp=timestamp,
            bars=context_bars,
        )
