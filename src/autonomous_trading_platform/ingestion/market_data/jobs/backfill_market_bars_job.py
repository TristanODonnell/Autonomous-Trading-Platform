from __future__ import annotations

from datetime import datetime
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    market_backfill_job_duration,
    market_backfill_job_failures,
    market_backfill_job_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService

from ..clients.alpaca_historical_bars_client import AlpacaHistoricalBarsClient
from ..services.market_backfill_service import MarketBackfillService

logger = get_logger(__name__)

BACKFILL_MARKET_BARS_JOB_METRICS = JobMetricSet(
    runs=market_backfill_job_runs,
    failures=market_backfill_job_failures,
    duration=market_backfill_job_duration,
)


class BackfillMarketBarsJob:
    """
    Job wrapper for historical market-data backfill.
    """

    def __init__(
        self,
        session: Session,
        historical_client: AlpacaHistoricalBarsClient,
        run_id: str,
        audit_logger: AuditLoggingService,
        ingestion_run_id: str,
        dataset_version_id: str,
    ) -> None:
        self.session = session
        self.ingestion_run_id = ingestion_run_id
        self.dataset_version_id = dataset_version_id

        self.backfill_service = MarketBackfillService(
            session=session,
            historical_client=historical_client,
            run_id=run_id,
            audit_logger=audit_logger,
            ingestion_run_id=ingestion_run_id,
            dataset_version_id=dataset_version_id,
        )

    async def run(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> None:
        component = "ingestion.backfill_market_bars_job"
        job = "backfill_market_bars"
        job_start = perf_counter()

        record_job_started(
            logger=logger,
            metrics=BACKFILL_MARKET_BARS_JOB_METRICS,
            job=job,
            component=component,
            run_id=self.backfill_service.run_id,
        )

        try:
            with start_span(
                "backfill_market_bars_job.run",
                timespan=SpanTimespan.JOB,
            ) as job_span:
                job_span.set_attribute("ratp.run_id", self.backfill_service.run_id)
                job_span.set_attribute("ratp.component", component)
                job_span.set_attribute("ratp.job", job)
                job_span.set_attribute("ratp.symbol_count", len(symbols))
                job_span.set_attribute("ratp.backfill_start", start.isoformat())
                job_span.set_attribute("ratp.backfill_end", end.isoformat())
                job_span.set_attribute("ratp.ingestion_run_id", self.ingestion_run_id)
                job_span.set_attribute("ratp.dataset_version_id", self.dataset_version_id)

                await self.backfill_service.backfill(
                    symbols=symbols,
                    start=start,
                    end=end,
                )

            duration = perf_counter() - job_start
            record_job_completed(
                logger=logger,
                metrics=BACKFILL_MARKET_BARS_JOB_METRICS,
                job=job,
                component=component,
                run_id=self.backfill_service.run_id,
                duration_seconds=duration,
            )
        except Exception as exc:
            duration = perf_counter() - job_start
            record_job_failed(
                logger=logger,
                metrics=BACKFILL_MARKET_BARS_JOB_METRICS,
                job=job,
                component=component,
                run_id=self.backfill_service.run_id,
                exc=exc,
                duration_seconds=duration,
            )
            raise
