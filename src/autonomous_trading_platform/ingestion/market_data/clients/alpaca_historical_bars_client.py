from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models.bars import Bar
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


class AlpacaHistoricalBarsClient:
    """
    Client responsible for retrieving historical minute bars from Alpaca.
    """

    def __init__(self, client: StockHistoricalDataClient) -> None:
        self.client = client

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> Iterable[Bar]:
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
        )

        response = self.client.get_stock_bars(request)

        for symbol in response.data:
            yield from response.data[symbol]
