import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.ingestion.corporate_actions.jobs.ingest_corporate_actions_job import (
    IngestCorporateActionsJob,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from src.db import get_session


def run_corporate_action_ingestion_cycle() -> None:
    """
    Entry point for the Airflow DAG.
    """
    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)
    run_id = uuid.uuid4()
    now_utc = datetime.now(UTC)

    # Daily ingestion window
    cycle_end = now_utc
    cycle_start = cycle_end - timedelta(days=1)

    manifest = RunManifest(
        run_id=run_id,
        run_type=RunType.INGESTION,
        created_at=now_utc,
        environment="local",
        broker="alpaca",
        broker_account_id="paper",
        strategy_id="baseline_strategy",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000.00"),
        interval=BarInterval.ONE_DAY,
        start_date=cycle_start.date(),
        end_date=cycle_end.date(),
        dataset_version="v1",
        universe_version="v1",
        git_commit="dev",
        python_version=platform.python_version(),
        notes="Daily corporate actions ingestion cycle",
    )
    manifest_service.save(manifest)
    base_metadata = {
        "run_id": run_id,
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "pipeline": "corporate_actions_ingestion",
        "manifest_run_type": manifest.run_type,
    }
    try:
        manifest_service.save(manifest)

        audit_logger.record_run_started(
            run_id=str(run_id),
            component="corporate_actions_ingestion",
            metadata=base_metadata,
        )

        job = IngestCorporateActionsJob(
            session=session,
            run_id=str(run_id),
            audit_logger=audit_logger,
            cycle_timestamp=cycle_end,
        )
        job.ingest_corporate_actions_job()

        audit_logger.record_run_completed(
            run_id=str(run_id),
            component="corporate_actions_ingestion",
            metadata=base_metadata,
        )
    except Exception as exc:
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component="corporate_actions_ingestion",
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        raise
    finally:
        session.close()
