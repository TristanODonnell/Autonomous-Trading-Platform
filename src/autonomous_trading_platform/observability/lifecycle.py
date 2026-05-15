from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.metric_labels import (
    broker_labels,
    context_strategy_id,
    observability_environment,
)
from autonomous_trading_platform.observability.metrics import (
    order_execution_ack_to_fill_latency_seconds,
    order_execution_signal_to_submit_latency_seconds,
    order_execution_submit_to_ack_latency_seconds,
    order_execution_total_latency_seconds,
    ratp_cash_drift_amount,
    ratp_position_drift_count_total,
    ratp_position_quantity_drift,
)
from autonomous_trading_platform.observability.runtime_context import get_runtime_context


def _environment() -> str:
    return observability_environment()


def _correlation_id() -> str | None:
    ctx = get_runtime_context()
    return ctx.correlation_id if ctx is not None else None


def _strategy_id() -> str | None:
    return context_strategy_id()


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
    correlation_id = _correlation_id()
    logger.info(
        "cycle_started",
        extra=LogContext(
            run_id=run_id,
            component=component,
            correlation_id=correlation_id,
        ).to_extra(),
    )

    metrics.runs.add(
        1,
        {
            "environment": _environment(),
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
    correlation_id = _correlation_id()
    logger.info(
        "cycle_completed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            duration_seconds=duration_seconds,
            correlation_id=correlation_id,
        ).to_extra(),
    )

    labels = {
        "environment": _environment(),
        "component": component,
        "status": "completed",
    }
    metrics.runs.add(1, labels)
    metrics.duration.record(duration_seconds, labels)


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
    correlation_id = _correlation_id()
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
            correlation_id=correlation_id,
        ).to_extra(),
    )

    span = trace.get_current_span()
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))

    env = _environment()
    metrics.failures.add(
        1,
        {
            "environment": env,
            "component": component,
            "failure_class": failure_class,
        },
    )
    labels = {
        "environment": env,
        "component": component,
        "status": "failed",
    }
    metrics.runs.add(1, labels)
    metrics.duration.record(duration_seconds, labels)


def record_step_started(
    *,
    logger,
    metrics: StepMetricSet,
    step: str,
    component: str,
    run_id: str,
) -> None:
    correlation_id = _correlation_id()
    logger.info(
        "step_started",
        extra=LogContext(
            run_id=run_id,
            component=component,
            step=step,
            correlation_id=correlation_id,
        ).to_extra(),
    )
    metrics.runs.add(
        1,
        {
            "environment": _environment(),
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
    correlation_id = _correlation_id()
    logger.info(
        "step_completed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            step=step,
            duration_seconds=duration_seconds,
            correlation_id=correlation_id,
        ).to_extra(),
    )

    labels = {
        "environment": _environment(),
        "component": component,
        "step": step,
        "status": "completed",
    }
    metrics.runs.add(1, labels)
    metrics.duration.record(duration_seconds, labels)


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
    correlation_id = _correlation_id()
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
            correlation_id=correlation_id,
        ).to_extra(),
    )

    span = trace.get_current_span()
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))

    labels = {
        "environment": _environment(),
        "component": component,
        "step": step,
        "status": "failed",
    }
    metrics.runs.add(1, labels)
    metrics.duration.record(duration_seconds, labels)


def record_job_started(
    *,
    logger,
    metrics: JobMetricSet,
    job: str,
    component: str,
    run_id: str,
) -> None:
    correlation_id = _correlation_id()
    strategy_id = _strategy_id()
    logger.info(
        "job_started",
        extra=LogContext(
            run_id=run_id,
            component=component,
            job=job,
            correlation_id=correlation_id,
            strategy_id=strategy_id,
        ).to_extra(),
    )

    labels: dict[str, str] = {
        "environment": _environment(),
        "component": component,
        "status": "started",
    }
    if strategy_id is not None:
        labels["strategy_id"] = strategy_id
    metrics.runs.add(1, labels)


def record_job_completed(
    *,
    logger,
    metrics: JobMetricSet,
    job: str,
    component: str,
    run_id: str,
    duration_seconds: float,
) -> None:
    correlation_id = _correlation_id()
    strategy_id = _strategy_id()
    logger.info(
        "job_completed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            job=job,
            duration_seconds=duration_seconds,
            correlation_id=correlation_id,
            strategy_id=strategy_id,
        ).to_extra(),
    )

    labels: dict[str, str] = {
        "environment": _environment(),
        "component": component,
        "status": "completed",
    }
    if strategy_id is not None:
        labels["strategy_id"] = strategy_id
    metrics.runs.add(1, labels)
    metrics.duration.record(duration_seconds, labels)


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
    correlation_id = _correlation_id()
    strategy_id = _strategy_id()
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
            correlation_id=correlation_id,
            strategy_id=strategy_id,
        ).to_extra(),
    )

    span = trace.get_current_span()
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))

    env = _environment()
    failure_labels: dict[str, str] = {
        "environment": env,
        "component": component,
        "failure_class": failure_class,
    }
    if strategy_id is not None:
        failure_labels["strategy_id"] = strategy_id
    metrics.failures.add(1, failure_labels)

    run_labels: dict[str, str] = {
        "environment": env,
        "component": component,
        "status": "failed",
    }
    if strategy_id is not None:
        run_labels["strategy_id"] = strategy_id
    metrics.runs.add(1, run_labels)
    metrics.duration.record(duration_seconds, run_labels)


