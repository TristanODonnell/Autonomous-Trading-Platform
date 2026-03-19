from __future__ import annotations

import time
from typing import Any, cast

import httpx

from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent


class OrderExecutionService:
    def __init__(
        self,
        broker_client: Any,
        adapter: Any,
        max_attempts: int = 3,
        initial_backoff_seconds: float = 0.5,
    ) -> None:
        self.client = broker_client
        self.adapter = adapter
        self.max_attempts = max_attempts
        self.initial_backoff_seconds = initial_backoff_seconds

    def submit(self, intent: OrderIntent) -> dict[str, Any]:
        payload = self.adapter.to_payload(intent)

        attempt = 0
        backoff = self.initial_backoff_seconds

        while True:
            attempt += 1
            try:
                return cast(dict[str, Any], self.client.submit_order(payload))
            except httpx.HTTPError:
                if attempt >= self.max_attempts:
                    raise
                time.sleep(backoff)
                backoff *= 2

    def get_order(self, broker_order_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], self.client.get_order_by_id(broker_order_id))

    def list_open_orders(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.client.list_open_orders())

    def cancel_order(self, broker_order_id: str) -> None:
        self.client.cancel_order(broker_order_id)
