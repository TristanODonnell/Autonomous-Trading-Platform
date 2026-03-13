from __future__ import annotations

import os
from datetime import datetime

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


def _get_credentials() -> tuple[str, str]:
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        raise ValueError(
            "Missing Alpaca credentials. "
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment."
        )

    return api_key, secret_key


def get_stock_data_stream() -> StockDataStream:
    """
    Create and return an Alpaca live stock market data stream client.
    """
    api_key, secret_key = _get_credentials()
    return StockDataStream(api_key, secret_key)


def get_stock_historical_client() -> StockHistoricalDataClient:
    """
    Create and return an Alpaca historical stock data client.
    """
    api_key, secret_key = _get_credentials()
    return StockHistoricalDataClient(api_key, secret_key)


def fetch_minute_bars(
    symbols: list[str],
    start: datetime,
    end: datetime,
):
    """
    Fetch minute bars for the provided symbols and time window.
    """
    client = get_stock_historical_client()

    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
    )

    return client.get_stock_bars(request)
