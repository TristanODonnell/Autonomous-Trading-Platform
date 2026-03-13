from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.market_data.clients.alpaca_historical_bars_client import (
    AlpacaHistoricalBarsClient,
)
from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
    get_stock_historical_client,
)
from autonomous_trading_platform.ingestion.market_data.jobs.backfill_market_bars_job import (
    BackfillMarketBarsJob,
)
from src.db import get_session


def run_market_backfill_cycle() -> None:
    """
    Entry point for the Airflow historical market-data backfill DAG.
    """
    session: Session = get_session()

    try:
        raw_client = get_stock_historical_client()
        historical_client = AlpacaHistoricalBarsClient(raw_client)

        job = BackfillMarketBarsJob(
            session=session,
            historical_client=historical_client,
        )

        # Example bootstrap window.
        end = datetime.now(UTC)
        start = end - timedelta(days=30)

        # Replace with your actual bootstrap universe later.
        symbols = ["SPY"]

        asyncio.run(
            job.run(
                symbols=symbols,
                start=start,
                end=end,
            )
        )
    finally:
        session.close()
