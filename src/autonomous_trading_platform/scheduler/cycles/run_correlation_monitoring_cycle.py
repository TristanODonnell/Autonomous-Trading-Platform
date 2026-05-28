from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from autonomous_trading_platform.application.services.correlation_monitoring_service import (
    CorrelationMonitoringConfig,
    CorrelationMonitoringService,
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
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

JOB_NAME = "correlation_monitoring_cycle"
COMPONENT = "scheduler.run_correlation_monitoring_cycle"
CORRELATION_CYCLE_METRICS = CycleMetricSet(
    runs=governance_cycle_runs,
    failures=governance_cycle_failures,
    duration=governance_cycle_duration,
)
logger = get_logger(__name__)


def run_correlation_monitoring_cycle(
    now_utc: datetime | None = None,
    trigger_source: str = "scheduler",
    portfolio_symbols: list[str] | None = None,
    strategy_ids: list[str] | None = None,
    sector_map: dict[str, str] | None = None,
    symbol_windows: list[int] | None = None,
) -> dict:
    """Compute and persist rolling correlation/covariance snapshots.

    Designed to run post-rebalance or on a scheduled cadence (e.g. EOD).
    Does not alter allocation or trading behavior — observability only.

    Args:
        now_utc: Reference timestamp; defaults to utcnow.
        trigger_source: Label for audit (e.g. "scheduler", "post_rebalance").
        portfolio_symbols: Symbols to include in symbol-level matrices.
        strategy_ids: Strategy IDs for strategy-level correlations.
        sector_map: {symbol: sector} mapping for sector aggregation.
        symbol_windows: Override default lookback windows.
    """
    if now_utc is None:
        now_utc = datetime.now(UTC)

    session = get_session()
    run_id = uuid4()
    cycle_wall_start = perf_counter()

    manifest = create_governance_manifest(
        session=session,
        run_id=run_id,
        job_name=JOB_NAME,
        governance_action="correlation_monitoring",
        input_settings={
            "now_utc": now_utc.isoformat(),
            "symbol_count": len(portfolio_symbols) if portfolio_symbols else 0,
            "strategy_count": len(strategy_ids) if strategy_ids else 0,
            "sector_count": len(set(sector_map.values())) if sector_map else 0,
        },
    )
    session.commit()

    config_kwargs: dict = {}
    if symbol_windows:
        config_kwargs["symbol_windows"] = symbol_windows
        config_kwargs["strategy_windows"] = symbol_windows
        config_kwargs["sector_windows"] = symbol_windows
    config = CorrelationMonitoringConfig(**config_kwargs)

    runner = RuntimeJobRunner(
        repository=RuntimeJobRunRepository(session),
        failure_notifier=PipelineFailureNotificationService(session),
    )

    def job() -> dict:
        try:
            result = CorrelationMonitoringService(session=session, config=config).run(
                as_of=now_utc,
                portfolio_symbols=portfolio_symbols or [],
                strategy_ids=strategy_ids,
                sector_map=sector_map,
                run_id=str(run_id),
            )
            payload = CorrelationMonitoringService.result_to_jsonable(result)
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
            metrics=CORRELATION_CYCLE_METRICS,
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
            cycle_span.set_attribute("ratp.governance_action", "correlation_monitoring")
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
                    metrics=CORRELATION_CYCLE_METRICS,
                    component=COMPONENT,
                    run_id=str(run_id),
                    duration_seconds=perf_counter() - cycle_wall_start,
                )
                session.commit()
                return result or {}
            except Exception as exc:
                record_cycle_failed(
                    logger=logger,
                    metrics=CORRELATION_CYCLE_METRICS,
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


if __name__ == "__main__":
    run_correlation_monitoring_cycle()
