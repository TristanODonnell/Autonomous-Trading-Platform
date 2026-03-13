from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..clients.alpaca_historical_bars_client import AlpacaHistoricalBarsClient
from .bar_ingestion_service import BarIngestionService


class MarketBackfillService:
    """
    Run historical market-data backfills through the normal ingestion pipeline.
    """

    def __init__(
        self,
        session: Session,
        historical_client: AlpacaHistoricalBarsClient,
    ) -> None:
        self.session = session
        self.historical_client = historical_client
        self.bar_ingestion_service = BarIngestionService(session)

    async def backfill(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> None:
        bars = self.historical_client.fetch_bars(
            symbols=symbols,
            start=start,
            end=end,
        )

        for provider_bar in bars:
            await self.bar_ingestion_service.handle_minute_bar(provider_bar)
