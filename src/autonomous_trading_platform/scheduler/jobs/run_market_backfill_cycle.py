from __future__ import annotations

import asyncio
import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
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
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from src.db import get_session


def run_market_backfill_cycle() -> None:
    """
    Entry point for the Airflow historical market-data backfill DAG.
    """
    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)
    run_id = uuid.uuid4()
    # TODO: Replace with universe symbols
    symbols = ["SPY"]
    end = datetime.now(UTC)
    start = end - timedelta(days=30)

    manifest = RunManifest(
        run_id=run_id,
        run_type=RunType.BACKTEST,
        created_at=end,
        environment="local",
        broker="alpaca",
        broker_account_id="paper",
        strategy_id="baseline_strategy",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000.00"),
        interval=BarInterval.ONE_DAY,
        start_date=start.date(),
        end_date=end.date(),
        dataset_version="v1",
        universe_version="v1",
        git_commit="dev",
        python_version=platform.python_version(),
        notes="Historical market bar backfill cycle",
    )
    manifest_service.save(manifest)

    base_metadata = {
        "pipeline": "market_backfill",
        "symbols": symbols,
        "backfill_start": start.isoformat(),
        "backfill_end": end.isoformat(),
        "manifest_run_type": manifest.run_type.value,
        "manifest_interval": manifest.interval.value,
    }

    try:
        audit_logger.record_run_started(
            run_id=str(run_id),
            component="market_backfill",
            metadata=base_metadata,
        )
        raw_client = get_stock_historical_client()
        historical_client = AlpacaHistoricalBarsClient(raw_client)

        job = BackfillMarketBarsJob(
            session=session,
            historical_client=historical_client,
            run_id=str(run_id),
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
            run_id=str(run_id),
            component="market_backfill",
            metadata=base_metadata,
        )
    except Exception as exc:
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component="market_backfill",
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        raise
    finally:
        session.close()
