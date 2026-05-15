from __future__ import annotations

import time
from time import perf_counter
from typing import Any, cast

import httpx
from opentelemetry.trace import StatusCode

from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.execution.services.paper_runtime_order_safeguard_service import (
    PaperRuntimeOrderSafeguardService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.metric_labels import broker_labels
from autonomous_trading_platform.observability.metrics import ratp_broker_api_retries_total
from autonomous_trading_platform.observability.tracing import start_span

COMPONENT = "execution.order_execution"


class OrderExecutionService:
    def __init__(
        self,
        broker_client: Any,
        adapter: Any,
        settings: Any | None = None,
        paper_runtime_order_guard: PaperRuntimeOrderSafeguardService | None = None,
        max_attempts: int = 3,
        initial_backoff_seconds: float = 0.5,
    ) -> None:
        self.client = broker_client
        self.adapter = adapter
        self.paper_runtime_order_guard = (
            paper_runtime_order_guard or PaperRuntimeOrderSafeguardService.from_settings(settings)
        )
        self.max_attempts = max_attempts
        self.initial_backoff_seconds = initial_backoff_seconds

    def submit(self, intent: OrderIntent) -> dict[str, Any]:
        payload = self.adapter.to_payload(intent)
        self.paper_runtime_order_guard.assert_payload_allowed(payload)

        attempt = 0
        backoff = self.initial_backoff_seconds

        while True:
            attempt += 1
            with start_span(
                "order_execution.submit_order",
                timespan=SpanTimespan.REQUEST,
                broker="alpaca",
                broker_endpoint="orders.submit",
                broker_status="attempt",
                retry_attempt=attempt,
                max_attempts=self.max_attempts,
                symbol=intent.symbol,
                order_intent_id=str(intent.intent_id),
                client_order_id=intent.client_order_id,
            ) as span:
                attempt_start = perf_counter()
                try:
                    response = cast(dict[str, Any], self.client.submit_order(payload))
                    span.set_attribute(
                        "ratp.broker_submit_attempt_latency_seconds",
                        perf_counter() - attempt_start,
                    )
                    span.set_attribute("ratp.broker.status", "submitted")
                    span.set_attribute("ratp.retry_succeeded", attempt > 1)
                    return response
                except httpx.HTTPError as exc:
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                    if attempt >= self.max_attempts:
                        span.set_attribute("ratp.retry_exhausted", True)
                        span.set_attribute("ratp.broker.status", "error")
                        raise
                    ratp_broker_api_retries_total.add(
                        1,
                        broker_labels(
                            component=COMPONENT,
                            broker="alpaca",
                            endpoint="orders.submit",
                            status="retry",
                        ),
                    )
                    span.set_attribute("ratp.broker.status", "retry")
                    span.set_attribute("ratp.retry_scheduled", True)
                    span.set_attribute("ratp.retry_backoff_seconds", backoff)
                    time.sleep(backoff)
                    backoff *= 2

    def get_order(self, broker_order_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], self.client.get_order_by_id(broker_order_id))

    def list_open_orders(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.client.list_open_orders())

    def cancel_order(self, broker_order_id: str) -> None:
        self.client.cancel_order(broker_order_id)
