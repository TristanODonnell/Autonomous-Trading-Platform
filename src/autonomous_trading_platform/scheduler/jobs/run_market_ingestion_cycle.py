import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.ingestion.market_data.jobs.ingest_bars_job import (
    IngestBarsJob,
)
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from src.db import get_session


def floor_to_five_minutes(timestamp: datetime) -> datetime:
    minute = (timestamp.minute // 5) * 5
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def run_market_ingestion_cycle(
    now_utc: datetime | None = None,
) -> None:
    """
    Entry point for the Airflow DAG.
    """

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)

    run_id = uuid.uuid4()
    component = "scheduler.run_market_ingestion_cycle"

    expected_symbols = {"SPY"}
    if now_utc is None:
        now_utc = datetime.now(UTC)

    cycle_end = floor_to_five_minutes(now_utc)
    cycle_start = cycle_end - timedelta(minutes=5)

    manifest = RunManifest(
        run_id=run_id,
        run_type=RunType.BACKTEST,
        created_at=now_utc,
        environment="local",
        broker="alpaca",
        broker_account_id="paper",
        strategy_id="baseline_strategy",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000.00"),
        interval=BarInterval.FIVE_MIN,
        start_date=cycle_start.date(),
        end_date=cycle_end.date(),
        dataset_version="v1",
        universe_version="v1",
        git_commit="dev",
        python_version=platform.python_version(),
        notes="5-minute market bar ingestion cycle",
    )
    manifest_service.save(manifest)
    base_metadata = {
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "expected_symbols": sorted(expected_symbols),
        "manifest_run_type": manifest.run_type.value,
        "manifest_interval": manifest.interval.value,
    }
    try:
        audit_logger.record_run_started(
            run_id=str(run_id),
            component=component,
            metadata=base_metadata,
        )

        job = IngestBarsJob(
            expected_symbols=expected_symbols,
            session=session,
            run_id=str(run_id),
            audit_logger=audit_logger,
        )

        job.run_once(start=cycle_start, end=cycle_end)

        audit_logger.record_run_completed(
            run_id=str(run_id),
            component=component,
            metadata=base_metadata,
        )
    except Exception as exc:
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        raise
    finally:
        session.close()
