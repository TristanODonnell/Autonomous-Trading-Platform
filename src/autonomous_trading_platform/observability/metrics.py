from __future__ import annotations

from opentelemetry import metrics

meter = metrics.get_meter("autonomous_trading_platform")

# =========================
# TRADING CYCLE METRICS
# =========================
trading_cycle_runs = meter.create_counter(
    name="ratp_trading_cycle_runs_total",
    description="Total number of trading cycle executions",
    unit="1",
)

trading_cycle_duration = meter.create_histogram(
    name="ratp_trading_cycle_duration_seconds",
    description="Trading cycle execution duration",
    unit="s",
)

trading_cycle_step_runs = meter.create_counter(
    name="ratp_trading_cycle_step_runs_total",
    description="Total number of trading cycle step executions",
    unit="1",
)

trading_cycle_step_duration = meter.create_histogram(
    name="ratp_trading_cycle_step_duration_seconds",
    description="Trading cycle step execution duration",
    unit="s",
)

trading_cycle_failures = meter.create_counter(
    name="ratp_trading_cycle_failures_total",
    description="Total number of trading cycle failures",
    unit="1",
)

trading_cycle_degraded = meter.create_counter(
    name="ratp_trading_cycle_degraded_total",
    description="Total number of degraded trading cycle outcomes",
    unit="1",
)

open_orders = meter.create_up_down_counter(
    name="ratp_open_orders",
    description="Current number of open orders",
    unit="1",
)

# =========================
# INGESTION CYCLE METRICS
# =========================

# --- Cycle-level metrics ---

ingestion_cycle_runs = meter.create_counter(
    name="ratp_ingestion_cycle_runs_total",
    description="Total number of ingestion cycle executions",
    unit="1",
)

ingestion_cycle_failures = meter.create_counter(
    name="ratp_ingestion_cycle_failures_total",
    description="Total number of ingestion cycle failures",
    unit="1",
)

ingestion_cycle_duration = meter.create_histogram(
    name="ratp_ingestion_cycle_duration_seconds",
    description="Ingestion cycle execution duration",
    unit="s",
)

ingestion_cycle_step_runs = meter.create_counter(
    name="ratp_ingestion_cycle_step_runs_total",
    description="Total number of ingestion cycle step executions",
    unit="1",
)

ingestion_cycle_step_duration = meter.create_histogram(
    name="ratp_ingestion_cycle_step_duration_seconds",
    description="Ingestion cycle step execution duration",
    unit="s",
)

# --- Data throughput metrics ---

bars_ingested = meter.create_counter(
    name="ratp_bars_ingested_total",
    description="Total number of market bars successfully ingested",
    unit="1",
)

missing_bars = meter.create_counter(
    name="ratp_missing_bars_total",
    description="Total number of expected bars that were missing",
    unit="1",
)

late_bars = meter.create_counter(
    name="ratp_late_bars_total",
    description="Total number of bars arriving after SLA threshold",
    unit="1",
)


# --- Performance & distribution metrics ---

bar_ingestion_latency = meter.create_histogram(
    name="ratp_bar_ingestion_latency_seconds",
    description="Latency between bar timestamp and ingestion time",
    unit="s",
)

ingestion_batch_size = meter.create_histogram(
    name="ratp_ingestion_batch_size",
    description="Number of bars processed per ingestion batch",
    unit="1",
)


# --- State / health metrics (gauges) ---


def _ingestion_lag_callback(options):
    """
    Returns current ingestion lag in seconds.
    Replace with real computation (e.g., now - latest_bar_timestamp).
    """
    # TODO: wire to real state
    return [metrics.Observation(0.0)]


ingestion_lag_seconds = meter.create_observable_gauge(
    name="ratp_ingestion_lag_seconds",
    description="Current lag between latest available market data and system ingestion",
    callbacks=[_ingestion_lag_callback],
    unit="s",
)


def _symbols_expected_callback(options):
    # TODO: wire to membership service
    return [metrics.Observation(0)]


symbols_expected = meter.create_observable_gauge(
    name="ratp_symbols_expected",
    description="Number of symbols expected for ingestion in current cycle",
    callbacks=[_symbols_expected_callback],
    unit="1",
)


def _symbols_received_callback(options):
    # TODO: wire to ingestion results
    return [metrics.Observation(0)]


symbols_received = meter.create_observable_gauge(
    name="ratp_symbols_received",
    description="Number of symbols successfully received in ingestion",
    callbacks=[_symbols_received_callback],
    unit="1",
)

# =============================
# MARKET BACKFILL CYCLE METRICS
# =============================

# --- Cycle-level metrics ---

market_backfill_cycle_runs = meter.create_counter(
    name="ratp_market_backfill_cycle_runs_total",
    description="Total number of market backfill cycle executions",
    unit="1",
)

market_backfill_cycle_failures = meter.create_counter(
    name="ratp_market_backfill_cycle_failures_total",
    description="Total number of market backfill cycle failures",
    unit="1",
)

market_backfill_cycle_duration = meter.create_histogram(
    name="ratp_market_backfill_cycle_duration_seconds",
    description="Market backfill cycle execution duration",
    unit="s",
)

market_backfill_cycle_step_runs = meter.create_counter(
    name="ratp_market_backfill_cycle_step_runs_total",
    description="Total number of market backfill cycle step executions",
    unit="1",
)

market_backfill_cycle_step_duration = meter.create_histogram(
    name="ratp_market_backfill_cycle_step_duration_seconds",
    description="Market backfill cycle step execution duration",
    unit="s",
)


# --- Data throughput & processing metrics ---

historical_bars_backfilled = meter.create_counter(
    name="ratp_historical_bars_backfilled_total",
    description="Total number of historical market bars successfully backfilled",
    unit="1",
)

