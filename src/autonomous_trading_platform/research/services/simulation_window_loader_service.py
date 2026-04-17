from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pyarrow as pa

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.storage.parquet.datasets import (
    RAW_BARS_DATASET,
    ParquetDataset,
)
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader


@dataclass(slots=True)
class SimulationWindowData:
    start_date: date
    end_date: date
    dataset_version: str
    symbols: list[str]
    bars_by_symbol: dict[str, list[MarketBar]]
    feature_tables_by_symbol: dict[str, pa.Table] | None = None


class SimulationWindowLoader:
    def __init__(
        self,
        *,
        bar_reader: HistoricalBarDatasetReader,
        feature_reader: HistoricalBarDatasetReader | None = None,
    ) -> None:
        self.bar_reader = bar_reader
        self.feature_reader = feature_reader

    def load_window(
        self,
        *,
        dataset_version: str,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
        feature_dataset: ParquetDataset | None = None,
        engine: str = "duckdb",
    ) -> SimulationWindowData:
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date")

        if not dataset_version.strip():
            raise ValueError("dataset_version must not be empty")

        normalized_symbols = sorted(
            {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        )
        if not normalized_symbols:
            raise ValueError("At least one symbol is required")

        bars_by_symbol: dict[str, list[MarketBar]] = {}
        feature_tables_by_symbol: dict[str, pa.Table] | None = {} if feature_dataset else None

        for symbol in normalized_symbols:
            bar_table = self.bar_reader.read(
                dataset=RAW_BARS_DATASET,
                dataset_version=dataset_version,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                engine=engine,
            )
            bars_by_symbol[symbol] = [self._row_to_market_bar(row) for row in bar_table.to_pylist()]

            if feature_dataset is not None:
                if self.feature_reader is None:
                    raise ValueError("feature_reader is required when feature_dataset is provided")

                assert feature_tables_by_symbol is not None

                feature_table = self.feature_reader.read(
                    dataset=feature_dataset,
                    dataset_version=dataset_version,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    engine=engine,
                )
                feature_tables_by_symbol[symbol] = feature_table

        return SimulationWindowData(
            start_date=start_date,
            end_date=end_date,
            dataset_version=dataset_version,
            symbols=normalized_symbols,
            bars_by_symbol=bars_by_symbol,
            feature_tables_by_symbol=feature_tables_by_symbol,
        )

    @staticmethod
    def _row_to_market_bar(row: dict[str, Any]) -> MarketBar:
        from autonomous_trading_platform.storage.parquet.repositories.parquet_bar_repository import (
            ParquetBarRepository,
        )

        return ParquetBarRepository._row_to_market_bar(row)
