from __future__ import annotations

from datetime import datetime

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from autonomous_trading_platform.config.settings import Settings


def _get_credentials() -> tuple[str, str]:
    settings = Settings()
    api_key = settings.broker_api_key
    secret_key = settings.broker_api_secret

    if not api_key or not secret_key:
        raise ValueError(
            "Missing Alpaca credentials. "
            "Set broker credentials for the configured trading environment."
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


def get_data_feed() -> DataFeed:
    """
    The feed bar requests are made against (``ALPACA_DATA_FEED``, default IEX).

    Left unset, Alpaca serves SIP, which the free plan cannot query for the most recent
    15 minutes, so live ingestion fails on every cycle.
    """
    return DataFeed(Settings().alpaca_data_feed)


def fetch_minute_bars(
    symbols: list[str],
    start: datetime,
    end: datetime,
    feed: DataFeed | None = None,
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
        feed=feed or get_data_feed(),
    )

    return client.get_stock_bars(request)
