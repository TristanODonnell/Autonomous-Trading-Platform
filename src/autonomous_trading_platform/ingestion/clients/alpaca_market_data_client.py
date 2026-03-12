from __future__ import annotations

import os

from alpaca.data.live import StockDataStream


def get_stock_data_stream() -> StockDataStream:
    """
    Create and return an Alpaca stock market data stream client.
    """
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        raise ValueError(
            "Missing Alpaca credentials. "
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment."
        )

    return StockDataStream(api_key, secret_key)
