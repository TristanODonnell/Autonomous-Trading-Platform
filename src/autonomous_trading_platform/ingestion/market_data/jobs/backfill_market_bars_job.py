from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..clients.alpaca_historical_bars_client import AlpacaHistoricalBarsClient
from ..services.market_backfill_service import MarketBackfillService


class BackfillMarketBarsJob:
    """
    Job wrapper for historical market-data backfill.
    """

    def __init__(
        self,
        session: Session,
        historical_client: AlpacaHistoricalBarsClient,
    ) -> None:
        self.session = session
        self.backfill_service = MarketBackfillService(
            session=session,
            historical_client=historical_client,
        )

    async def run(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> None:
        await self.backfill_service.backfill(
            symbols=symbols,
            start=start,
            end=end,
        )
