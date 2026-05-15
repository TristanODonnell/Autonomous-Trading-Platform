# autonomous_trading_platform/execution/clients/alpaca_broker_client.py

from time import perf_counter
from typing import Any, cast
from uuid import uuid4

import httpx
from opentelemetry.trace import StatusCode

from autonomous_trading_platform.config.broker_config_validator import (
    ALPACA_LIVE_BASE_URL,
    ALPACA_PAPER_BASE_URL,
)
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.metrics import (
    ratp_broker_api_failures_total,
    ratp_broker_api_latency_seconds,
    ratp_broker_api_requests_total,
)
from autonomous_trading_platform.observability.tracing import start_span


class AlpacaBrokerClient:
    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.broker_api_key
        self.secret_key = settings.broker_api_secret
        self.base_url = settings.alpaca_base_url

        if not self.api_key or not self.secret_key:
            raise ValueError("Missing Alpaca credentials for the configured trading environment.")
        if self.base_url not in {ALPACA_PAPER_BASE_URL, ALPACA_LIVE_BASE_URL}:
            raise ValueError(f"Invalid Alpaca base URL: {self.base_url}.")

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
                "accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request("POST", "/v2/orders", endpoint_tag="orders.submit", json=payload),
        )

    def get_order_by_id(self, order_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request("GET", f"/v2/orders/{order_id}", endpoint_tag="orders.get"),
        )

    def list_open_orders(self) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            self._request(
                "GET",
                "/v2/orders",
                endpoint_tag="orders.list_open",
                params={"status": "open"},
            ),
        )

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/v2/orders/{order_id}", endpoint_tag="orders.cancel")

    # ------------------------------------------------------------------
    # Account & Positions
    # ------------------------------------------------------------------

    def get_account(self) -> dict[str, Any]:
        """
        Fetch the current account details from Alpaca.

        Key fields returned:
            equity          — total account equity (cash + market value of positions)
            cash            — settled cash available
            buying_power    — available buying power
            portfolio_value — alias for equity in most Alpaca account types
        """
        return cast(dict[str, Any], self._request("GET", "/v2/account", endpoint_tag="account.get"))

    def get_positions(self) -> list[dict[str, Any]]:
        """
        Fetch all open positions from Alpaca.

        Each position dict includes:
            symbol          — ticker
            qty             — number of shares held (string)
            avg_entry_price — average cost basis (string)
            market_value    — current market value (string)
            unrealized_pl   — unrealized P&L (string)
            current_price   — latest price for this position (string)
        """
        return cast(
            list[dict[str, Any]],
            self._request("GET", "/v2/positions", endpoint_tag="positions.list"),
        )

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """
        Fetch a single open position by symbol.
        Returns None if no position exists for that symbol (404).
        """
        response = self._raw_request(
            "GET",
            f"/v2/positions/{symbol}",
            endpoint_tag="positions.get",
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_latest_quotes(self, symbols: list[str]) -> dict[str, Any]:
        """
        Fetch the latest quotes for a list of symbols via the Alpaca
        market data API.

        Returns a dict keyed by symbol, each value containing:
            ap   — ask price (float)
            bp   — bid price (float)
            as_  — ask size
            bs   — bid size
            t    — timestamp

        Uses the mid-price (ask + bid / 2) for sizing — callers should
        extract via get_mid_prices() helper below.

        Note: uses the /v2/stocks/quotes/latest endpoint which requires
        the data base_url (data.alpaca.markets), not the trading base_url.
        Callers must ensure settings.alpaca_data_base_url is configured.
        """
        params = {"symbols": ",".join(symbols), "feed": "iex"}
        data = cast(
            dict[str, Any],
            self._request(
                "GET",
                "/v2/stocks/quotes/latest",
                endpoint_tag="market_data.latest_quotes",
                params=params,
            ),
        )
        return cast(dict[str, Any], data.get("quotes", {}))

    def get_latest_trades(self, symbols: list[str]) -> dict[str, Any]:
        """
        Fetch the latest trade price for a list of symbols.

        Returns a dict keyed by symbol, each value containing:
            p   — trade price (float)
            s   — trade size
            t   — timestamp
        """
        params = {"symbols": ",".join(symbols), "feed": "iex"}
        data = cast(
            dict[str, Any],
            self._request(
                "GET",
                "/v2/stocks/trades/latest",
                endpoint_tag="market_data.latest_trades",
                params=params,
            ),
        )
        return cast(dict[str, Any], data.get("trades", {}))

    def _request(
        self,
        method: str,
        path: str,
        *,
        endpoint_tag: str,
        **kwargs: Any,
    ) -> Any:
        response = self._raw_request(method, path, endpoint_tag=endpoint_tag, **kwargs)
        response.raise_for_status()
        if response.status_code == 204:
            return None
        return response.json()

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        endpoint_tag: str,
        **kwargs: Any,
    ) -> httpx.Response:
        request_id = f"ratp-{uuid4()}"
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["X-RATP-Request-ID"] = request_id
        labels = {"broker": "alpaca", "endpoint": endpoint_tag, "method": method.upper()}

        with start_span(
            "broker_http_request",
            timespan=SpanTimespan.REQUEST,
            broker="alpaca",
            broker_endpoint=endpoint_tag,
            http_method=method.upper(),
            http_route=path,
            request_id=request_id,
        ) as span:
            start = perf_counter()
            ratp_broker_api_requests_total.add(1, labels)
            try:
                response = self.client.request(method, path, headers=headers, **kwargs)
                latency = perf_counter() - start
                ratp_broker_api_latency_seconds.record(
                    latency,
                    {**labels, "status_code": str(response.status_code)},
                )
                span.set_attribute("http.status_code", response.status_code)
                span.set_attribute("ratp.broker_api_latency_seconds", latency)
                if response.is_error:
                    ratp_broker_api_failures_total.add(
                        1,
                        {**labels, "status_code": str(response.status_code)},
                    )
                    span.set_status(StatusCode.ERROR, f"HTTP {response.status_code}")
                return response
            except Exception as exc:
                latency = perf_counter() - start
                ratp_broker_api_latency_seconds.record(latency, {**labels, "status_code": "error"})
                ratp_broker_api_failures_total.add(1, {**labels, "status_code": "error"})
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
                raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.client.close()
