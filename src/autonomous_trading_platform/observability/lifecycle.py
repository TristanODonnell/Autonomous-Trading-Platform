from dataclasses import dataclass
from typing import Any


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


def record_step_started(
    *,
    logger,
    metrics: StepMetricSet,
    step: str,
    component: str,
    run_id: str,
) -> None:
    logger.info(
        "step_started run_id=%s component=%s step=%s",
        run_id,
        component,
        step,
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
        "step_completed run_id=%s component=%s step=%s duration_seconds=%.6f",
        run_id,
        component,
        step,
        duration_seconds,
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
        "step_failed run_id=%s component=%s step=%s duration_seconds=%.6f error=%s",
        run_id,
        component,
        step,
        duration_seconds,
        str(exc),
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
        "job_started run_id=%s component=%s job=%s",
        run_id,
        component,
        job,
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
        "job_completed run_id=%s component=%s job=%s duration_seconds=%.6f",
        run_id,
        component,
        job,
        duration_seconds,
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
        "job_failed run_id=%s component=%s job=%s duration_seconds=%.6f error=%s",
        run_id,
        component,
        job,
        duration_seconds,
        str(exc),
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
