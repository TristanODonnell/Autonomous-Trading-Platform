from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from autonomous_trading_platform.common.errors import TransientInfrastructureError
from autonomous_trading_platform.observability.enums import SpanTimespan
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
    order_reconciliation_mismatches,
)
from autonomous_trading_platform.observability.tracing import start_span
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
        with start_span(
            "order_reconciliation_job.run",
            timespan=SpanTimespan.JOB,
        ) as job_span:
            job_span.set_attribute("ratp.run_id", run_id)
            job_span.set_attribute("ratp.component", component)
            job_span.set_attribute("ratp.job", job)
            job_span.set_attribute("ratp.now_utc", resolved_now.isoformat())

            with SorUnitOfWork(session) as uow:
                tracked_orders = (
                    execution_context.order_runtime_state_service.list_reconciliation_inputs(
                        uow=uow,
                    )
                )

            mismatch_count = 0
            for tracked_order in tracked_orders:
                try:
                    result = execution_context.order_reconciliation_service.reconcile_order(
                        tracked_order,
                        now=resolved_now,
                    )
                    if result.event_applied is not None:
                        mismatch_count += 1
                        order_reconciliation_mismatches.add(
                            1,
                            {
                                "component": component,
                                "job": job,
                            },
                        )
                    if result.fill is not None:
                        try:
                            execution_context.realised_slippage_service.record_fill_actuals(
                                fill=result.fill,
                                now_utc=resolved_now,
                            )
                        except Exception as exc:
                            logger.warning(
                                "fill_quality.record_fill_actuals_failed",
                                extra={"fill_id": result.fill.fill_id, "error": str(exc)},
                            )
                except TimeoutError as exc:
                    raise TransientInfrastructureError(f"reconciliation timeout: {exc}") from exc
                except ConnectionError as exc:
                    raise TransientInfrastructureError(
                        f"reconciliation connection error: {exc}"
                    ) from exc

                with SorUnitOfWork(session) as uow:
                    uow.broker_orders.upsert(
                        execution_context.broker_order_mapper.to_orm_row(result.broker_order)
                    )

                    if result.fill is not None:
                        uow.fills.upsert(
                            execution_context.broker_order_mapper.to_fill_orm_row(result.fill)
                        )

                    execution_context.order_runtime_state_service.apply_reconciliation_result(
                        uow=uow,
                        result=result,
                        now_utc=resolved_now,
                    )

                if result.fill is not None:
                    try:
                        with SorUnitOfWork(session) as uow:
                            execution_context.post_fill_accounting_service.apply_fill(
                                uow=uow,
                                fill=result.fill,
                                now_utc=resolved_now,
                            )
                    except Exception as exc:
                        logger.warning(
                            "post_fill_accounting.failed",
                            extra={"fill_id": result.fill.fill_id, "error": str(exc)},
                        )
            job_span.set_attribute("ratp.reconciliation.mismatch_count", mismatch_count)
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
