from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from autonomous_trading_platform.common.errors import TransientInfrastructureError
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    order_reconciliation_job_duration,
    order_reconciliation_job_failures,
    order_reconciliation_job_runs,
)
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_cycle_dependencies,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

ORDER_RECONCILIATION_JOB_METRICS = JobMetricSet(
    runs=order_reconciliation_job_runs,
    failures=order_reconciliation_job_failures,
    duration=order_reconciliation_job_duration,
)


def run_order_reconciliation_job(
    *,
    run_id: str,
    now_utc: datetime | None = None,
) -> None:
    resolved_now = now_utc or datetime.now(UTC)
    component = "scheduler.jobs.order_reconciliation_job"
    job = "order_reconciliation_job"
    job_start = perf_counter()

    dependencies = build_trading_cycle_dependencies()
    session = dependencies.session
    execution_context = dependencies.execution_context

    record_job_started(
        logger=logger,
        metrics=ORDER_RECONCILIATION_JOB_METRICS,
        job=job,
        component=component,
        run_id=run_id,
    )

    try:
        with SorUnitOfWork(session) as uow:
            tracked_orders = (
                execution_context.order_runtime_state_service.list_reconciliation_inputs(
                    uow=uow,
                )
            )

        for tracked_order in tracked_orders:
            try:
                result = execution_context.order_reconciliation_service.reconcile_order(
                    tracked_order,
                    now=resolved_now,
                )
            except TimeoutError as exc:
                raise TransientInfrastructureError(f"reconciliation timeout: {exc}") from exc
            except ConnectionError as exc:
                raise TransientInfrastructureError(
                    f"reconciliation connection error: {exc}"
                ) from exc

            with SorUnitOfWork(session) as uow:
                uow.broker_orders.upsert(result.broker_order)

                if result.fill is not None:
                    uow.fills.upsert(result.fill)
                    execution_context.post_fill_accounting_service.apply_fill(
                        uow=uow,
                        fill=result.fill,
                        now_utc=resolved_now,
                    )

                execution_context.order_runtime_state_service.apply_reconciliation_result(
                    uow=uow,
                    result=result,
                    now_utc=resolved_now,
                )

        duration = perf_counter() - job_start
        record_job_completed(
            logger=logger,
            metrics=ORDER_RECONCILIATION_JOB_METRICS,
            job=job,
            component=component,
            run_id=run_id,
            duration_seconds=duration,
        )

    except Exception as exc:
        duration = perf_counter() - job_start
        record_job_failed(
            logger=logger,
            metrics=ORDER_RECONCILIATION_JOB_METRICS,
            job=job,
            component=component,
            run_id=run_id,
            exc=exc,
            duration_seconds=duration,
            failure_class=(
                "transient" if isinstance(exc, TransientInfrastructureError) else "unknown"
            ),
        )
        raise

    finally:
        session.close()
