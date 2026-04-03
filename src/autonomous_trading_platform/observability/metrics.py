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
