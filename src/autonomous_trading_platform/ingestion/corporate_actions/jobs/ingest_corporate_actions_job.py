from __future__ import annotations

from datetime import datetime
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_ingestion_service import (
    CorporateActionIngestionService,
)
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
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService

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
    ) -> None:
        self.session = session
        self.run_id = run_id
        self.audit_logger = audit_logger
        self.cycle_timestamp = cycle_timestamp

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
            service = CorporateActionIngestionService(
                session=self.session,
                run_id=self.run_id,
                audit_logger=self.audit_logger,
                cycle_timestamp=self.cycle_timestamp,
            )
            service.ingest_corporate_actions()

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
