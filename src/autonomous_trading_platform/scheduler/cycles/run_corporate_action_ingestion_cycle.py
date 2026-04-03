import platform
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, RunType
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.ingestion.corporate_actions.jobs.ingest_corporate_actions_job import (
    IngestCorporateActionsJob,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    corporate_action_ingestion_cycle_duration,
    corporate_action_ingestion_cycle_failures,
    corporate_action_ingestion_cycle_runs,
    corporate_action_ingestion_cycle_step_duration,
    corporate_action_ingestion_cycle_step_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.runtime.services.run_manifest_service import RunManifestService
from src.db import get_session

logger = get_logger(__name__)


def _record_cycle_started(*, component: str, run_id: str) -> None:
    logger.info(
        "corporate_action_ingestion_cycle_started run_id=%s component=%s",
        run_id,
        component,
    )


def _record_cycle_completed(*, component: str, run_id: str, duration_seconds: float) -> None:
    logger.info(
        "corporate_action_ingestion_cycle_completed run_id=%s component=%s duration_seconds=%.6f",
        run_id,
        component,
        duration_seconds,
    )
    corporate_action_ingestion_cycle_runs.add(
        1,
        {
            "component": component,
            "status": "completed",
        },
    )
    corporate_action_ingestion_cycle_duration.record(
        duration_seconds,
        {
            "component": component,
            "status": "completed",
        },
    )


def _record_cycle_failed(
    *,
    component: str,
    run_id: str,
    exc: Exception,
    duration_seconds: float,
) -> None:
    logger.exception(
        "corporate_action_ingestion_cycle_failed run_id=%s component=%s duration_seconds=%.6f error=%s",
        run_id,
        component,
        duration_seconds,
        str(exc),
    )
    corporate_action_ingestion_cycle_failures.add(
        1,
        {
            "component": component,
            "failure_class": "unknown",
        },
    )
    corporate_action_ingestion_cycle_runs.add(
        1,
        {
            "component": component,
            "status": "failed",
        },
    )
    corporate_action_ingestion_cycle_duration.record(
        duration_seconds,
        {
            "component": component,
            "status": "failed",
        },
    )


def _record_step_started(*, step: str, component: str, run_id: str) -> None:
    logger.info(
        "corporate_action_ingestion_cycle_step_started run_id=%s component=%s step=%s",
        run_id,
        component,
        step,
    )
    corporate_action_ingestion_cycle_step_runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "started",
        },
    )


def _record_step_completed(
    *,
    step: str,
    component: str,
    run_id: str,
    duration_seconds: float,
) -> None:
    logger.info(
        "corporate_action_ingestion_cycle_step_completed run_id=%s component=%s step=%s duration_seconds=%.6f",
        run_id,
        component,
        step,
        duration_seconds,
    )
    corporate_action_ingestion_cycle_step_runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "completed",
        },
    )
    corporate_action_ingestion_cycle_step_duration.record(
        duration_seconds,
        {
            "component": component,
            "step": step,
            "status": "completed",
        },
    )


def _record_step_failed(
    *,
    step: str,
    component: str,
    run_id: str,
    exc: Exception,
    duration_seconds: float,
) -> None:
    logger.exception(
        "corporate_action_ingestion_cycle_step_failed run_id=%s component=%s step=%s duration_seconds=%.6f error=%s",
        run_id,
        component,
        step,
        duration_seconds,
        str(exc),
    )
    corporate_action_ingestion_cycle_step_runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "failed",
        },
    )
    corporate_action_ingestion_cycle_duration.record(
        duration_seconds,
        {
            "component": component,
            "step": step,
            "status": "failed",
        },
    )


def run_corporate_action_ingestion_cycle() -> None:
    """
    Entry point for the Airflow DAG.
    """
    now_utc = datetime.now(UTC)
    cycle_wall_start = perf_counter()

    session: Session = get_session()
    audit_logger = AuditLoggingService(session=session)
    manifest_service = RunManifestService(session=session)

    run_id = uuid.uuid4()
    component = "corporate_action_ingestion_cycle"
    _record_cycle_started(component=component, run_id=str(run_id))

    try:
        # Daily ingestion window
        cycle_end = now_utc
        cycle_start = cycle_end - timedelta(days=1)

        manifest = RunManifest(
            run_id=run_id,
            run_type=RunType.PAPER,
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
            "run_id": str(run_id),
            "cycle_start": cycle_start.isoformat(),
            "cycle_end": cycle_end.isoformat(),
            "pipeline": "corporate_actions_ingestion",
            "manifest_run_type": manifest.run_type,
        }

        with start_span("corporate_action_ingestion_cycle") as cycle_span:
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.component", component)
            cycle_span.set_attribute("ratp.cycle_start", cycle_start.isoformat())
            cycle_span.set_attribute("ratp.cycle_end", cycle_end.isoformat())

            manifest_service.save(manifest)

            audit_logger.record_run_started(
                run_id=str(run_id),
                component="corporate_actions_ingestion",
                metadata=base_metadata,
            )
            step = "ingest_corporate_actions"
            _record_step_started(step=step, component=component, run_id=str(run_id))

            step_start = perf_counter()
            try:
                job = IngestCorporateActionsJob(
                    session=session,
                    run_id=str(run_id),
                    audit_logger=audit_logger,
                    cycle_timestamp=cycle_end,
                )
                job.ingest_corporate_actions_job()
                step_duration = perf_counter() - step_start

                audit_logger.record_run_completed(
                    run_id=str(run_id),
                    component="corporate_actions_ingestion",
                    metadata=base_metadata,
                )
                total_duration = perf_counter() - cycle_wall_start
                _record_step_completed(
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=step_duration,
                )
            except Exception as exc:
                step_duration = perf_counter() - step_start
                _record_step_failed(
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=step_duration,
                )
                raise

            audit_logger.record_run_completed(
                run_id=str(run_id),
                component="corporate_actions_ingestion",
                metadata=base_metadata,
            )

            total_duration = perf_counter() - cycle_wall_start

            _record_cycle_completed(
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )
    except Exception as exc:
        total_duration = perf_counter() - cycle_wall_start

        audit_logger.record_run_failed(
            run_id=str(run_id),
            component="corporate_actions_ingestion",
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )

        _record_cycle_failed(
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=total_duration,
        )
        raise
    finally:
        session.close()
