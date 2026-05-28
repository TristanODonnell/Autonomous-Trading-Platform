from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from sqlalchemy import select

from autonomous_trading_platform.application.services.factor_exposure_monitoring_service import (
    FactorExposureMonitoringConfig,
    FactorExposureMonitoringService,
)
from autonomous_trading_platform.contracts.runtime.factor_exposure import (
    FactorExposureInputPosition,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    CycleMetricSet,
    record_cycle_completed,
    record_cycle_failed,
    record_cycle_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    governance_cycle_duration,
    governance_cycle_failures,
    governance_cycle_runs,
)
from autonomous_trading_platform.observability.runtime_context import runtime_context
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.pipeline_failure_notification_service import (
    PipelineFailureNotificationService,
)
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.governance_automation_common import (
    complete_governance_manifest,
    create_governance_manifest,
    fail_governance_manifest,
)
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

JOB_NAME = "factor_exposure_monitoring_cycle"
COMPONENT = "scheduler.run_factor_exposure_monitoring_cycle"
FACTOR_CYCLE_METRICS = CycleMetricSet(
    runs=governance_cycle_runs,
    failures=governance_cycle_failures,
    duration=governance_cycle_duration,
)
logger = get_logger(__name__)


def run_factor_exposure_monitoring_cycle(
    now_utc: datetime | None = None,
    trigger_source: str = "scheduler",
    positions: list[FactorExposureInputPosition] | None = None,
    portfolio_id: str | None = None,
    factor_exposure_windows: list[int] | None = None,
    benchmark_symbol: str = "SPY",
) -> dict:
    """Compute and persist factor exposure snapshots.

    Designed for scheduled cadence, post-rebalance recomputation, and historical
    replay. It is observe-only and intentionally does not enforce factor limits.
    """
    if now_utc is None:
        now_utc = datetime.now(UTC)

    session = get_session()
    run_id = uuid4()
    cycle_wall_start = perf_counter()

    if positions is None:
        positions = _load_latest_position_weights(session)

    manifest = create_governance_manifest(
        session=session,
        run_id=run_id,
        job_name=JOB_NAME,
        governance_action="factor_exposure_monitoring",
        input_settings={
            "now_utc": now_utc.isoformat(),
            "portfolio_id": portfolio_id,
            "position_count": len(positions),
            "benchmark_symbol": benchmark_symbol,
            "factor_exposure_windows": factor_exposure_windows,
        },
    )
    session.commit()

    config_kwargs: dict = {"benchmark_symbol": benchmark_symbol}
    if factor_exposure_windows:
        config_kwargs["factor_exposure_windows"] = factor_exposure_windows
    config = FactorExposureMonitoringConfig(**config_kwargs)

    runner = RuntimeJobRunner(
        repository=RuntimeJobRunRepository(session),
        failure_notifier=PipelineFailureNotificationService(session),
    )

    def job() -> dict:
        try:
            result = FactorExposureMonitoringService(session=session, config=config).run(
                as_of=now_utc,
                positions=positions or [],
                portfolio_id=portfolio_id,
                run_id=str(run_id),
            )
            payload = FactorExposureMonitoringService.result_to_jsonable(result)
            complete_governance_manifest(
                session=session,
                manifest=manifest,
                output_decisions=payload,
            )
            session.commit()
            return payload
        except Exception as exc:
            session.rollback()
            fail_governance_manifest(session=session, manifest=manifest, error=exc)
            session.commit()
            raise

    try:
        record_cycle_started(
            logger=logger,
            metrics=FACTOR_CYCLE_METRICS,
            component=COMPONENT,
            run_id=str(run_id),
        )

        with (
            runtime_context(
                correlation_id=str(run_id),
                run_id=str(run_id),
                environment=manifest.environment,
                strategy_id=manifest.strategy_id,
            ),
            start_span(f"{JOB_NAME}.run", timespan=SpanTimespan.CYCLE) as cycle_span,
        ):
            cycle_span.set_attribute("ratp.component", COMPONENT)
            cycle_span.set_attribute("ratp.run_id", str(run_id))
            cycle_span.set_attribute("ratp.governance_action", "factor_exposure_monitoring")
            try:
                result = runner.run(
                    job_name=JOB_NAME,
                    trigger_type=trigger_source,
                    correlation_id=str(run_id),
                    input_summary_json={
                        "component": COMPONENT,
                        "run_manifest_id": str(run_id),
                    },
                    job=job,
                    output_summary_json=lambda payload: payload,
                )
                record_cycle_completed(
                    logger=logger,
                    metrics=FACTOR_CYCLE_METRICS,
                    component=COMPONENT,
                    run_id=str(run_id),
                    duration_seconds=perf_counter() - cycle_wall_start,
                )
                session.commit()
                return result or {}
            except Exception as exc:
                record_cycle_failed(
                    logger=logger,
                    metrics=FACTOR_CYCLE_METRICS,
                    component=COMPONENT,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=perf_counter() - cycle_wall_start,
                    failure_class=type(exc).__name__,
                )
                session.commit()
                raise
    finally:
        session.close()


def _load_latest_position_weights(session) -> list[FactorExposureInputPosition]:
    latest = session.scalars(
        select(PositionSnapshot).order_by(PositionSnapshot.timestamp.desc()).limit(1)
    ).first()
    if latest is None:
        return []
    items = list(
        session.scalars(
            select(PositionSnapshotItem).where(
                PositionSnapshotItem.snapshot_id == latest.snapshot_id
            )
        ).all()
    )
    total_abs_market_value = sum(abs(float(item.market_value or 0.0)) for item in items)
    if total_abs_market_value <= 0:
        return []
    return [
        FactorExposureInputPosition(
            symbol=item.symbol,
            weight=float(item.market_value or 0.0) / total_abs_market_value,
            strategy_id=None,
            sector=None,
            market_cap=None,
        )
        for item in items
        if item.market_value is not None and float(item.market_value) != 0.0
    ]


if __name__ == "__main__":
    run_factor_exposure_monitoring_cycle()
