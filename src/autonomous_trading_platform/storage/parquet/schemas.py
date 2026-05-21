from __future__ import annotations

import pyarrow as pa

UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

BAR_SCHEMA = pa.schema(
    [
        pa.field("bar_id", pa.string(), nullable=False),
        pa.field("timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("end_timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("interval", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("vwap", pa.float64(), nullable=True),
        pa.field("trade_count", pa.int64(), nullable=True),
        pa.field("price_basis", pa.string(), nullable=False),
        pa.field("adjustment_factor", pa.float64(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("ingested_at", UTC_TIMESTAMP, nullable=False),
        pa.field("quality_flags", pa.list_(pa.string()), nullable=True),
        pa.field("date", pa.date32(), nullable=False),
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)

CORPORATE_ACTION_SCHEMA = pa.schema(
    [
        pa.field("action_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("action_type", pa.string(), nullable=False),
        pa.field("effective_date", pa.date32(), nullable=False),
        pa.field("announced_date", pa.date32(), nullable=True),
        pa.field("record_date", pa.date32(), nullable=True),
        pa.field("payable_date", pa.date32(), nullable=True),
        pa.field("split_ratio", pa.float64(), nullable=True),
        pa.field("cash_amount", pa.float64(), nullable=True),
        pa.field("currency", pa.string(), nullable=False),
        pa.field("new_symbol", pa.string(), nullable=True),
        pa.field("source", pa.string(), nullable=False),
        pa.field("ingested_at", UTC_TIMESTAMP, nullable=False),
        pa.field("metadata", pa.string(), nullable=True),
        pa.field(
            "date", pa.date32(), nullable=False
        ),  # normalized partition/filter date; equals effective_date
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)

FEATURE_RETURNS_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("date", pa.date32(), nullable=False),
        pa.field("ret_1d", pa.float64(), nullable=True),
        pa.field("ret_5d", pa.float64(), nullable=True),
        pa.field("ret_20d", pa.float64(), nullable=True),
        pa.field("underlying_dataset_version", pa.string(), nullable=False),
        pa.field("price_basis", pa.string(), nullable=False),
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)

FEATURE_VOLATILITY_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string()),
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("date", pa.date32()),
        pa.field("volatility_value", pa.float64()),
        pa.field("underlying_dataset_version", pa.string()),
        pa.field("price_basis", pa.string()),
        pa.field("year", pa.string()),
        pa.field("month", pa.string()),
    ]
)

FEATURE_MOVING_AVERAGE_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("date", pa.date32(), nullable=False),
        pa.field("moving_average_value", pa.float64(), nullable=True),
        pa.field("underlying_dataset_version", pa.string(), nullable=False),
        pa.field("price_basis", pa.string(), nullable=False),
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)

FEATURE_LIQUIDITY_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("date", pa.date32(), nullable=False),
        pa.field("avg_volume_value", pa.float64(), nullable=True),
        pa.field("bid_ask_spread", pa.float64(), nullable=True),
        pa.field("underlying_dataset_version", pa.string(), nullable=False),
        pa.field("price_basis", pa.string(), nullable=False),
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)

FEATURE_REGIME_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("date", pa.date32(), nullable=False),
        pa.field("regime", pa.string(), nullable=False),
        pa.field("underlying_dataset_version", pa.string(), nullable=False),
        pa.field("price_basis", pa.string(), nullable=False),
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)

FEATURE_REGIME_CLASSIFICATION_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", UTC_TIMESTAMP, nullable=False),
        pa.field("date", pa.date32(), nullable=False),
        # Trend dimension
        pa.field("regime_trend", pa.string(), nullable=True),
        pa.field("regime_trend_confidence", pa.float64(), nullable=True),
        pa.field("trend_ma_short", pa.float64(), nullable=True),
        pa.field("trend_ma_long", pa.float64(), nullable=True),
        pa.field("trend_rolling_return_20d", pa.float64(), nullable=True),
        # Volatility dimension
        pa.field("regime_volatility", pa.string(), nullable=True),
        pa.field("regime_volatility_confidence", pa.float64(), nullable=True),
        pa.field("volatility_realized", pa.float64(), nullable=True),
        pa.field("volatility_percentile", pa.float64(), nullable=True),
        pa.field("volatility_threshold_high", pa.float64(), nullable=True),
        pa.field("volatility_threshold_low", pa.float64(), nullable=True),
        # Liquidity dimension
        pa.field("regime_liquidity", pa.string(), nullable=True),
        pa.field("regime_liquidity_confidence", pa.float64(), nullable=True),
        pa.field("liquidity_avg_dollar_volume", pa.float64(), nullable=True),
        pa.field("liquidity_percentile", pa.float64(), nullable=True),
        # Mean-reversion dimension
        pa.field("regime_mean_reversion", pa.string(), nullable=True),
        pa.field("regime_mean_reversion_confidence", pa.float64(), nullable=True),
        pa.field("mean_reversion_zscore_std", pa.float64(), nullable=True),
        pa.field("mean_reversion_trend_strength", pa.float64(), nullable=True),
        # Composite risk dimension
        pa.field("regime_risk", pa.string(), nullable=True),
        # Lineage
        pa.field("underlying_dataset_version", pa.string(), nullable=False),
        pa.field("price_basis", pa.string(), nullable=False),
        pa.field("year", pa.string(), nullable=False),
        pa.field("month", pa.string(), nullable=False),
    ]
)

