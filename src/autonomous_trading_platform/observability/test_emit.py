from __future__ import annotations

import random
import time

from dotenv import load_dotenv
from opentelemetry import trace

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    open_orders,
    trading_cycle_duration,
    trading_cycle_runs,
)
from autonomous_trading_platform.observability.telemetry import setup_telemetry


def emit_test_metrics() -> None:
    load_dotenv()
    setup_telemetry("ratp-local-test")

    logger = get_logger(__name__)
    tracer = trace.get_tracer(__name__)

    for i in range(10):
        with tracer.start_as_current_span("test_emit_batch") as span:
            span.set_attribute("batch.number", i)
            span.set_attribute("environment", "dev")

            start = time.perf_counter()

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

            logger.info("loki_smoke_test_batch_completed", extra={"batch_number": i})
            print(f"emitted batch {i + 1} duration={duration:.3f}s")

    logger.warning("loki_smoke_test_warning")
    logger.error("loki_smoke_test_error")

    time.sleep(5)


if __name__ == "__main__":
    emit_test_metrics()
