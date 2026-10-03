from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.compute as pc

from autonomous_trading_platform.accounting.corporate_actions import (
    SplitSource,
    adjust_bars_for_splits,
    split_factor_for,
)
from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowData,
)
from autonomous_trading_platform.storage.parquet.datasets import ParquetDataset
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext


def _arrow_ts(value: datetime, table: pa.Table) -> pa.Scalar:
    """value as a scalar of the table's timestamp type (tz-aware comparisons in Arrow)."""
    return pa.scalar(value, type=table.schema.field("timestamp").type)


DatasetVersionResolver = Callable[[date, date], list[str]]


class StrategyContextBuilder:
    """Builds the bar history a strategy evaluates on.

    Bars are read raw and, when a ``split_source`` is given, every bar before a
    split's ex-date (ex-date on or before the evaluation date — never a future split)
    is expressed in post-split terms, so live, backtests and research all see the same
    continuous history while fills stay at raw prices (plan 5d, decision D5).

    ``dataset_version_resolver`` maps a date window to the dataset versions holding
    it (live ingestion writes one version per trading day); without it the single
    ``dataset_version`` is read.
    """

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
        dataset_version_resolver: DatasetVersionResolver | None = None,
        split_source: SplitSource | None = None,
    ) -> None:
        self.market_bar_reader = market_bar_reader
        self.bars_dataset = bars_dataset
        self.lookback_bars = lookback_bars
        self.lookahead_guard_service = lookahead_guard_service or LookaheadGuardService()
        self.dataset_version = dataset_version
        self.fallback_dataset = fallback_dataset
        self.fallback_dataset_version = fallback_dataset_version
        self.dataset_version_resolver = dataset_version_resolver
        self.split_source = split_source

    def with_lookback(self, lookback_bars: int) -> StrategyContextBuilder:
        """A copy that hands strategies exactly lookback_bars bars.

        Research runs use the strategy's registry warmup, the same count the trading
        cycle uses, so both paths evaluate a strategy on identical bars.
        """
        builder = copy.copy(self)
        builder.lookback_bars = lookback_bars
        return builder

    def with_split_source(self, split_source: SplitSource | None) -> StrategyContextBuilder:
        """A copy that adjusts history with these splits (research windows load theirs)."""
        builder = copy.copy(self)
        builder.split_source = split_source
        return builder

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def lookback_window(self, bar_timestamp: datetime) -> tuple[date, date]:
        """Calendar window read for ``lookback_bars`` bars ending at ``bar_timestamp``.

        For 5-min bars (~78/day): lookback_bars // 78 days + buffer. For short lookbacks
        (<= 78): twice the count plus a holiday buffer, so a 31-bar warmup covers ~62
        calendar days (~44 trading days).
        """
        if self.lookback_bars <= 78:
            lookback_days = self.lookback_bars * 2 + 14
        else:
            lookback_days = max(self.lookback_bars // 78 + 5, 10)
        return (bar_timestamp - timedelta(days=lookback_days)).date(), bar_timestamp.date()

    def dataset_versions_for(self, start_date: date, end_date: date) -> list[str]:
        if self.dataset_version_resolver is not None:
            resolved = self.dataset_version_resolver(start_date, end_date)
            if resolved:
                return list(resolved)
        return [self.dataset_version]

    def read_bars(self, *, symbol: str, start_date: date, end_date: date) -> pa.Table:
        """Raw bars for ``symbol`` in the window across every dataset version holding
        it, sorted by timestamp with duplicate timestamps collapsed (last version wins).
        Falls back to ``fallback_dataset`` when the primary read is empty."""
        tables = [
            self.market_bar_reader.read(
                dataset=self.bars_dataset,
                dataset_version=version,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            for version in self.dataset_versions_for(start_date, end_date)
        ]
        tables = [t for t in tables if t.num_rows > 0]
        if not tables and self.fallback_dataset is not None:
            fallback = self.market_bar_reader.read(
                dataset=self.fallback_dataset,
                dataset_version=self.fallback_dataset_version or self.dataset_version,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            if fallback.num_rows > 0:
                tables = [fallback]
        if not tables:
            return pa.table({})
        if len(tables) == 1:
            return tables[0]
        return _merge_by_timestamp(tables)

    def splits_for(self, symbol: str, start_date: date, end_date: date) -> list[Any]:
        if self.split_source is None:
            return []
        return self.split_source.splits_for(symbol, start_date, end_date)

    def recent_closes(
        self, *, symbol: str, before: datetime, lookback_bars: int, days: int = 5
    ) -> list[float]:
        """The last ``lookback_bars`` closes strictly before ``before``, split-adjusted
        to the share terms of ``before``'s date (for volatility scaling)."""
        start_date = (before - timedelta(days=days)).date()
        end_date = before.date()
        table = self.read_bars(symbol=symbol, start_date=start_date, end_date=end_date)
        if table.num_rows == 0:
            return []
        ts = table["timestamp"]
        prior = table.filter(pc.less(ts, pa.scalar(before, type=ts.type)))
        if prior.num_rows == 0:
            return []
        tail = prior.slice(max(prior.num_rows - lookback_bars, 0))
        closes = tail.column("close").to_pylist()
        splits = self.splits_for(symbol, start_date, end_date)
        if not splits:
            return [float(c) for c in closes]
        stamps = tail.column("timestamp").to_pylist()
        return [
            float(close) * float(split_factor_for(splits, bar_date=stamp.date(), as_of=end_date))
            for close, stamp in zip(closes, stamps, strict=True)
        ]

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
        from autonomous_trading_platform.storage.parquet.repositories.parquet_bar_repository import (
            ParquetBarRepository,
        )

        start_date, end_date = self.lookback_window(bar_timestamp)
        table = self.read_bars(symbol=symbol, start_date=start_date, end_date=end_date)

        if table.num_rows == 0:
            return None

        # Filter and slice in Arrow, then convert only the bars handed to the strategy:
        # the date window holds far more rows than a short lookback needs (a 5-bar
        # strategy reads ~24 days), and per-row MarketBar conversion dominated cycle time.
        before = table.filter(pc.less(table["timestamp"], _arrow_ts(bar_timestamp, table)))
        if before.num_rows < self.lookback_bars:
            return None

        context_bars = [
            ParquetBarRepository._row_to_market_bar(row)
            for row in before.slice(before.num_rows - self.lookback_bars).to_pylist()
        ]
        context_bars = self._split_adjusted(context_bars, symbol, start_date, bar_timestamp)

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
        if context_bars and self.split_source is not None:
            context_bars = self._split_adjusted(
                context_bars, symbol, context_bars[0].timestamp.date(), timestamp
            )

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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _split_adjusted(
        self, bars: list[Any], symbol: str, start_date: date, as_of: datetime
    ) -> list[Any]:
        if self.split_source is None or not bars:
            return bars
        splits = self.splits_for(symbol, start_date, as_of.date())
        if not splits:
            return bars
        return adjust_bars_for_splits(bars, splits, as_of=as_of.date())


def _merge_by_timestamp(tables: list[pa.Table]) -> pa.Table:
    """Concatenate bar tables, sort by timestamp and keep the last row per timestamp
    (a later dataset version re-ingesting a day replaces the earlier one)."""
    combined = pa.concat_tables(tables, promote_options="default")
    order = pa.array(range(combined.num_rows))
    combined = combined.append_column("__order", order)
    combined = combined.sort_by([("timestamp", "ascending"), ("__order", "ascending")])
    stamps = combined.column("timestamp").to_pylist()
    keep = [i for i, stamp in enumerate(stamps) if i + 1 == len(stamps) or stamps[i + 1] != stamp]
    return combined.take(pa.array(keep)).drop_columns(["__order"])
