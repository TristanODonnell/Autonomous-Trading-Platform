from __future__ import annotations

import random
import time

from autonomous_trading_platform.observability.metrics import (
    open_orders,
    trading_cycle_duration,
    trading_cycle_runs,
)
from autonomous_trading_platform.observability.telemetry import setup_telemetry


def emit_test_metrics() -> None:
    setup_telemetry("ratp-local-test")

    for i in range(10):
        start = time.perf_counter()

        # simulate work
        time.sleep(0.4 + random.random() * 0.3)

        duration = time.perf_counter() - start

        trading_cycle_runs.add(
            1,
            {"cycle_name": "test_cycle", "environment": "dev"},
        )

        trading_cycle_duration.record(
            duration,
            {"cycle_name": "test_cycle", "environment": "dev"},
        )

        open_orders.add(
            random.choice([-1, 1]),
            {"environment": "dev"},
        )

        print(f"emitted batch {i + 1} duration={duration:.3f}s")

    # give exporter time to flush on short-lived process
    time.sleep(10)


if __name__ == "__main__":
    emit_test_metrics()
