from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.market_data.clients import (
    alpaca_market_data_client as client,
)
from autonomous_trading_platform.ingestion.market_data.services.bar_ingestion_service import (
    BarIngestionService,
)


class IngestBarsJob:
    def __init__(self, expected_symbols: set[str], session: Session) -> None:
        self.session = session
        self.expected_symbols = expected_symbols
        self.received_symbols: set[str] = set()
        self.current_cycle_timestamp: datetime | None = None
        self.ingestion_service = BarIngestionService(session)

    async def on_provider_bar(self, provider_bar) -> None:
        five_min_bar = await self.ingestion_service.handle_minute_bar(provider_bar)

        if five_min_bar is None:
            return

        cycle_timestamp = five_min_bar.timestamp

        if self.current_cycle_timestamp is None:
            self.current_cycle_timestamp = cycle_timestamp

        if cycle_timestamp != self.current_cycle_timestamp:
            self.finalize_cycle(self.current_cycle_timestamp)
            self.received_symbols.clear()
            self.current_cycle_timestamp = cycle_timestamp

        self.received_symbols.add(five_min_bar.symbol)

        print(five_min_bar)

    def finalize_cycle(self, cycle_timestamp: datetime) -> None:
        missing_symbols = self.expected_symbols - self.received_symbols
        # TODO symbols_to_evaluate = self.received_symbols.copy()

        for symbol in missing_symbols:
            print(f"Missing symbol for cycle {cycle_timestamp}: {symbol}")
            # mark_symbol_missing(symbol, cycle_timestamp)

        # evaluate_symbols(
        #     cycle_timestamp=cycle_timestamp,
        #     symbols=symbols_to_evaluate,
        # )

    def ingest_bars_job(self) -> None:
        stream = client.get_stock_data_stream()
        stream.subscribe_bars(self.on_provider_bar, "SPY")
        stream.run()
