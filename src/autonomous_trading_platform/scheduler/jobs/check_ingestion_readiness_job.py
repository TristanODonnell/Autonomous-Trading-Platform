from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    ingestion_readiness_job_duration,
    ingestion_readiness_job_failures,
    ingestion_readiness_job_ingestion_lag,
    ingestion_readiness_job_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_cycle_window,
)

logger = get_logger(__name__)

INGESTION_READINESS_JOB_METRICS = JobMetricSet(
    runs=ingestion_readiness_job_runs,
    failures=ingestion_readiness_job_failures,
    duration=ingestion_readiness_job_duration,
)


@dataclass(slots=True)
class IngestionReadinessResult:
    ready: bool
    safe_mode: bool
    reason: str | None = None


def check_ingestion_readiness_job(
    *,
    run_id: str,
    now_utc: datetime | None = None,
) -> IngestionReadinessResult:
    resolved_now = now_utc or datetime.now(UTC)
    component = "scheduler.jobs.check_ingestion_readiness_job"
    job = "check_ingestion_readiness_job"
    job_start = perf_counter()

    record_job_started(
        logger=logger,
        metrics=INGESTION_READINESS_JOB_METRICS,
        job=job,
        component=component,
        run_id=run_id,
    )

    try:
        with start_span(
            "check_ingestion_readiness_job.run",
            timespan=SpanTimespan.JOB,
        ) as job_span:
            job_span.set_attribute("ratp.run_id", run_id)
            job_span.set_attribute("ratp.component", component)
            job_span.set_attribute("ratp.job", job)
            job_span.set_attribute("ratp.now_utc", resolved_now.isoformat())

            cycle_window = build_trading_cycle_window(now_utc=resolved_now)

            ingestion_lag_seconds = max(
                0.0,
                (resolved_now - cycle_window.ingestion_deadline).total_seconds(),
            )
            ingestion_readiness_job_ingestion_lag.record(
                ingestion_lag_seconds,
                {
                    "component": component,
                    "job": job,
                },
            )
            job_span.set_attribute("ratp.ingestion.lag_seconds", ingestion_lag_seconds)

            if resolved_now > cycle_window.ingestion_deadline:
                result = IngestionReadinessResult(
                    ready=False,
                    safe_mode=True,
                    reason="ingestion_deadline_missed",
                )
            else:
                result = IngestionReadinessResult(
                    ready=True,
                    safe_mode=False,
                    reason=None,
                )

            job_span.set_attribute("ratp.ingestion.ready", result.ready)
            job_span.set_attribute("ratp.ingestion.safe_mode", result.safe_mode)
            if result.reason is not None:
                job_span.set_attribute("ratp.ingestion.reason", result.reason)

        duration = perf_counter() - job_start

        record_job_completed(
            logger=logger,
            metrics=INGESTION_READINESS_JOB_METRICS,
            job=job,
            component=component,
            run_id=run_id,
            duration_seconds=duration,
        )
        return result

    except Exception as exc:
        duration = perf_counter() - job_start
        record_job_failed(
            logger=logger,
            metrics=INGESTION_READINESS_JOB_METRICS,
            job=job,
            component=component,
            run_id=run_id,
            exc=exc,
            duration_seconds=duration,
        )
        raise
