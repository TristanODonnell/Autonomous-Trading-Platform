from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

from .schemas import (
    BAR_SCHEMA,
    CORPORATE_ACTION_SCHEMA,
    FEATURE_LIQUIDITY_SCHEMA,
    FEATURE_MOVING_AVERAGE_SCHEMA,
    FEATURE_REGIME_SCHEMA,
    FEATURE_RETURNS_SCHEMA,
    FEATURE_VOLATILITY_SCHEMA,
    SIMULATION_EQUITY_CURVE_SCHEMA,
    SIMULATION_PER_BAR_METRICS_SCHEMA,
    SIMULATION_POSITIONS_SCHEMA,
    SIMULATION_SIGNAL_LOG_SCHEMA,
    SIMULATION_TRADE_LOGS_SCHEMA,
)


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

FEATURE_VOLATILITY_DATASET = ParquetDataset(
    dataset_key="feature_volatility",
    schema=FEATURE_VOLATILITY_SCHEMA,
    schema_version="1.0.0",
    root_parts=("features", "volatility"),
    partition_cols=("symbol", "year", "month"),
)

FEATURE_MOVING_AVERAGE_DATASET = ParquetDataset(
    dataset_key="feature_moving_average",
    schema=FEATURE_MOVING_AVERAGE_SCHEMA,
    schema_version="1.0.0",
    root_parts=("features", "moving_average"),
    partition_cols=("symbol", "year", "month"),
)

FEATURE_LIQUIDITY_DATASET = ParquetDataset(
    dataset_key="feature_liquidity",
    schema=FEATURE_LIQUIDITY_SCHEMA,
    schema_version="1.0.0",
    root_parts=("features", "liquidity"),
    partition_cols=("symbol", "year", "month"),
)

FEATURE_REGIME_DATASET = ParquetDataset(
    dataset_key="feature_regime",
    schema=FEATURE_REGIME_SCHEMA,
    schema_version="1.0.0",
    root_parts=("features", "regime"),
    partition_cols=("symbol", "year", "month"),
)

SIMULATION_TRADE_LOGS_DATASET = ParquetDataset(
    dataset_key="simulation_trade_logs",
    schema=SIMULATION_TRADE_LOGS_SCHEMA,
    schema_version="1.0.0",
    root_parts=("simulations", "trade_logs"),
    partition_cols=("experiment_id", "strategy_id", "date"),
)

SIMULATION_EQUITY_CURVE_DATASET = ParquetDataset(
    dataset_key="simulation_equity_curve",
    root_parts=("simulations", "equity_curve"),
    schema=SIMULATION_EQUITY_CURVE_SCHEMA,
    schema_version="1.0.0",
    partition_cols=("experiment_id", "strategy_id", "date"),
)

SIMULATION_PER_BAR_METRICS_DATASET = ParquetDataset(
    dataset_key="simulation_per_bar_metrics",
    root_parts=("simulations", "per_bar_metrics"),
    schema=SIMULATION_PER_BAR_METRICS_SCHEMA,
    schema_version="1.0.0",
    partition_cols=("experiment_id", "strategy_id", "date"),
)

SIMULATION_POSITIONS_DATASET = ParquetDataset(
    dataset_key="simulation_positions",
    root_parts=("simulations", "positions"),
    schema=SIMULATION_POSITIONS_SCHEMA,
    schema_version="1.0.0",
    partition_cols=("experiment_id", "strategy_id", "date"),
)

SIMULATION_SIGNAL_LOG_DATASET = ParquetDataset(
    dataset_key="simulation_signal_log",
    root_parts=("simulations", "signal_log"),
    schema=SIMULATION_SIGNAL_LOG_SCHEMA,
    schema_version="1.0.0",
    partition_cols=("experiment_id", "strategy_id", "date"),
)
