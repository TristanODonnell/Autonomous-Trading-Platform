from __future__ import annotations

import os
from dataclasses import dataclass

from opentelemetry import metrics

from autonomous_trading_platform.observability.runtime_context import get_runtime_context

meter = metrics.get_meter("autonomous_trading_platform")


@dataclass
class RuntimeFreshnessState:
    ingestion_lag_seconds: float | None = None
    symbols_expected: int | None = None
    symbols_received: int | None = None


_runtime_freshness_state = RuntimeFreshnessState()


def record_runtime_freshness(
    *,
    ingestion_lag_seconds: float | None = None,
    symbols_expected: int | None = None,
    symbols_received: int | None = None,
) -> None:
    if ingestion_lag_seconds is not None:
        _runtime_freshness_state.ingestion_lag_seconds = max(0.0, float(ingestion_lag_seconds))
    if symbols_expected is not None:
        _runtime_freshness_state.symbols_expected = max(0, int(symbols_expected))
    if symbols_received is not None:
        _runtime_freshness_state.symbols_received = max(0, int(symbols_received))


# ==========================================
# INCIDENT METRICS
# ==========================================

incident_events = meter.create_counter(
    name="ratp_incident_events_total",
    description="Total number of incident events",
    unit="1",
)

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

# --- Job-level metrics ---

# --- Ingestion Readiness ---

ingestion_readiness_job_runs = meter.create_counter(
    name="ratp_ingestion_readiness_job_runs_total",
    description="Total number of ingestion readiness job executions",
    unit="1",
)

ingestion_readiness_job_failures = meter.create_counter(
    name="ratp_ingestion_readiness_job_failures_total",
    description="Total number of ingestion readiness job failures",
    unit="1",
)

ingestion_readiness_job_duration = meter.create_histogram(
    name="ratp_ingestion_readiness_job_duration_seconds",
    description="Ingestion readiness job execution duration",
    unit="s",
)

ingestion_readiness_job_ingestion_lag = meter.create_histogram(
    name="ratp_ingestion_readiness_job_ingestion_lag_seconds",
    description="Ingestion readiness job ingestion lag seconds",
    unit="s",
)


# --- Strategy Evaluation ---

evaluate_strategy_job_runs = meter.create_counter(
    name="ratp_evaluate_strategy_job_runs_total",
    description="Total number of evaluate_strategy job executions",
    unit="1",
)

evaluate_strategy_job_failures = meter.create_counter(
    name="ratp_evaluate_strategy_job_failures_total",
    description="Total number of evaluate_strategy job failures",
    unit="1",
)

evaluate_strategy_job_duration = meter.create_histogram(
    name="ratp_evaluate_strategy_job_duration_seconds",
    description="Evaluate_strategy job execution duration",
    unit="s",
)

# --- Trading Evaluation ---

trading_evaluation_job_runs = meter.create_counter(
    name="ratp_trading_evaluation_job_runs_total",
    description="Total number of trading_evaluation job executions",
    unit="1",
)

trading_evaluation_job_failures = meter.create_counter(
    name="ratp_trading_evaluation_job_failures_total",
    description="Total number of trading_evaluation job failures",
    unit="1",
)

trading_evaluation_job_duration = meter.create_histogram(
    name="ratp_trading_evaluation_job_duration_seconds",
    description="Trading_evaluation job execution duration",
    unit="s",
)

# --- Order Submission ---

order_submission_job_runs = meter.create_counter(
    name="ratp_order_submission_job_runs_total",
    description="Total number of order submission job executions",
    unit="1",
)

order_submission_job_failures = meter.create_counter(
    name="ratp_order_submission_job_failures_total",
    description="Total number of order submission job failures",
    unit="1",
)

order_submission_job_duration = meter.create_histogram(
    name="ratp_order_submission_job_duration_seconds",
    description="Order submission job execution duration",
    unit="s",
)

order_submission_latency_seconds = meter.create_histogram(
    name="ratp_order_submission_latency_seconds",
    description="Latency of broker order submission requests",
    unit="s",
)

order_execution_signal_to_submit_latency_seconds = meter.create_histogram(
    name="ratp_order_execution_signal_to_submit_latency_seconds",
    description="Latency from signal generation to broker order submission attempt",
    unit="s",
)

