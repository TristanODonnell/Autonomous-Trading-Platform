from __future__ import annotations

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
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    StepMetricSet,
    record_step_completed,
    record_step_failed,
    record_step_started,
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
CORPORATE_ACTION_INGESTION_STEP_METRICS = StepMetricSet(
    runs=corporate_action_ingestion_cycle_step_runs,
    duration=corporate_action_ingestion_cycle_step_duration,
)


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
    component = "scheduler.run_corporate_action_ingestion_cycle"
    base_metadata: dict[str, object] = {}

    _record_cycle_started(component=component, run_id=str(run_id))

    try:
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
            "manifest_run_type": manifest.run_type.value,
        }

        with start_span(
            "corporate_action_ingestion_cycle.run", timespan=SpanTimespan.CYCLE
        ) as cycle_span:
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.component", component)
            cycle_span.set_attribute("ratp.cycle_start", cycle_start.isoformat())
            cycle_span.set_attribute("ratp.cycle_end", cycle_end.isoformat())

            audit_logger.record_run_started(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )

            step = "ingest_corporate_actions"
            record_step_started(
                logger=logger,
                metrics=CORPORATE_ACTION_INGESTION_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()
            try:
                with start_span(
                    "corporate_action_ingestion_cycle.ingest_corporate_actions",
                    timespan=SpanTimespan.STEP,
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.step", step)

                    job = IngestCorporateActionsJob(
                        session=session,
                        run_id=str(run_id),
                        audit_logger=audit_logger,
                        cycle_timestamp=cycle_end,
                    )
                    job.ingest_corporate_actions_job()

                step_duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=CORPORATE_ACTION_INGESTION_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=step_duration,
                )
            except Exception as exc:
                step_duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=CORPORATE_ACTION_INGESTION_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=step_duration,
                )
                raise

            audit_logger.record_run_completed(
                run_id=str(run_id),
                component=component,
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
            component=component,
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
