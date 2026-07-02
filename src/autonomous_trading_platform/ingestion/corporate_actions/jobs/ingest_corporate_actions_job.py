from __future__ import annotations

from datetime import datetime
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    PriceBasis,
)
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
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
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.storage.parquet.datasets import ADJUSTED_BARS_DATASET
from autonomous_trading_platform.storage.parquet.mappers import bars_to_arrow
from autonomous_trading_platform.storage.parquet.repositories.parquet_bar_repository import (
    ParquetBarRepository,
)
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
        adjusted_bars_dataset_version_id: str,
        source_raw_bars_dataset_version_id: str,
        bar_repository: ParquetBarRepository,
        fetch_start: str | None = None,
        fetch_end: str | None = None,
        fetch_symbols: list[str] | None = None,
    ) -> None:
        self.session = session
        self.run_id = run_id
        self.audit_logger = audit_logger
        self.cycle_timestamp = cycle_timestamp
        self.ingestion_run_id = ingestion_run_id
        self.dataset_version_id = dataset_version_id
        self.source_raw_bars_dataset_version_id = source_raw_bars_dataset_version_id
        self.bar_repository = bar_repository
        self.adjusted_bars_dataset_version_id = adjusted_bars_dataset_version_id
        self.fetch_start = fetch_start
        self.fetch_end = fetch_end
        self.fetch_symbols = fetch_symbols

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
                    fetch_start=self.fetch_start,
                    fetch_end=self.fetch_end,
                    fetch_symbols=self.fetch_symbols,
                )
                result = service.ingest_corporate_actions()

                if result.adjusted_bars:
                    table = bars_to_arrow(result.adjusted_bars)
                    write_table(
                        table=table,
                        dataset=ADJUSTED_BARS_DATASET,
                        base_path="data",
                        dataset_version=self.adjusted_bars_dataset_version_id,
                    )
                    dataset_registration_service = DatasetRegistrationService(
                        session=self.session,
                    )

                    dataset_registration_service.register(
                        DatasetVersion(
                            dataset_version_id=self.adjusted_bars_dataset_version_id,
                            dataset_name=ADJUSTED_BARS_DATASET.dataset_key,
                            created_at=self.cycle_timestamp,
                            source="corporate_action_adjustment",
                            price_basis=PriceBasis.ADJUSTED,
                            interval=BarInterval.ONE_DAY,
                            schema_version=ADJUSTED_BARS_DATASET.schema_version,
                            symbol_coverage=None,
                            date_coverage_start=self.cycle_timestamp.date(),
                            date_coverage_end=self.cycle_timestamp.date(),
                            validation_status="validated",
                            checksum=None,
                            source_dataset_version=self.source_raw_bars_dataset_version_id,
                            source_manifest={
                                "pipeline": "corporate_action_adjustment",
                                "source_raw_bars_dataset_version_id": self.source_raw_bars_dataset_version_id,
                            },
                            metadata_json={
                                "dataset_type": "adjusted_bars",
                                "source_raw_bars_dataset_version_id": self.source_raw_bars_dataset_version_id,
                            },
                        )
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