order_execution_submit_to_ack_latency_seconds = meter.create_histogram(
    name="ratp_order_execution_submit_to_ack_latency_seconds",
    description="Latency from broker order submission attempt to broker acknowledgement",
    unit="s",
)

order_execution_ack_to_fill_latency_seconds = meter.create_histogram(
    name="ratp_order_execution_ack_to_fill_latency_seconds",
    description="Latency from broker acknowledgement to fill receipt",
    unit="s",
)

order_execution_total_latency_seconds = meter.create_histogram(
    name="ratp_order_execution_total_latency_seconds",
    description="Total latency from signal generation to fill receipt or broker acknowledgement",
    unit="s",
)

order_submission_risk_rejection_count = meter.create_counter(
    name="ratp_order_submission_risk_rejection_count_total",
    description="Total number of order submission risk rejection count",
    unit="1",
)

ratp_broker_api_requests_total = meter.create_counter(
    name="ratp_broker_api_requests_total",
    description="Total number of broker API requests",
    unit="1",
)

ratp_broker_api_failures_total = meter.create_counter(
    name="ratp_broker_api_failures_total",
    description="Total number of failed broker API requests",
    unit="1",
)

ratp_broker_api_latency_seconds = meter.create_histogram(
    name="ratp_broker_api_latency_seconds",
    description="Broker API request latency",
    unit="s",
)

ratp_broker_api_retries_total = meter.create_counter(
    name="ratp_broker_api_retries_total",
    description="Total number of broker API retry attempts",
    unit="1",
)

# --- Order Reconciliation ---

order_reconciliation_job_runs = meter.create_counter(
    name="ratp_order_reconciliation_job_runs_total",
    description="Total number of order reconciliation job executions",
    unit="1",
)

order_reconciliation_job_failures = meter.create_counter(
    name="ratp_order_reconciliation_job_failures_total",
    description="Total number of order reconciliation job failures",
    unit="1",
)

order_reconciliation_job_duration = meter.create_histogram(
    name="ratp_order_reconciliation_job_duration_seconds",
    description="Order reconciliation job execution duration",
    unit="s",
)

order_reconciliation_mismatches = meter.create_counter(
    name="ratp_order_reconciliation_mismatches_total",
    description="Total number of reconciliation mismatches detected",
    unit="1",
)

# --- External Broker Reconciliation metrics (TASK-514 to TASK-519) ---

# TASK-514: open tracked orders whose broker_order_id is absent from broker's open order list
ratp_unreconciled_orders = meter.create_up_down_counter(
    name="ratp_unreconciled_orders",
    description="Current number of platform-open orders not present in broker's open order list",
    unit="1",
)

# TASK-515: fills where platform-side cumulative qty exceeds broker-reported filled qty
ratp_duplicate_fills_detected_total = meter.create_counter(
    name="ratp_duplicate_fills_detected_total",
    description="Total number of possible duplicate fill events detected during reconciliation",
    unit="1",
)

# TASK-516: signed cash drift (platform internal cash minus broker-reported cash)
ratp_cash_drift_amount = meter.create_histogram(
    name="ratp_cash_drift_amount",
    description="Signed cash drift (platform minus broker) detected during external reconciliation",
    unit="USD",
)

# TASK-517: count of per-symbol position drift events
ratp_position_drift_count_total = meter.create_counter(
    name="ratp_position_drift_count_total",
    description="Total number of per-symbol position drift events detected during reconciliation",
    unit="1",
)

# TASK-518: signed position quantity drift per symbol (platform minus broker)
ratp_position_quantity_drift = meter.create_histogram(
    name="ratp_position_quantity_drift",
    description="Signed position quantity drift (platform minus broker) per symbol during reconciliation",
    unit="1",
)

# TASK-519: signed equity drift (platform internal equity minus broker-reported equity)
ratp_equity_drift_amount = meter.create_histogram(
    name="ratp_equity_drift_amount",
    description="Signed equity drift (platform minus broker) detected during external reconciliation",
    unit="USD",
)

# --- Risk Snapshot ---

risk_snapshot_job_runs = meter.create_counter(
    name="ratp_risk_snapshot_job_runs_total",
    description="Total number of risk_snapshot job executions",
    unit="1",
)

