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
        lookback_bars: int = 300,
        lookahead_guard_service: LookaheadGuardService | None = None,
        dataset_version: str = "v1",
        fallback_dataset: ParquetDataset | None = None,
        fallback_dataset_version: str | None = None,
    ) -> None:
        self.market_bar_reader = market_bar_reader
        self.bars_dataset = bars_dataset
        self.lookback_bars = lookback_bars
        self.lookahead_guard_service = lookahead_guard_service or LookaheadGuardService()
        self.dataset_version = dataset_version
        self.fallback_dataset = fallback_dataset
        self.fallback_dataset_version = fallback_dataset_version

    def build(
        self,
        *,
        run_id: UUID,
        strategy_id: str,
        symbol: str,
        bar_timestamp: datetime,
        evaluation_timestamp: datetime,
    ) -> StrategyContext | None:
        """
        Live-cycle path — satisfies StrategyContextBuilderProtocol.

        Reads bars from Parquet via HistoricalBarDatasetReader and converts
        rows to MarketBar using ParquetBarRepository._row_to_market_bar,
        which handles all enum casting and Decimal conversion correctly.

        Derives a date window from bar_timestamp — assumes ~78 5-min bars
        per trading day. Returns None if fewer than lookback_bars available.
        """
        from datetime import timedelta

        from autonomous_trading_platform.storage.parquet.repositories.parquet_bar_repository import (
            ParquetBarRepository,
        )

        # For 5-min bars (~78/day): lookback_bars // 78 days + buffer.
        # For daily bars (lookback_bars <= 78): multiply by 2 and add holiday buffer
        # so that a 31-bar warmup covers ~62+ calendar days (~44 trading days).
        if self.lookback_bars <= 78:
            lookback_days = self.lookback_bars * 2 + 14
        else:
            lookback_days = max(self.lookback_bars // 78 + 5, 10)
        start_date = (bar_timestamp - timedelta(days=lookback_days)).date()
        end_date = bar_timestamp.date()

        table = self.market_bar_reader.read(
            dataset=self.bars_dataset,
            dataset_version=self.dataset_version,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        if table.num_rows == 0 and self.fallback_dataset is not None:
            table = self.market_bar_reader.read(
                dataset=self.fallback_dataset,
                dataset_version=self.fallback_dataset_version or self.dataset_version,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )

        if table.num_rows == 0:
            return None

        bars = [
            ParquetBarRepository._row_to_market_bar(row)
            for row in table.to_pylist()
            if row["timestamp"] < bar_timestamp
        ]

        if len(bars) < self.lookback_bars:
            return None

        context_bars = bars[-self.lookback_bars :]

        self.lookahead_guard_service.assert_historical_only(
            symbol=symbol,
            simulation_timestamp=evaluation_timestamp,
            bars=context_bars,
        )

        return StrategyContext(
            run_id=run_id,
            strategy_id=strategy_id,
            symbol=symbol,
            bar_timestamp=bar_timestamp,
            evaluation_timestamp=evaluation_timestamp,
            bars=context_bars,
        )

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
        """
        Simulation path — reads bars from a pre-loaded SimulationWindowData
        rather than hitting Parquet on every call.

        Uses bisect on the precomputed sorted timestamp list for O(log N)
        lookups instead of a full O(N) scan per call.
        """
        import bisect

        symbol_bars = window.bars_by_symbol.get(symbol, [])
        if not symbol_bars:
            return None

        # O(log N) binary search using the precomputed sorted timestamp index.
        # Falls back to O(N) linear scan when the index isn't available (e.g.
        # legacy test mocks that don't populate sorted_timestamps_by_symbol).
        ts_index = getattr(window, "sorted_timestamps_by_symbol", None)
        if ts_index is not None:
            symbol_ts = ts_index.get(symbol, [])
            idx = bisect.bisect_left(symbol_ts, timestamp)
        else:
            idx = sum(1 for b in symbol_bars if b.timestamp < timestamp)

        if idx < self.lookback_bars:
            return None

        context_bars = symbol_bars[idx - self.lookback_bars : idx]

        self.lookahead_guard_service.assert_historical_only(
            symbol=symbol,
            simulation_timestamp=timestamp,
            bars=context_bars,
        )
        feature_tables_by_symbol = getattr(window, "feature_tables_by_symbol", {})

        return StrategyContext(
            run_id=run_id,
            strategy_id=strategy_id,
            symbol=symbol,
            bar_timestamp=timestamp,
            evaluation_timestamp=timestamp,
            bars=context_bars,
            features=feature_tables_by_symbol.get(symbol, {}),
        )
