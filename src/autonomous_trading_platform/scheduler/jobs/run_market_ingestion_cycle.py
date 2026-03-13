import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.market_data.jobs.ingest_bars_job import (
    IngestBarsJob,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from src.db import get_session


def floor_to_five_minutes(timestamp: datetime) -> datetime:
    minute = (timestamp.minute // 5) * 5
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def run_market_ingestion_cycle() -> None:
    """
    Entry point for the Airflow DAG.
    """

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)

    run_id = str(uuid.uuid4())
    component = "scheduler.run_market_ingestion_cycle"
    expected_symbols = {"SPY"}
    now_utc = datetime.now(UTC)
    cycle_end = floor_to_five_minutes(now_utc)
    cycle_start = cycle_end - timedelta(minutes=5)
    base_metadata = {
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "expected_symbols": sorted(expected_symbols),
    }

    try:
        audit_logger.record_run_started(
            run_id=run_id,
            component=component,
            metadata=base_metadata,
        )

        job = IngestBarsJob(
            expected_symbols=expected_symbols,
            session=session,
            run_id=run_id,
            audit_logger=audit_logger,
        )

        job.run_once(start=cycle_start, end=cycle_end)

        audit_logger.record_run_completed(
            run_id=run_id,
            component=component,
            metadata=base_metadata,
        )
    except Exception as exc:
        audit_logger.record_run_failed(
            run_id=run_id,
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        raise
    finally:
        session.close()
