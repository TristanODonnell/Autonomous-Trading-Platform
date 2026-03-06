from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

from .schemas import BAR_SCHEMA, CORPORATE_ACTION_SCHEMA


@dataclass(frozen=True)
class ParquetDataset:
    name: str
    schema: pa.Schema
    partition_cols: tuple[str, ...]
    schema_version: str


RAW_BARS_DATASET = ParquetDataset(
    name="market_bars_raw",
    schema=BAR_SCHEMA,
    partition_cols=("symbol", "date"),
    schema_version="1.0.0",
)

ADJUSTED_BARS_DATASET = ParquetDataset(
    name="market_bars_adjusted",
    schema=BAR_SCHEMA,
    partition_cols=("symbol", "date"),
    schema_version="1.0.0",
)

CORPORATE_ACTIONS_DATASET = ParquetDataset(
    name="corporate_actions",
    schema=CORPORATE_ACTION_SCHEMA,
    partition_cols=("symbol", "date"),
    schema_version="1.0.0",
)

ALL_PARQUET_DATASETS: dict[str, ParquetDataset] = {
    RAW_BARS_DATASET.name: RAW_BARS_DATASET,
    ADJUSTED_BARS_DATASET.name: ADJUSTED_BARS_DATASET,
    CORPORATE_ACTIONS_DATASET.name: CORPORATE_ACTIONS_DATASET,
}
