# autonomous_trading_platform/execution/clients/alpaca_broker_client.py

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

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

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
        response = self.client.get("/v2/account")
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

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
        response = self.client.get("/v2/positions")
        response.raise_for_status()
        return cast(list[dict[str, Any]], response.json())

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """
        Fetch a single open position by symbol.
        Returns None if no position exists for that symbol (404).
        """
        response = self.client.get(f"/v2/positions/{symbol}")
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
        response = self.client.get("/v2/stocks/quotes/latest", params=params)
        response.raise_for_status()
        data = cast(dict[str, Any], response.json())
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
        response = self.client.get("/v2/stocks/trades/latest", params=params)
        response.raise_for_status()
        data = cast(dict[str, Any], response.json())
        return cast(dict[str, Any], data.get("trades", {}))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.client.close()
