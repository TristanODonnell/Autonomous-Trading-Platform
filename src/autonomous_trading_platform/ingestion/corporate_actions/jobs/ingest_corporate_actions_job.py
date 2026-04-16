from __future__ import annotations

from datetime import datetime
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service import (
    CorporateActionIngestionService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    corporate_action_ingestion_job_duration,
    corporate_action_ingestion_job_failures,
    corporate_action_ingestion_job_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.storage.parquet.datasets import ADJUSTED_BARS_DATASET
from autonomous_trading_platform.storage.parquet.mappers import bars_to_arrow
from autonomous_trading_platform.storage.parquet.parquet_bar_repository import ParquetBarRepository
from autonomous_trading_platform.storage.parquet.writer import write_table

logger = get_logger(__name__)

CORPORATE_ACTION_JOB_METRICS = JobMetricSet(
    runs=corporate_action_ingestion_job_runs,
    failures=corporate_action_ingestion_job_failures,
    duration=corporate_action_ingestion_job_duration,
)


class IngestCorporateActionsJob:
    def __init__(
        self,
        session: Session,
        run_id: str,
        audit_logger: AuditLoggingService,
        cycle_timestamp: datetime,
        ingestion_run_id: str,
        dataset_version_id: str,
        source_raw_bars_dataset_version_id: str,
        bar_repository: ParquetBarRepository,
    ) -> None:
        self.session = session
        self.run_id = run_id
        self.audit_logger = audit_logger
        self.cycle_timestamp = cycle_timestamp
        self.ingestion_run_id = ingestion_run_id
        self.dataset_version_id = dataset_version_id
        self.source_raw_bars_dataset_version_id = source_raw_bars_dataset_version_id
        self.bar_repository = bar_repository

    def ingest_corporate_actions_job(self) -> None:
        component = "ingestion.ingest_corporate_actions_job"
        job = "ingest_corporate_actions"
        job_start = perf_counter()

        record_job_started(
            logger=logger,
            metrics=CORPORATE_ACTION_JOB_METRICS,
            job=job,
            component=component,
            run_id=self.run_id,
        )

        try:
            with start_span(
                "ingest_corporate_actions_job.run",
                timespan=SpanTimespan.JOB,
            ) as job_span:
                job_span.set_attribute("ratp.run_id", self.run_id)
                job_span.set_attribute("ratp.component", component)
                job_span.set_attribute("ratp.job", job)
                job_span.set_attribute("ratp.cycle_timestamp", self.cycle_timestamp.isoformat())

                service = CorporateActionIngestionService(
                    session=self.session,
                    run_id=self.run_id,
                    audit_logger=self.audit_logger,
                    cycle_timestamp=self.cycle_timestamp,
                    bar_repository=self.bar_repository,
                    source_raw_bars_dataset_version_id=self.source_raw_bars_dataset_version_id,
                )
                result = service.ingest_corporate_actions()

                if result.adjusted_bars:
                    table = bars_to_arrow(result.adjusted_bars)
                    write_table(
                        table=table,
                        dataset=ADJUSTED_BARS_DATASET,
                        base_path="data",
                        dataset_version=self.dataset_version_id,
                    )

            duration = perf_counter() - job_start
            record_job_completed(
                logger=logger,
                metrics=CORPORATE_ACTION_JOB_METRICS,
                job=job,
                component=component,
                run_id=self.run_id,
                duration_seconds=duration,
            )
        except Exception as exc:
            duration = perf_counter() - job_start
            record_job_failed(
                logger=logger,
                metrics=CORPORATE_ACTION_JOB_METRICS,
                job=job,
                component=component,
                run_id=self.run_id,
                exc=exc,
                duration_seconds=duration,
            )
            raise
