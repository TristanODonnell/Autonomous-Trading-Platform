from typing import Any, cast

import httpx

from autonomous_trading_platform.config.settings import Settings


class AlpacaBrokerClient:
    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.paper_broker_api_key
        self.secret_key = settings.paper_broker_api_secret
        self.base_url = settings.alpaca_base_url

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Missing Alpaca credentials. Set alpaca_api_key and alpaca_secret_key in settings."
            )

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
                "accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post("/v2/orders", json=payload)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def get_order_by_id(self, order_id: str) -> dict[str, Any]:
        response = self.client.get(f"/v2/orders/{order_id}")
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def list_open_orders(self) -> list[dict[str, Any]]:
        response = self.client.get("/v2/orders", params={"status": "open"})
        response.raise_for_status()
        return cast(list[dict[str, Any]], response.json())

    def cancel_order(self, order_id: str) -> None:
        response = self.client.delete(f"/v2/orders/{order_id}")
        response.raise_for_status()

    def close(self) -> None:
        self.client.close()
