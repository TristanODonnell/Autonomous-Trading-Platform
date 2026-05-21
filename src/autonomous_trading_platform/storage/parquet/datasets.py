from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

from .schemas import (
    BAR_SCHEMA,
    CORPORATE_ACTION_SCHEMA,
    FEATURE_LIQUIDITY_SCHEMA,
    FEATURE_MOVING_AVERAGE_SCHEMA,
    FEATURE_REGIME_CLASSIFICATION_SCHEMA,
    FEATURE_REGIME_SCHEMA,
    FEATURE_RETURNS_SCHEMA,
    FEATURE_VOLATILITY_SCHEMA,
    INTELLIGENCE_CANDIDATE_RANKING_SCHEMA,
    INTELLIGENCE_CLUSTER_ASSIGNMENT_SCHEMA,
    INTELLIGENCE_REGIME_FINGERPRINT_SCHEMA,
    REGIME_ANALYSIS_METRICS_SCHEMA,
    REGIME_TRANSITION_SUMMARY_SCHEMA,
    SIMULATION_EQUITY_CURVE_SCHEMA,
    SIMULATION_PER_BAR_METRICS_SCHEMA,
    SIMULATION_POSITIONS_SCHEMA,
    SIMULATION_SIGNAL_LOG_SCHEMA,
    SIMULATION_TRADE_LOGS_SCHEMA,
    STRATEGY_REGIME_PROFILE_SCHEMA,
    VALIDATION_OVERFITTING_SCHEMA,
    VALIDATION_ROBUSTNESS_SCORE_SCHEMA,
    VALIDATION_SENSITIVITY_SCHEMA,
    VALIDATION_STRESS_TEST_SCHEMA,
    VALIDATION_WALK_FORWARD_SCHEMA,
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

FEATURE_REGIME_CLASSIFICATION_DATASET = ParquetDataset(
    dataset_key="feature_regime_classification",
    schema=FEATURE_REGIME_CLASSIFICATION_SCHEMA,
    schema_version="1.0.0",
    root_parts=("features", "regime_classification"),
    partition_cols=("symbol", "year", "month"),
)

_SIMULATION_PARTITION_COLS = (
    "experiment_id",
    "run_id",
    "stage_name",
    "window_role",
)

SIMULATION_TRADE_LOGS_DATASET = ParquetDataset(
    dataset_key="simulation_trade_logs",
    schema=SIMULATION_TRADE_LOGS_SCHEMA,
    schema_version="2.0.0",
    root_parts=("simulations", "trade_logs"),
    partition_cols=_SIMULATION_PARTITION_COLS,
)

SIMULATION_EQUITY_CURVE_DATASET = ParquetDataset(
    dataset_key="simulation_equity_curve",
    root_parts=("simulations", "equity_curve"),
    schema=SIMULATION_EQUITY_CURVE_SCHEMA,
    schema_version="2.0.0",
    partition_cols=_SIMULATION_PARTITION_COLS,
)

SIMULATION_PER_BAR_METRICS_DATASET = ParquetDataset(
    dataset_key="simulation_per_bar_metrics",
    root_parts=("simulations", "per_bar_metrics"),
    schema=SIMULATION_PER_BAR_METRICS_SCHEMA,
    schema_version="2.0.0",
    partition_cols=_SIMULATION_PARTITION_COLS,
)

SIMULATION_POSITIONS_DATASET = ParquetDataset(
    dataset_key="simulation_positions",
    root_parts=("simulations", "positions"),
    schema=SIMULATION_POSITIONS_SCHEMA,
    schema_version="2.0.0",
    partition_cols=_SIMULATION_PARTITION_COLS,
)

SIMULATION_SIGNAL_LOG_DATASET = ParquetDataset(
    dataset_key="simulation_signal_log",
    root_parts=("simulations", "signal_log"),
    schema=SIMULATION_SIGNAL_LOG_SCHEMA,
    schema_version="2.0.0",
    partition_cols=_SIMULATION_PARTITION_COLS,
)

REGIME_ANALYSIS_METRICS_DATASET = ParquetDataset(
    dataset_key="regime_analysis_metrics",
    root_parts=("simulations", "regime_analysis", "metrics"),
    schema=REGIME_ANALYSIS_METRICS_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_SIMULATION_PARTITION_COLS,
)

REGIME_TRANSITION_SUMMARY_DATASET = ParquetDataset(
    dataset_key="regime_transition_summary",
    root_parts=("simulations", "regime_analysis", "transitions"),
    schema=REGIME_TRANSITION_SUMMARY_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_SIMULATION_PARTITION_COLS,
)

STRATEGY_REGIME_PROFILE_DATASET = ParquetDataset(
    dataset_key="strategy_regime_profile",
    root_parts=("simulations", "regime_analysis", "profiles"),
    schema=STRATEGY_REGIME_PROFILE_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_SIMULATION_PARTITION_COLS,
)

# ---------------------------------------------------------------------------
# Validation framework datasets (TASK-2.4)
# ---------------------------------------------------------------------------

_VALIDATION_PARTITION_COLS = ("experiment_id", "strategy_id")

VALIDATION_ROBUSTNESS_SCORE_DATASET = ParquetDataset(
    dataset_key="validation_robustness_score",
    root_parts=("validation", "robustness_scores"),
    schema=VALIDATION_ROBUSTNESS_SCORE_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_VALIDATION_PARTITION_COLS,
)

VALIDATION_STRESS_TEST_DATASET = ParquetDataset(
    dataset_key="validation_stress_test",
    root_parts=("validation", "stress_test_results"),
    schema=VALIDATION_STRESS_TEST_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_VALIDATION_PARTITION_COLS,
)

VALIDATION_WALK_FORWARD_DATASET = ParquetDataset(
    dataset_key="validation_walk_forward",
    root_parts=("validation", "walk_forward_results"),
    schema=VALIDATION_WALK_FORWARD_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_VALIDATION_PARTITION_COLS,
)

VALIDATION_OVERFITTING_DATASET = ParquetDataset(
    dataset_key="validation_overfitting",
    root_parts=("validation", "overfitting_analysis"),
    schema=VALIDATION_OVERFITTING_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_VALIDATION_PARTITION_COLS,
)

VALIDATION_SENSITIVITY_DATASET = ParquetDataset(
    dataset_key="validation_sensitivity",
    root_parts=("validation", "sensitivity_profiles"),
    schema=VALIDATION_SENSITIVITY_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_VALIDATION_PARTITION_COLS,
)

# ---------------------------------------------------------------------------
# Research intelligence datasets (TASK-2.5)
# ---------------------------------------------------------------------------

_INTELLIGENCE_PARTITION_COLS = ("experiment_id", "strategy_id")

INTELLIGENCE_CANDIDATE_RANKING_DATASET = ParquetDataset(
    dataset_key="intelligence_candidate_ranking",
    root_parts=("intelligence", "candidate_rankings"),
    schema=INTELLIGENCE_CANDIDATE_RANKING_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_INTELLIGENCE_PARTITION_COLS,
)

INTELLIGENCE_REGIME_FINGERPRINT_DATASET = ParquetDataset(
    dataset_key="intelligence_regime_fingerprint",
    root_parts=("intelligence", "regime_fingerprints"),
    schema=INTELLIGENCE_REGIME_FINGERPRINT_SCHEMA,
    schema_version="1.0.0",
    partition_cols=_INTELLIGENCE_PARTITION_COLS,
)

INTELLIGENCE_CLUSTER_ASSIGNMENT_DATASET = ParquetDataset(
    dataset_key="intelligence_cluster_assignments",
    root_parts=("intelligence", "cluster_assignments"),
    schema=INTELLIGENCE_CLUSTER_ASSIGNMENT_SCHEMA,
    schema_version="1.0.0",
    partition_cols=("experiment_id",),
)
