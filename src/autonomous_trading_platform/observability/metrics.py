from __future__ import annotations

from opentelemetry import metrics

meter = metrics.get_meter("autonomous_trading_platform")

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