SIMULATION_TRADE_LOGS_SCHEMA = pa.schema(
    [
        pa.field("run_id", pa.string()),
        pa.field("experiment_id", pa.string()),
        pa.field("strategy_id", pa.string()),
        pa.field("stage_name", pa.string()),
        pa.field("window_role", pa.string()),
        pa.field("dataset_version", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("side", pa.string()),
        pa.field("quantity", pa.float64()),
        pa.field("price", pa.float64()),
        pa.field("notional", pa.float64()),
        pa.field("fees", pa.float64()),
        pa.field("date", pa.date32()),
    ]
)

SIMULATION_EQUITY_CURVE_SCHEMA = pa.schema(
    [
        pa.field("run_id", pa.string()),
        pa.field("experiment_id", pa.string()),
        pa.field("strategy_id", pa.string()),
        pa.field("stage_name", pa.string()),
        pa.field("window_role", pa.string()),
        pa.field("dataset_version", pa.string()),
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("equity", pa.float64()),
        pa.field("cash", pa.float64()),
        pa.field("positions_value", pa.float64()),
        pa.field("drawdown", pa.float64()),
        pa.field("date", pa.date32()),
    ]
)

SIMULATION_PER_BAR_METRICS_SCHEMA = pa.schema(
    [
        pa.field("run_id", pa.string()),
        pa.field("experiment_id", pa.string()),
        pa.field("strategy_id", pa.string()),
        pa.field("stage_name", pa.string()),
        pa.field("window_role", pa.string()),
        pa.field("dataset_version", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("bar_return", pa.float64()),
        pa.field("position_size", pa.float64()),
        pa.field("unrealized_pnl", pa.float64()),
        pa.field("realized_pnl", pa.float64()),
        pa.field("date", pa.date32()),
    ]
)

SIMULATION_POSITIONS_SCHEMA = pa.schema(
    [
        pa.field("run_id", pa.string()),
        pa.field("experiment_id", pa.string()),
        pa.field("strategy_id", pa.string()),
        pa.field("stage_name", pa.string()),
        pa.field("window_role", pa.string()),
        pa.field("dataset_version", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("timestamp", UTC_TIMESTAMP),
        pa.field("quantity", pa.float64()),
        pa.field("avg_cost", pa.float64()),
        pa.field("market_price", pa.float64()),
        pa.field("market_value", pa.float64()),
        pa.field("unrealized_pnl", pa.float64()),
        pa.field("realized_pnl", pa.float64()),
        pa.field("date", pa.date32()),
    ]
)

SIMULATION_SIGNAL_LOG_SCHEMA = pa.schema(
    [
        pa.field("run_id", pa.string()),
        pa.field("experiment_id", pa.string()),
        pa.field("strategy_id", pa.string()),
        pa.field("stage_name", pa.string()),
        pa.field("window_role", pa.string()),
        pa.field("dataset_version", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("direction", pa.string()),
        pa.field("date", pa.date32()),
    ]
)

_REGIME_ANALYSIS_IDENTITY = [
    pa.field("run_id", pa.string()),
    pa.field("experiment_id", pa.string()),
    pa.field("strategy_id", pa.string()),
    pa.field("stage_name", pa.string()),
    pa.field("window_role", pa.string()),
    pa.field("dataset_version", pa.string()),
]

REGIME_ANALYSIS_METRICS_SCHEMA = pa.schema(
    _REGIME_ANALYSIS_IDENTITY
    + [
        pa.field("regime_dimension", pa.string()),
        pa.field("regime_label", pa.string()),
        pa.field("bar_count", pa.int64()),
        pa.field("exposure_fraction", pa.float64()),
        pa.field("total_return", pa.float64(), nullable=True),
        pa.field("cagr", pa.float64(), nullable=True),
        pa.field("sharpe", pa.float64(), nullable=True),
        pa.field("sortino", pa.float64(), nullable=True),
        pa.field("volatility", pa.float64(), nullable=True),
        pa.field("max_drawdown", pa.float64(), nullable=True),
        pa.field("trade_count", pa.int64()),
        pa.field("win_rate", pa.float64(), nullable=True),
        pa.field("expectancy", pa.float64(), nullable=True),
        pa.field("profit_factor", pa.float64(), nullable=True),
        pa.field("avg_win", pa.float64(), nullable=True),
        pa.field("avg_loss", pa.float64(), nullable=True),
        pa.field("trade_frequency", pa.float64(), nullable=True),
        pa.field("avg_bar_return", pa.float64(), nullable=True),
        pa.field("date", pa.date32()),
    ]
)

REGIME_TRANSITION_SUMMARY_SCHEMA = pa.schema(
    _REGIME_ANALYSIS_IDENTITY
    + [
        pa.field("regime_dimension", pa.string()),
        pa.field("from_regime", pa.string()),
        pa.field("to_regime", pa.string()),
        pa.field("transition_count", pa.int64()),
        pa.field("avg_duration_before", pa.float64(), nullable=True),
        pa.field("avg_pre_return", pa.float64(), nullable=True),
        pa.field("avg_post_return", pa.float64(), nullable=True),
        pa.field("date", pa.date32()),
    ]
)

STRATEGY_REGIME_PROFILE_SCHEMA = pa.schema(
    _REGIME_ANALYSIS_IDENTITY
    + [
        pa.field("regime_dimension", pa.string()),
        pa.field("sharpe_std", pa.float64()),
        pa.field("sharpe_range", pa.float64()),
        pa.field("best_regime", pa.string(), nullable=True),
        pa.field("worst_regime", pa.string(), nullable=True),
        pa.field("regime_robustness", pa.float64()),
        pa.field("overall_sensitivity", pa.float64()),
        pa.field("is_regime_robust", pa.bool_()),
        pa.field("date", pa.date32()),
    ]
)

# ---------------------------------------------------------------------------
# Validation framework schemas (TASK-2.4)
# ---------------------------------------------------------------------------

_VALIDATION_IDENTITY = [
    pa.field("validation_run_id", pa.string(), nullable=False),
    pa.field("experiment_id", pa.string(), nullable=False),
    pa.field("strategy_id", pa.string(), nullable=False),
    pa.field("dataset_version", pa.string(), nullable=False),
]

VALIDATION_ROBUSTNESS_SCORE_SCHEMA = pa.schema(
    _VALIDATION_IDENTITY
    + [
        pa.field("overall_score", pa.float64()),
        pa.field("walk_forward_consistency", pa.float64(), nullable=True),
        pa.field("mc_stability", pa.float64(), nullable=True),
        pa.field("regime_robustness", pa.float64(), nullable=True),
        pa.field("parameter_stability", pa.float64(), nullable=True),
        pa.field("stress_resilience", pa.float64(), nullable=True),
        pa.field("overfitting_resistance", pa.float64(), nullable=True),
        pa.field("overall_passed", pa.bool_()),
        pa.field("computed_at", UTC_TIMESTAMP),
        pa.field("date", pa.date32()),
    ]
)

VALIDATION_STRESS_TEST_SCHEMA = pa.schema(
    _VALIDATION_IDENTITY
    + [
        pa.field("scenario_name", pa.string()),
        pa.field("description", pa.string()),
        pa.field("original_sharpe", pa.float64()),
        pa.field("stressed_sharpe", pa.float64()),
        pa.field("original_drawdown", pa.float64()),
        pa.field("stressed_drawdown", pa.float64()),
        pa.field("sharpe_degradation", pa.float64()),
        pa.field("survived", pa.bool_()),
        pa.field("date", pa.date32()),
    ]
)

VALIDATION_WALK_FORWARD_SCHEMA = pa.schema(
    _VALIDATION_IDENTITY
    + [
        pa.field("n_folds", pa.int64()),
        pa.field("n_folds_passed", pa.int64()),
        pa.field("fold_consistency", pa.float64()),
        pa.field("avg_train_sharpe", pa.float64()),
        pa.field("avg_test_sharpe", pa.float64()),
        pa.field("train_test_degradation", pa.float64()),
        pa.field("fold_sharpe_cv", pa.float64()),
        pa.field("fold_sharpe_stability", pa.float64()),
        pa.field("date", pa.date32()),
    ]
)

VALIDATION_OVERFITTING_SCHEMA = pa.schema(
    _VALIDATION_IDENTITY
    + [
        pa.field("overfitting_probability", pa.float64()),
        pa.field("resistance_score", pa.float64()),
        pa.field("train_test_degradation", pa.float64(), nullable=True),
        pa.field("fold_instability", pa.float64(), nullable=True),
        pa.field("mc_instability", pa.float64(), nullable=True),
        pa.field("regime_concentration", pa.float64(), nullable=True),
        pa.field("low_trade_count_flag", pa.bool_(), nullable=True),
        pa.field("narrow_period_alpha", pa.float64(), nullable=True),
        pa.field("parameter_fragility", pa.float64(), nullable=True),
        pa.field("date", pa.date32()),
    ]
)

VALIDATION_SENSITIVITY_SCHEMA = pa.schema(
    _VALIDATION_IDENTITY
    + [
        pa.field("parameter_name", pa.string()),
        pa.field("reference_value", pa.float64()),
        pa.field("sensitivity_score", pa.float64()),
        pa.field("stability_score", pa.float64()),
        pa.field("is_stable", pa.bool_()),
        pa.field("stability_region_min", pa.float64(), nullable=True),
        pa.field("stability_region_max", pa.float64(), nullable=True),
        pa.field("overall_stability_score", pa.float64()),
        pa.field("date", pa.date32()),
    ]
)