risk_snapshot_job_failures = meter.create_counter(
    name="ratp_risk_snapshot_job_failures_total",
    description="Total number of risk_snapshot job failures",
    unit="1",
)

risk_snapshot_job_duration = meter.create_histogram(
    name="ratp_risk_snapshot_job_duration_seconds",
    description="Risk_snapshot job execution duration",
    unit="s",
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

# --- Job-level metrics ---

ingestion_job_runs = meter.create_counter(
    name="ratp_ingestion_job_runs_total",
    description="Total number of ingestion job executions",
    unit="1",
)

ingestion_job_failures = meter.create_counter(
    name="ratp_ingestion_job_failures_total",
    description="Total number of ingestion job failures",
    unit="1",
)

ingestion_job_duration = meter.create_histogram(
    name="ratp_ingestion_job_duration_seconds",
    description="Ingestion job execution duration",
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
    if _runtime_freshness_state.ingestion_lag_seconds is None:
        return []
    return [metrics.Observation(_runtime_freshness_state.ingestion_lag_seconds)]


ingestion_lag_seconds = meter.create_observable_gauge(
    name="ratp_ingestion_lag_seconds",
    description="Current lag between latest available market data and system ingestion",
    callbacks=[_ingestion_lag_callback],
    unit="s",
)


def _symbols_expected_callback(options):
    if _runtime_freshness_state.symbols_expected is None:
        return []
    return [metrics.Observation(_runtime_freshness_state.symbols_expected)]


symbols_expected = meter.create_observable_gauge(
    name="ratp_symbols_expected",
    description="Number of symbols expected for ingestion in current cycle",
    callbacks=[_symbols_expected_callback],
    unit="1",
)


def _symbols_received_callback(options):
    if _runtime_freshness_state.symbols_received is None:
        return []
    return [metrics.Observation(_runtime_freshness_state.symbols_received)]


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

# --- Job-level metrics ---

market_backfill_job_runs = meter.create_counter(
    name="ratp_market_backfill_job_runs_total",
    description="Total number of market backfill job executions",
    unit="1",
)

market_backfill_job_failures = meter.create_counter(
    name="ratp_market_backfill_job_failures_total",
    description="Total number of market backfill job failures",
    unit="1",
)

market_backfill_job_duration = meter.create_histogram(
    name="ratp_market_backfill_job_duration_seconds",
    description="Market backfill job execution duration",
    unit="s",
)

backfill_throughput = meter.create_histogram(
    name="ratp_backfill_throughput_bars_per_second",
    description="Backfill throughput measured in bars processed per second",
    unit="bars/s",
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

# --- Job-level metrics ---

corporate_action_ingestion_job_runs = meter.create_counter(
    name="ratp_corporate_action_ingestion_job_runs_total",
    description="Total number of corporate action ingestion job executions",
    unit="1",
)

corporate_action_ingestion_job_failures = meter.create_counter(
    name="ratp_corporate_action_ingestion_job_failures_total",
    description="Total number of corporate action ingestion job failures",
    unit="1",
)

corporate_action_ingestion_job_duration = meter.create_histogram(
    name="ratp_corporate_action_ingestion_job_duration_seconds",
    description="Corporate action ingestion job execution duration",
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

# =========================
# FEATURE PIPELINE CYCLE METRICS
# =========================

feature_pipeline_cycle_duration = meter.create_histogram(
    name="ratp_feature_pipeline_cycle_duration_seconds",
    description="Feature pipeline cycle execution duration",
    unit="s",
)
feature_pipeline_cycle_failures = meter.create_counter(
    name="ratp_feature_pipeline_cycle_failures_total",
    description="Total number of Feature pipeline cycle failures",
    unit="1",
)
feature_pipeline_cycle_runs = meter.create_counter(
    name="ratp_feature_pipeline_cycle_runs_total",
    description="Total number of Feature pipeline cycle executions",
    unit="1",
)
feature_pipeline_cycle_step_duration = meter.create_histogram(
    name="ratp_feature_pipeline_cycle_step_duration_seconds",
    description="Feature pipeline cycle step execution duration",
    unit="s",
)
feature_pipeline_cycle_step_runs = meter.create_counter(
    name="ratp_feature_pipeline_cycle_step_runs_total",
    description="Total number of Feature pipeline step executions",
    unit="1",
)

# =========================
# EXPERIMENT PIPELINE CYCLE METRICS
# =========================

experiment_pipeline_cycle_runs = meter.create_counter(
    name="ratp_experiment_pipeline_cycle_runs_total",
    description="Total number of experiment pipeline cycle executions",
    unit="1",
)

experiment_pipeline_cycle_failures = meter.create_counter(
    name="ratp_experiment_pipeline_cycle_failures_total",
    description="Total number of experiment pipeline cycle failures",
    unit="1",
)

experiment_pipeline_cycle_duration = meter.create_histogram(
    name="ratp_experiment_pipeline_cycle_duration_seconds",
    description="Experiment pipeline cycle execution duration",
    unit="s",
)

experiment_pipeline_cycle_step_runs = meter.create_counter(
    name="ratp_experiment_pipeline_cycle_step_runs_total",
    description="Total number of experiment pipeline cycle step executions",
    unit="1",
)

experiment_pipeline_cycle_step_duration = meter.create_histogram(
    name="ratp_experiment_pipeline_cycle_step_duration_seconds",
    description="Experiment pipeline cycle step execution duration",
    unit="s",
)

# =========================
# GOVERNANCE / ALLOCATION CYCLE METRICS
# =========================

governance_cycle_runs = meter.create_counter(
    name="ratp_governance_cycle_runs_total",
    description="Total number of governance cycle executions",
    unit="1",
)

governance_cycle_failures = meter.create_counter(
    name="ratp_governance_cycle_failures_total",
    description="Total number of governance cycle failures",
    unit="1",
)

governance_cycle_duration = meter.create_histogram(
    name="ratp_governance_cycle_duration_seconds",
    description="Governance cycle execution duration",
    unit="s",
)

auto_demotion_scan_total = meter.create_counter(
    name="ratp_auto_demotion_scan_total",
    description="Total number of auto-demotion scans",
    unit="1",
)

auto_demotion_breach_total = meter.create_counter(
    name="ratp_auto_demotion_breach_total",
    description="Total number of auto-demotion breaches detected",
    unit="1",
)

strategy_demotion_total = meter.create_counter(
    name="ratp_strategy_demotion_total",
    description="Total number of strategy demotions executed",
    unit="1",
)

auto_demotion_scan_duration_seconds = meter.create_histogram(
    name="ratp_auto_demotion_scan_duration_seconds",
    description="Auto-demotion scan duration",
    unit="s",
)

allocation_cycle_runs = meter.create_counter(
    name="ratp_allocation_cycle_runs_total",
    description="Total number of allocation cycle executions",
    unit="1",
)

allocation_cycle_failures = meter.create_counter(
    name="ratp_allocation_cycle_failures_total",
    description="Total number of allocation cycle failures",
    unit="1",
)

allocation_cycle_duration = meter.create_histogram(
    name="ratp_allocation_cycle_duration_seconds",
    description="Allocation cycle execution duration",
    unit="s",
)

# =========================
# UNIVERSE METRICS
# =========================

universe_candidate_count = meter.create_histogram(
    name="ratp_universe_candidate_count",
    description="Number of symbols considered during candidate universe generation",
    unit="1",
)

universe_rejected_symbol_count = meter.create_histogram(
    name="ratp_universe_rejected_symbol_count",
    description="Number of symbols rejected during candidate universe generation",
    unit="1",
)

universe_ranking_duration_seconds = meter.create_histogram(
    name="ratp_universe_ranking_duration_seconds",
    description="Duration of universe candidate ranking",
    unit="s",
)

universe_rebalance_duration_seconds = meter.create_histogram(
    name="ratp_universe_rebalance_duration_seconds",
    description="Duration of universe rebalance proposal generation",
    unit="s",
)

universe_rotation_duration_seconds = meter.create_histogram(
    name="ratp_universe_rotation_duration_seconds",
    description="Duration of universe rotation or rollback operations",
    unit="s",
)

universe_rebalance_churn_pct = meter.create_histogram(
    name="ratp_universe_rebalance_churn_pct",
    description="Churn percentage applied in universe rebalance operations",
    unit="1",
)

universe_symbols_added_count = meter.create_histogram(
    name="ratp_universe_symbols_added_count",
    description="Number of symbols added by universe rebalance or rotation",
    unit="1",
)

universe_symbols_removed_count = meter.create_histogram(
    name="ratp_universe_symbols_removed_count",
    description="Number of symbols removed by universe rebalance or rotation",
    unit="1",
)

universe_symbols_retained_count = meter.create_histogram(
    name="ratp_universe_symbols_retained_count",
    description="Number of symbols retained by universe rebalance or rotation",
    unit="1",
)

universe_invalid_member_count = meter.create_histogram(
    name="ratp_universe_invalid_member_count",
    description="Number of invalid members found during universe validation",
    unit="1",
)

universe_validation_failures = meter.create_counter(
    name="ratp_universe_validation_failures_total",
    description="Total number of universe validation failures",
    unit="1",
)

universe_resolution_latency_seconds = meter.create_histogram(
    name="ratp_universe_resolution_latency_seconds",
    description="Latency of universe resolution operations",
    unit="s",
)

universe_rejected_trade_count = meter.create_counter(
    name="ratp_universe_rejected_trade_count_total",
    description="Total number of trade rejections due to symbol being outside the active universe",
    unit="1",
)

universe_rejected_trade_outside_universe_count = universe_rejected_trade_count

_universe_active_size_state: int | None = None


def record_universe_active_size(size: int) -> None:
    global _universe_active_size_state
    _universe_active_size_state = size


def _universe_active_size_callback(options):
    if _universe_active_size_state is None:
        return []
    return [
        metrics.Observation(
            _universe_active_size_state,
            _universe_metric_attrs("universe_resolution"),
        )
    ]


universe_active_size = meter.create_observable_gauge(
    name="ratp_universe_active_size",
    description="Number of symbols in the current active universe",
    callbacks=[_universe_active_size_callback],
    unit="1",
)


def _universe_metric_attrs(component: str, **extra: str) -> dict[str, str]:
    ctx = get_runtime_context()
    environment = (
        (ctx.environment if ctx else None)
        or os.getenv("APP_ENV")
        or os.getenv("TRADING_ENVIRONMENT")
        or "unknown"
    )
    attrs = {"environment": str(environment), "component": component}
    attrs.update({key: str(value) for key, value in extra.items() if value is not None})
    return attrs


def record_universe_candidate_generation_metrics(
    *,
    candidate_count: int,
    rejected_count: int,
    ranking_duration_seconds: float,
) -> None:
    attrs = _universe_metric_attrs("universe_candidate_generation")
    universe_candidate_count.record(max(0, int(candidate_count)), attrs)
    universe_rejected_symbol_count.record(max(0, int(rejected_count)), attrs)
    universe_ranking_duration_seconds.record(max(0.0, float(ranking_duration_seconds)), attrs)


def record_universe_rebalance_metrics(
    *,
    duration_seconds: float,
    churn_pct: float,
    added_count: int,
    removed_count: int,
    retained_count: int,
) -> None:
    attrs = _universe_metric_attrs("universe_rebalance")
    universe_rebalance_duration_seconds.record(max(0.0, float(duration_seconds)), attrs)
    universe_rebalance_churn_pct.record(max(0.0, float(churn_pct)), attrs)
    universe_symbols_added_count.record(max(0, int(added_count)), attrs)
    universe_symbols_removed_count.record(max(0, int(removed_count)), attrs)
    universe_symbols_retained_count.record(max(0, int(retained_count)), attrs)


def record_universe_rotation_metrics(
    *,
    duration_seconds: float,
    churn_pct: float | None,
    added_count: int,
    removed_count: int,
    retained_count: int,
    rotation_type: str,
) -> None:
    attrs = _universe_metric_attrs("universe_rotation", rotation_type=rotation_type)
    universe_rotation_duration_seconds.record(max(0.0, float(duration_seconds)), attrs)
    if churn_pct is not None:
        universe_rebalance_churn_pct.record(max(0.0, float(churn_pct)), attrs)
    universe_symbols_added_count.record(max(0, int(added_count)), attrs)
    universe_symbols_removed_count.record(max(0, int(removed_count)), attrs)
    universe_symbols_retained_count.record(max(0, int(retained_count)), attrs)


def record_universe_validation_metrics(*, invalid_member_count: int, failure_count: int) -> None:
    attrs = _universe_metric_attrs("universe_validation")
    universe_invalid_member_count.record(max(0, int(invalid_member_count)), attrs)
    if failure_count > 0:
        universe_validation_failures.add(int(failure_count), attrs)


def record_universe_resolution_latency(duration_seconds: float) -> None:
    universe_resolution_latency_seconds.record(
        max(0.0, float(duration_seconds)),
        _universe_metric_attrs("universe_resolution"),
    )


def record_rejected_trade_outside_universe(count: int = 1) -> None:
    universe_rejected_trade_count.add(
        max(0, int(count)),
        _universe_metric_attrs("universe_runtime_guard"),
    )


# =========================
# RESEARCH PIPELINE METRICS
# =========================

research_stage_runs = meter.create_counter(
    name="ratp_research_stage_runs_total",
    description="Total number of research pipeline stage executions",
    unit="1",
)

research_stage_duration = meter.create_histogram(
    name="ratp_research_stage_duration_seconds",
    description="Research pipeline stage execution duration",
    unit="s",
)

research_stage_survivors_entered = meter.create_histogram(
    name="ratp_research_stage_survivors_entered",
    description="Number of strategy candidates entering each research pipeline stage",
    unit="1",
)

research_stage_survivors_passed = meter.create_histogram(
    name="ratp_research_stage_survivors_passed",
    description="Number of strategy candidates passing each research pipeline stage",
    unit="1",
)

# ===================================
# RESEARCH PARALLEL EXECUTION METRICS
# ===================================

research_parallel_unit_runs = meter.create_counter(
    name="ratp_research_parallel_unit_runs_total",
    description="Total number of research parallel execution unit completions",
    unit="1",
)

research_parallel_unit_duration = meter.create_histogram(
    name="ratp_research_parallel_unit_duration_seconds",
    description="Research parallel execution unit duration",
    unit="s",
)

# ==============================
# RESEARCH CHECKPOINT METRICS
# ==============================

research_checkpoint_transitions = meter.create_counter(
    name="ratp_research_checkpoint_transitions_total",
    description="Total number of research checkpoint state transitions",
    unit="1",
)

research_checkpoint_duration = meter.create_histogram(
    name="ratp_research_checkpoint_duration_seconds",
    description="Research checkpoint duration from creation to terminal state",
    unit="s",
)

# =========================
# RESEARCH CACHE METRICS
# =========================

research_cache_lookups = meter.create_counter(
    name="ratp_research_cache_lookups_total",
    description="Total number of research cache lookup attempts",
    unit="1",
)

# ==============================
# RESEARCH VALIDATION METRICS
# ==============================

research_validation_stage_runs = meter.create_counter(
    name="ratp_research_validation_stage_runs_total",
    description="Total number of research validation stage executions",
    unit="1",
)

research_validation_stage_duration = meter.create_histogram(
    name="ratp_research_validation_stage_duration_seconds",
    description="Research validation stage execution duration",
    unit="s",
)

research_robustness_score = meter.create_histogram(
    name="ratp_research_robustness_score",
    description="Research strategy robustness scores",
    unit="1",
)

# ==================================
# RESEARCH REGIME ANALYSIS METRICS
# ==================================

research_regime_analysis_duration = meter.create_histogram(
    name="ratp_research_regime_analysis_duration_seconds",
    description="Research regime analysis execution duration",
    unit="s",
)

research_regime_dimension_coverage_pct = meter.create_histogram(
    name="ratp_research_regime_dimension_coverage_pct",
    description="Fraction of regime buckets with observations per dimension",
    unit="1",
)

# ================================
# RESEARCH INTELLIGENCE METRICS
# ================================

research_intelligence_ranking_score = meter.create_histogram(
    name="ratp_research_intelligence_ranking_score",
    description="Research intelligence candidate composite ranking scores",
    unit="1",
)

research_intelligence_cluster_count = meter.create_histogram(
    name="ratp_research_intelligence_cluster_count",
    description="Number of strategy clusters produced by intelligence clustering",
    unit="1",
)

research_intelligence_overfit_estimate = meter.create_histogram(
    name="ratp_research_intelligence_overfit_estimate",
    description="Research intelligence overfitting probability estimates",
    unit="1",
)
