from __future__ import annotations

from decimal import Decimal
from time import perf_counter

from autonomous_trading_platform.common.errors import (
    TransientInfrastructureError,
)
from autonomous_trading_platform.execution.services.risk_snapshot_service import (
    RiskLimitConfig,
    RiskSnapshotService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    risk_snapshot_job_duration,
    risk_snapshot_job_failures,
    risk_snapshot_job_runs,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

RISK_SNAPSHOT_JOB_METRICS = JobMetricSet(
    runs=risk_snapshot_job_runs,
    failures=risk_snapshot_job_failures,
    duration=risk_snapshot_job_duration,
)


def run_risk_snapshot_job(
    *,
    now_utc,
    trading_cycle_dependencies,
    run_id,
) -> None:
    component = "scheduler.jobs.risk_snapshot_job"
    job = "risk_snapshot_job"
    job_start = perf_counter()
    session = trading_cycle_dependencies.session
    settings = trading_cycle_dependencies.settings
    risk_snapshot_service = RiskSnapshotService()

    record_job_started(
        logger=logger,
        metrics=RISK_SNAPSHOT_JOB_METRICS,
        job=job,
        component=component,
        run_id=str(run_id),
    )
    try:
        with start_span(
            "risk_snapshot_job.run",
            timespan=SpanTimespan.JOB,
        ) as job_span:
            job_span.set_attribute("ratp.run_id", run_id)
            job_span.set_attribute("ratp.component", component)
            job_span.set_attribute("ratp.job", job)
            job_span.set_attribute("ratp.now_utc", now_utc.isoformat())

            with SorUnitOfWork(session) as uow:
                latest_position_snapshot = (
                    uow.position_snapshots.get_latest()
                    if hasattr(uow.position_snapshots, "get_latest")
                    else None
                )
                latest_cash_snapshot = (
                    uow.cash_snapshots.get_latest()
                    if hasattr(uow.cash_snapshots, "get_latest")
                    else None
                )

                risk_snapshot = risk_snapshot_service.compute_snapshot(
                    run_id=run_id,
                    timestamp=now_utc,
                    position_snapshot=latest_position_snapshot,
                    cash_snapshot=latest_cash_snapshot,
                    limits_config=RiskLimitConfig(
                        max_gross_exposure=Decimal(str(settings.max_gross_exposure)),
                        max_net_exposure=Decimal(str(settings.max_net_exposure)),
                        max_leverage=Decimal(str(settings.max_leverage)),
                    ),
                    drawdown_pct=None,
                )
                uow.risk_snapshots.upsert(risk_snapshot)

        duration = perf_counter() - job_start
        record_job_completed(
            logger=logger,
            metrics=RISK_SNAPSHOT_JOB_METRICS,
            job=job,
            component=component,
            run_id=str(run_id),
            duration_seconds=duration,
        )

    except Exception as exc:
        duration = perf_counter() - job_start
        record_job_failed(
            logger=logger,
            metrics=RISK_SNAPSHOT_JOB_METRICS,
            job=job,
            component=component,
            run_id=str(run_id),
            exc=exc,
            duration_seconds=duration,
            failure_class=(
                "transient" if isinstance(exc, TransientInfrastructureError) else "unknown"
            ),
        )
        raise
