from dataclasses import dataclass
from typing import Any

from autonomous_trading_platform.observability.log_context import LogContext


@dataclass(frozen=True)
class StepMetricSet:
    runs: Any
    duration: Any


@dataclass(frozen=True)
class JobMetricSet:
    runs: Any
    failures: Any
    duration: Any


@dataclass(frozen=True)
class CycleMetricSet:
    runs: Any
    failures: Any
    duration: Any


def record_cycle_started(
    *,
    logger,
    metrics: CycleMetricSet,
    component: str,
    run_id: str,
) -> None:
    logger.info(
        "cycle_started",
        extra=LogContext(
            run_id=run_id,
            component=component,
        ).to_extra(),
    )

    metrics.runs.add(
        1,
        {
            "component": component,
            "status": "started",
        },
    )


def record_cycle_completed(
    *,
    logger,
    metrics: CycleMetricSet,
    component: str,
    run_id: str,
    duration_seconds: float,
) -> None:
    logger.info(
        "cycle_completed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            duration_seconds=duration_seconds,
        ).to_extra(),
    )

    metrics.runs.add(
        1,
        {
            "component": component,
            "status": "completed",
        },
    )

    metrics.duration.record(
        duration_seconds,
        {
            "component": component,
            "status": "completed",
        },
    )


def record_cycle_failed(
    *,
    logger,
    metrics: CycleMetricSet,
    component: str,
    run_id: str,
    exc: Exception,
    duration_seconds: float,
    failure_class: str = "unknown",
) -> None:
    logger.exception(
        "cycle_failed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            duration_seconds=duration_seconds,
            exception_type=type(exc).__name__,
            error_message=str(exc),
            failure_class=failure_class,
            incident_type="cycle_failure",
        ).to_extra(),
    )

    metrics.failures.add(
        1,
        {
            "component": component,
            "failure_class": failure_class,
        },
    )

    metrics.runs.add(
        1,
        {
            "component": component,
            "status": "failed",
        },
    )

    metrics.duration.record(
        duration_seconds,
        {
            "component": component,
            "status": "failed",
        },
    )


def record_step_started(
    *,
    logger,
    metrics: StepMetricSet,
    step: str,
    component: str,
    run_id: str,
) -> None:
    logger.info(
        "step_started", extra=LogContext(run_id=run_id, component=component, step=step).to_extra()
    )
    metrics.runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "started",
        },
    )


def record_step_completed(
    *,
    logger,
    metrics: StepMetricSet,
    step: str,
    component: str,
    run_id: str,
    duration_seconds: float,
) -> None:
    logger.info(
        "step_completed",
        extra=LogContext(
            run_id=run_id, component=component, step=step, duration_seconds=duration_seconds
        ).to_extra(),
    )

    metrics.runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "completed",
        },
    )
    metrics.duration.record(
        duration_seconds,
        {
            "component": component,
            "step": step,
            "status": "completed",
        },
    )


def record_step_failed(
    *,
    logger,
    metrics: StepMetricSet,
    step: str,
    component: str,
    run_id: str,
    exc: Exception,
    duration_seconds: float,
) -> None:
    logger.exception(
        "step_failed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            step=step,
            duration_seconds=duration_seconds,
            exception_type=type(exc).__name__,
            error_message=str(exc),
            incident_type="step_failure",
        ).to_extra(),
    )

    metrics.runs.add(
        1,
        {
            "component": component,
            "step": step,
            "status": "failed",
        },
    )
    metrics.duration.record(
        duration_seconds,
        {
            "component": component,
            "step": step,
            "status": "failed",
        },
    )


def record_job_started(
    *,
    logger,
    metrics: JobMetricSet,
    job: str,
    component: str,
    run_id: str,
) -> None:
    logger.info(
        "job_started",
        extra=LogContext(
            run_id=run_id,
            component=component,
            job=job,
        ).to_extra(),
    )

    metrics.runs.add(
        1,
        {
            "component": component,
            "job": job,
            "status": "started",
        },
    )


def record_job_completed(
    *,
    logger,
    metrics: JobMetricSet,
    job: str,
    component: str,
    run_id: str,
    duration_seconds: float,
) -> None:
    logger.info(
        "job_completed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            job=job,
            duration_seconds=duration_seconds,
        ).to_extra(),
    )
    metrics.runs.add(
        1,
        {
            "component": component,
            "job": job,
            "status": "completed",
        },
    )
    metrics.duration.record(
        duration_seconds,
        {
            "component": component,
            "job": job,
            "status": "completed",
        },
    )


def record_job_failed(
    *,
    logger,
    metrics: JobMetricSet,
    job: str,
    component: str,
    run_id: str,
    exc: Exception,
    duration_seconds: float,
    failure_class: str = "unknown",
) -> None:
    logger.exception(
        "job_failed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            job=job,
            duration_seconds=duration_seconds,
            exception_type=type(exc).__name__,
            error_message=str(exc),
            failure_class=failure_class,
            incident_type="job_failure",
        ).to_extra(),
    )
    metrics.failures.add(
        1,
        {
            "component": component,
            "job": job,
            "failure_class": failure_class,
        },
    )
    metrics.runs.add(
        1,
        {
            "component": component,
            "job": job,
            "status": "failed",
        },
    )
    metrics.duration.record(
        duration_seconds,
        {
            "component": component,
            "job": job,
            "status": "failed",
        },
    )


def record_operation_started(
    *,
    logger,
    event: str,
    run_id: str,
    component: str,
    **context,
) -> None:
    logger.info(
        event,
        extra={
            **LogContext(
                run_id=run_id,
                component=component,
            ).to_extra(),
            **context,
        },
    )


def record_operation_completed(
    *,
    logger,
    event: str,
    run_id: str,
    component: str,
    **context,
) -> None:
    logger.info(
        event,
        extra={
            **LogContext(
                run_id=run_id,
                component=component,
            ).to_extra(),
            **context,
        },
    )


def record_operation_failed(
    *,
    logger,
    event: str,
    run_id: str,
    component: str,
    exc: Exception,
    **context,
) -> None:
    logger.exception(
        event,
        extra={
            **LogContext(
                run_id=run_id,
                component=component,
                exception_type=type(exc).__name__,
                error_message=str(exc),
            ).to_extra(),
            **context,
        },
    )
