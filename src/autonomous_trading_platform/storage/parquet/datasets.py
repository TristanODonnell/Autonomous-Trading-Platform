from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

from .schemas import BAR_SCHEMA, CORPORATE_ACTION_SCHEMA, FEATURE_RETURNS_SCHEMA


@dataclass(frozen=True)
class ParquetDataset:
    dataset_key: str
    schema: pa.Schema
    schema_version: str
    root_parts: tuple[str, ...]
    partition_cols: tuple[str, ...]


RAW_BARS_DATASET = ParquetDataset(
    dataset_key="raw_bars",
    schema=BAR_SCHEMA,
    schema_version="1.0.0",
    root_parts=("bars", "raw"),
    partition_cols=("symbol", "year", "month"),
)

ADJUSTED_BARS_DATASET = ParquetDataset(
    dataset_key="adjusted_bars",
    schema=BAR_SCHEMA,
    schema_version="1.0.0",
    root_parts=("bars", "adjusted"),
    partition_cols=("symbol", "year", "month"),
)

CORPORATE_ACTIONS_DATASET = ParquetDataset(
    dataset_key="corporate_actions",
    schema=CORPORATE_ACTION_SCHEMA,
    schema_version="1.0.0",
    root_parts=("corporate_actions",),
    partition_cols=("symbol", "year", "month"),
)

FEATURE_RETURNS_DATASET = ParquetDataset(
    dataset_key="feature_returns",
    schema=FEATURE_RETURNS_SCHEMA,
    schema_version="1.0.0",
    root_parts=("features", "returns"),
    partition_cols=("symbol", "year", "month"),
)

SIMULATION_INPUTS_DATASET = ParquetDataset(
    dataset_key="simulation_inputs",
    schema=pa.schema([]),  # placeholder for now
    schema_version="1.0.0",
    root_parts=("simulation_inputs", "default"),
    partition_cols=("universe_version", "date"),
)
