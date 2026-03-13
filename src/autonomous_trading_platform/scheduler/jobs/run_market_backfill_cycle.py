from __future__ import annotations

import asyncio
import uuid
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
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from src.db import get_session


def run_market_backfill_cycle() -> None:
    """
    Entry point for the Airflow historical market-data backfill DAG.
    """
    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    run_id = str(uuid.uuid4())
    # TODO: Replace with universe symbols
    symbols = ["SPY"]
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    base_metadata = {
        "pipeline": "market_backfill",
        "symbols": symbols,
        "backfill_start": start.isoformat(),
        "backfill_end": end.isoformat(),
    }

    try:
        audit_logger.record_run_started(
            run_id=run_id,
            component="market_backfill",
            metadata=base_metadata,
        )
        raw_client = get_stock_historical_client()
        historical_client = AlpacaHistoricalBarsClient(raw_client)

        job = BackfillMarketBarsJob(
            session=session,
            historical_client=historical_client,
            run_id=run_id,
            audit_logger=audit_logger,
        )

        asyncio.run(
            job.run(
                symbols=symbols,
                start=start,
                end=end,
            )
        )
        audit_logger.record_run_completed(
            run_id=run_id,
            component="market_backfill",
            metadata=base_metadata,
        )
    except Exception as exc:
        audit_logger.record_run_failed(
            run_id=run_id,
            component="market_backfill",
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        raise
    finally:
        session.close()
