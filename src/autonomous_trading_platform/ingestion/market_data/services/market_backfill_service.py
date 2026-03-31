from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService

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
        run_id: str,
        audit_logger: AuditLoggingService,
    ) -> None:
        self.session = session
        self.historical_client = historical_client
        self.bar_ingestion_service = BarIngestionService(
            session,
            run_id=run_id,
            audit_logger=audit_logger,
        )

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
            try:
                await self.bar_ingestion_service.handle_minute_bar(provider_bar)
            except ValueError as exc:
                message = str(exc)
                if "Incomplete prior bucket detected" in message:
                    self.bar_ingestion_service.aggregator.drop_incomplete_buckets_for_symbol(
                        provider_bar.symbol
                    )
                    await self.bar_ingestion_service.handle_minute_bar(provider_bar)
                    continue
                raise