def record_operation_started(
    *,
    logger,
    event: str,
    run_id: str,
    component: str,
    **context,
) -> None:
    correlation_id = _correlation_id()
    logger.info(
        event,
        extra={
            **LogContext(
                run_id=run_id,
                component=component,
                correlation_id=correlation_id,
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
    correlation_id = _correlation_id()
    logger.info(
        event,
        extra={
            **LogContext(
                run_id=run_id,
                component=component,
                correlation_id=correlation_id,
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
    correlation_id = _correlation_id()
    logger.exception(
        event,
        extra={
            **LogContext(
                run_id=run_id,
                component=component,
                exception_type=type(exc).__name__,
                error_message=str(exc),
                correlation_id=correlation_id,
            ).to_extra(),
            **context,
        },
    )

    span = trace.get_current_span()
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))


# ============================================================
# Reconciliation lifecycle helpers (TASK-620)
# ============================================================


def record_reconciliation_started(
    *,
    logger,
    run_id: str,
    component: str,
    check_name: str,
) -> None:
    correlation_id = _correlation_id()
    logger.info(
        "reconciliation_started",
        extra=LogContext(
            run_id=run_id,
            component=component,
            check_name=check_name,
            reconciliation_status="started",
            correlation_id=correlation_id,
        ).to_extra(),
    )


def record_reconciliation_completed(
    *,
    logger,
    run_id: str,
    component: str,
    check_name: str,
    duration_seconds: float,
    reconciliation_status: str,
) -> None:
    correlation_id = _correlation_id()
    logger.info(
        "reconciliation_completed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            check_name=check_name,
            duration_seconds=duration_seconds,
            reconciliation_status=reconciliation_status,
            correlation_id=correlation_id,
        ).to_extra(),
    )


def record_reconciliation_failed(
    *,
    logger,
    run_id: str,
    component: str,
    check_name: str,
    exc: Exception,
    duration_seconds: float,
) -> None:
    correlation_id = _correlation_id()
    logger.exception(
        "reconciliation_failed",
        extra=LogContext(
            run_id=run_id,
            component=component,
            check_name=check_name,
            duration_seconds=duration_seconds,
            reconciliation_status="failed",
            exception_type=type(exc).__name__,
            error_message=str(exc),
            incident_type="reconciliation_failure",
            correlation_id=correlation_id,
        ).to_extra(),
    )

    span = trace.get_current_span()
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))


def record_cash_drift_detected(
    *,
    logger,
    run_id: str,
    component: str,
    expected_cash: float,
    actual_cash: float,
    cash_drift: float,
    severity: str,
) -> None:
    correlation_id = _correlation_id()
    logger.warning(
        "cash_drift_detected",
        extra=LogContext(
            run_id=run_id,
            component=component,
            expected_cash=expected_cash,
            actual_cash=actual_cash,
            cash_drift=cash_drift,
            severity=severity,
            reconciliation_status="drifted",
            correlation_id=correlation_id,
        ).to_extra(),
    )

    env = _environment()
    ratp_cash_drift_amount.record(
        cash_drift,
        {"environment": env, "component": component, "status": severity},
    )


def record_position_drift_detected(
    *,
    logger,
    run_id: str,
    component: str,
    symbol: str,
    expected_qty: float,
    actual_qty: float,
    position_drift: float,
    severity: str,
) -> None:
    correlation_id = _correlation_id()
    logger.warning(
        "position_drift_detected",
        extra=LogContext(
            run_id=run_id,
            component=component,
            symbol=symbol,
            expected_position_qty=expected_qty,
            actual_position_qty=actual_qty,
            position_drift=position_drift,
            severity=severity,
            reconciliation_status="drifted",
            correlation_id=correlation_id,
        ).to_extra(),
    )

    env = _environment()
    labels = {"environment": env, "component": component, "status": severity}
    ratp_position_drift_count_total.add(1, labels)
    ratp_position_quantity_drift.record(position_drift, labels)


def record_order_execution_latency(
    *,
    logger,
    component: str,
    run_id: str,
    symbol: str,
    broker: str,
    broker_order_id: str | None = None,
    signal_to_submit_seconds: float | None = None,
    submit_to_ack_seconds: float | None = None,
    ack_to_fill_seconds: float | None = None,
    total_execution_seconds: float | None = None,
) -> None:
    correlation_id = _correlation_id()
    context = {
        "symbol": symbol,
        "broker": broker,
        "broker_order_id": broker_order_id,
        "signal_to_submit_seconds": signal_to_submit_seconds,
        "submit_to_ack_seconds": submit_to_ack_seconds,
        "ack_to_fill_seconds": ack_to_fill_seconds,
        "total_execution_seconds": total_execution_seconds,
    }
    logger.info(
        "order_execution_latency_recorded",
        extra={
            **LogContext(
                run_id=run_id,
                component=component,
                correlation_id=correlation_id,
            ).to_extra(),
            **{key: value for key, value in context.items() if value is not None},
        },
    )

    labels = broker_labels(
        component=component,
        broker=broker,
        endpoint="order_execution.latency",
    )
    if signal_to_submit_seconds is not None:
        order_execution_signal_to_submit_latency_seconds.record(
            signal_to_submit_seconds,
            labels,
        )
    if submit_to_ack_seconds is not None:
        order_execution_submit_to_ack_latency_seconds.record(submit_to_ack_seconds, labels)
    if ack_to_fill_seconds is not None:
        order_execution_ack_to_fill_latency_seconds.record(ack_to_fill_seconds, labels)
    if total_execution_seconds is not None:
        order_execution_total_latency_seconds.record(total_execution_seconds, labels)
