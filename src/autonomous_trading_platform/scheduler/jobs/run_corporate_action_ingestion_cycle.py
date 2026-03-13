import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.corporate_actions.jobs.ingest_corporate_actions_job import (
    IngestCorporateActionsJob,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from src.db import get_session


def run_corporate_action_ingestion_cycle() -> None:
    """
    Entry point for the Airflow DAG.
    """
    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    run_id = str(uuid.uuid4())
    now_utc = datetime.now(UTC)

    # Daily ingestion window
    cycle_end = now_utc
    cycle_start = cycle_end - timedelta(days=1)

    base_metadata = {
        "run_id": run_id,
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "pipeline": "corporate_actions_ingestion",
    }
    try:
        audit_logger.record_run_started(
            run_id=run_id,
            component="corporate_actions_ingestion",
            metadata=base_metadata,
        )

        job = IngestCorporateActionsJob(
            session=session,
            run_id=run_id,
            audit_logger=audit_logger,
            cycle_timestamp=cycle_end,
        )
        job.ingest_corporate_actions_job()

        audit_logger.record_run_completed(
            run_id=run_id,
            component="corporate_actions_ingestion",
            metadata=base_metadata,
        )
    except Exception as exc:
        audit_logger.record_run_failed(
            run_id=run_id,
            component="corporate_actions_ingestion",
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        raise
    finally:
        session.close()
