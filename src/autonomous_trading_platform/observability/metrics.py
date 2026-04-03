from __future__ import annotations

from opentelemetry import metrics

meter = metrics.get_meter("autonomous_trading_platform")

# Counter: monotonically increasing event count
trading_cycle_runs = meter.create_counter(
    name="ratp_trading_cycle_runs_total",
    description="Total number of trading cycle executions",
    unit="1",
)

# Histogram: durations / latencies
trading_cycle_duration = meter.create_histogram(
    name="ratp_trading_cycle_duration_seconds",
    description="Trading cycle execution duration",
    unit="s",
)

# UpDownCounter: can go up or down
open_orders = meter.create_up_down_counter(
    name="ratp_open_orders",
    description="Current number of open orders",
    unit="1",
)