backfill_symbols_processed = meter.create_counter(
    name="ratp_backfill_symbols_processed_total",
    description="Total number of symbols processed during backfill",
    unit="1",
)

backfill_symbol_failures = meter.create_counter(
    name="ratp_backfill_symbol_failures_total",
    description="Total number of symbol-level failures during backfill",
    unit="1",
)

backfill_windows_processed = meter.create_counter(
    name="ratp_backfill_windows_processed_total",
    description="Total number of time windows processed during backfill",
    unit="1",
)

backfill_api_requests = meter.create_counter(
    name="ratp_backfill_api_requests_total",
    description="Total number of API requests made during backfill",
    unit="1",
)

backfill_api_request_failures = meter.create_counter(
    name="ratp_backfill_api_request_failures_total",
    description="Total number of failed API requests during backfill",
    unit="1",
)


# --- Performance & distribution metrics ---

backfill_symbol_duration_seconds = meter.create_histogram(
    name="ratp_backfill_symbol_duration_seconds",
    description="Time taken to backfill data for a single symbol",
    unit="s",
)

backfill_window_duration_seconds = meter.create_histogram(
    name="ratp_backfill_window_duration_seconds",
    description="Time taken to process a single time window during backfill",
    unit="s",
)

backfill_batch_size = meter.create_histogram(
    name="ratp_backfill_batch_size",
    description="Number of bars processed per batch during backfill",
    unit="1",
)

backfill_days_requested = meter.create_histogram(
    name="ratp_backfill_days_requested",
    description="Number of days requested per backfill operation",
    unit="1",
)

backfill_bars_per_symbol = meter.create_histogram(
    name="ratp_backfill_bars_per_symbol",
    description="Number of bars retrieved per symbol during backfill",
    unit="1",
)

backfill_request_latency_seconds = meter.create_histogram(
    name="ratp_backfill_request_latency_seconds",
    description="Latency of API requests during backfill",
    unit="s",
)

# ==========================================
# CORPORATE ACTION INGESTION CYCLE METRICS
# ==========================================

# --- Cycle-level metrics ---

corporate_action_ingestion_cycle_runs = meter.create_counter(
    name="ratp_corporate_action_ingestion_cycle_runs_total",
    description="Total number of corporate action ingestion cycle executions",
    unit="1",
)

corporate_action_ingestion_cycle_failures = meter.create_counter(
    name="ratp_corporate_action_ingestion_cycle_failures_total",
    description="Total number of corporate action ingestion cycle failures",
    unit="1",
)

corporate_action_ingestion_cycle_duration = meter.create_histogram(
    name="ratp_corporate_action_ingestion_cycle_duration_seconds",
    description="Corporate action ingestion cycle execution duration",
    unit="s",
)

corporate_action_ingestion_cycle_step_runs = meter.create_counter(
    name="ratp_corporate_action_ingestion_cycle_step_runs_total",
    description="Total number of corporate action ingestion cycle step executions",
    unit="1",
)

corporate_action_ingestion_cycle_step_duration = meter.create_histogram(
    name="ratp_corporate_action_ingestion_cycle_step_duration_seconds",
    description="Corporate action ingestion cycle step execution duration",
    unit="s",
)


# --- Data throughput & processing metrics ---

corporate_actions_ingested = meter.create_counter(
    name="ratp_corporate_actions_ingested_total",
    description="Total number of corporate actions successfully ingested",
    unit="1",
)

corporate_action_records_processed = meter.create_counter(
    name="ratp_corporate_action_records_processed_total",
    description="Total number of corporate action records processed",
    unit="1",
)

affected_symbols_processed = meter.create_counter(
    name="ratp_affected_symbols_processed_total",
    description="Total number of symbols affected by processed corporate actions",
    unit="1",
)

adjustments_applied = meter.create_counter(
    name="ratp_adjustments_applied_total",
    description="Total number of price/history adjustments applied due to corporate actions",
    unit="1",
)

adjustment_failures = meter.create_counter(
    name="ratp_adjustment_failures_total",
    description="Total number of failures while applying corporate action adjustments",
    unit="1",
)

corporate_action_validation_failures = meter.create_counter(
    name="ratp_corporate_action_validation_failures_total",
    description="Total number of corporate action validation failures",
    unit="1",
)

corporate_action_normalization_failures = meter.create_counter(
    name="ratp_corporate_action_normalization_failures_total",
    description="Total number of corporate action normalization failures",
    unit="1",
)

unsupported_corporate_actions = meter.create_counter(
    name="ratp_unsupported_corporate_actions_total",
    description="Total number of unsupported corporate action records encountered",
    unit="1",
)


# --- Performance & distribution metrics ---

corporate_action_ingestion_batch_size = meter.create_histogram(
    name="ratp_corporate_action_ingestion_batch_size",
    description="Number of corporate action records processed per batch",
    unit="1",
)

corporate_action_request_latency_seconds = meter.create_histogram(
    name="ratp_corporate_action_request_latency_seconds",
    description="Latency of upstream corporate action data requests",
    unit="s",
)

corporate_action_processing_duration_seconds = meter.create_histogram(
    name="ratp_corporate_action_processing_duration_seconds",
    description="Time taken to process a corporate action record or batch",
    unit="s",
)

corporate_action_adjustment_duration_seconds = meter.create_histogram(
    name="ratp_corporate_action_adjustment_duration_seconds",
    description="Time taken to apply historical adjustments for a corporate action",
    unit="s",
)

affected_bars_per_action = meter.create_histogram(
    name="ratp_affected_bars_per_action",
    description="Number of historical bars affected by a single corporate action",
    unit="1",
)

actions_per_symbol = meter.create_histogram(
    name="ratp_actions_per_symbol",
    description="Number of corporate actions processed per symbol",
    unit="1",
)
