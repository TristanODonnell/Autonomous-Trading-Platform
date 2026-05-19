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
