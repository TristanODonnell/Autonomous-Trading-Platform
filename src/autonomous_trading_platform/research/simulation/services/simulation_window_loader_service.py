from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pyarrow as pa

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.storage.parquet.datasets import (
    ParquetDataset,
)
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader


@dataclass(slots=True)
class SimulationFeatureDatasetRequest:
    feature_name: str
    dataset: ParquetDataset
    dataset_version: str


@dataclass(slots=True)
class SimulationWindowData:
    start_date: date
    end_date: date
    dataset_version: str
    symbols: list[str]
    timeline: list[datetime]
    bars_by_symbol: dict[str, list[MarketBar]]
    bars_by_timestamp: dict[datetime, dict[str, MarketBar]]

    # Always present. Empty dict means no features loaded.
    feature_tables_by_symbol: dict[str, dict[str, pa.Table]]


class SimulationWindowLoader:
    """
    Loads simulation data using bounded partition reads only.
    This loader must not read entire dataset versions for windowed simulation access.
    """

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
        bars_dataset: ParquetDataset,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
        feature_datasets: Iterable[SimulationFeatureDatasetRequest] | None = None,
        engine: str = "duckdb",
        strict: bool = False,
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
        feature_requests = list(feature_datasets or [])
        feature_tables_by_symbol: dict[str, dict[str, pa.Table]] = {}

        for symbol in normalized_symbols:
            try:
                bar_table = self.bar_reader.read(
                    dataset=bars_dataset,
                    dataset_version=dataset_version,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    engine=engine,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load bar data for symbol={symbol}, "
                    f"dataset_version={dataset_version}, "
                    f"window={start_date}..{end_date}"
                ) from exc

            if strict and bar_table.num_rows == 0:
                raise ValueError(
                    f"No bar data found for symbol={symbol}, "
                    f"dataset_version={dataset_version}, "
                    f"window={start_date}..{end_date}"
                )

            bars_by_symbol[symbol] = [self._row_to_market_bar(row) for row in bar_table.to_pylist()]

            if feature_requests:
                if self.feature_reader is None:
                    raise ValueError(
                        "feature_reader is required when feature_datasets are provided"
                    )

                feature_tables_by_symbol[symbol] = {}

                for feature_request in feature_requests:
                    try:
                        feature_table = self.feature_reader.read(
                            dataset=feature_request.dataset,
                            dataset_version=feature_request.dataset_version,
                            symbol=symbol,
                            start_date=start_date,
                            end_date=end_date,
                            engine=engine,
                        )
                    except Exception as exc:
                        raise RuntimeError(
                            f"Failed to load feature data for symbol={symbol}, "
                            f"feature_name={feature_request.feature_name}, "
                            f"dataset_version={feature_request.dataset_version}, "
                            f"window={start_date}..{end_date}, "
                            f"feature_dataset={feature_request.dataset.dataset_key}"
                        ) from exc

                    if strict and feature_table.num_rows == 0:
                        raise ValueError(
                            f"No feature data found for symbol={symbol}, "
                            f"feature_name={feature_request.feature_name}, "
                            f"dataset_version={feature_request.dataset_version}, "
                            f"window={start_date}..{end_date}"
                        )

                    feature_tables_by_symbol[symbol][feature_request.feature_name] = feature_table

        bars_by_timestamp: dict[datetime, dict[str, MarketBar]] = {}

        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                bars_by_timestamp.setdefault(bar.timestamp, {})[symbol] = bar

        timeline = sorted(bars_by_timestamp.keys())

        return SimulationWindowData(
            start_date=start_date,
            end_date=end_date,
            dataset_version=dataset_version,
            symbols=normalized_symbols,
            timeline=timeline,
            bars_by_symbol=bars_by_symbol,
            bars_by_timestamp=bars_by_timestamp,
            feature_tables_by_symbol=feature_tables_by_symbol,
        )

    @staticmethod
    def _row_to_market_bar(row: dict[str, Any]) -> MarketBar:
        from autonomous_trading_platform.storage.parquet.repositories.parquet_bar_repository import (
            ParquetBarRepository,
        )

        return ParquetBarRepository._row_to_market_bar(row)
