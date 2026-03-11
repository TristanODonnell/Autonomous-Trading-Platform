from __future__ import annotations

from .. import alpaca_market_data_client as client
from ..services.bar_ingestion_service import BarIngestionService


class IngestBarsJob:
    def ingest_bars_job(self) -> None:
        stream = client.get_stock_data_stream()
        ingestion_service = BarIngestionService()

        stream.subscribe_bars(ingestion_service.handle_minute_bar, "SPY")
        stream.run()
