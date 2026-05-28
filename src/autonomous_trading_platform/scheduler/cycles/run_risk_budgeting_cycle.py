from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from autonomous_trading_platform.application.services.risk_budgeting_service import (
    AllocationMode,
    RiskBudgetConfig,
    RiskBudgetingService,
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

JOB_NAME = "risk_budgeting_cycle"
COMPONENT = "scheduler.run_risk_budgeting_cycle"
RISK_BUDGET_CYCLE_METRICS = CycleMetricSet(
    runs=governance_cycle_runs,
    failures=governance_cycle_failures,
    duration=governance_cycle_duration,
)
logger = get_logger(__name__)


def run_risk_budgeting_cycle(
    now_utc: datetime | None = None,
    trigger_source: str = "scheduler",
    strategy_ids: list[str] | None = None,
    mode: str = "equal_risk_contribution",
    risk_budgets: dict[str, float] | None = None,
    covariance_lookback_window: int = 60,
) -> dict:
    """Compute and persist risk-budgeted allocation recommendations.

    Designed to run post-rebalance or on a scheduled cadence.
    Does NOT modify existing AllocationOverrides or allocation engine.

    Args:
        now_utc: Reference timestamp; defaults to utcnow.
        trigger_source: Audit label (e.g. "scheduler", "post_rebalance").
        strategy_ids: Strategies to include; required.
        mode: Allocation mode (equal_capital, equal_risk_contribution,
              fixed_risk_budgets, inverse_volatility).
        risk_budgets: Target risk fractions per strategy (fixed mode only).
        covariance_lookback_window: Lookback window to use when fetching
            covariance snapshots from TASK-5.1 storage.
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
        governance_action="risk_budgeting",
        input_settings={
            "now_utc": now_utc.isoformat(),
            "mode": mode,
            "strategy_count": len(strategy_ids) if strategy_ids else 0,
            "covariance_lookback_window": covariance_lookback_window,
        },
    )
    session.commit()

    try:
        allocation_mode = AllocationMode(mode)
    except ValueError:
        allocation_mode = AllocationMode.EQUAL_RISK_CONTRIBUTION

    config = RiskBudgetConfig(
        mode=allocation_mode,
        risk_budgets=risk_budgets or {},
        covariance_lookback_window=covariance_lookback_window,
    )

    runner = RuntimeJobRunner(
        repository=RuntimeJobRunRepository(session),
        failure_notifier=PipelineFailureNotificationService(session),
    )

    def job() -> dict:
        try:
            result = RiskBudgetingService(session=session, config=config).compute(
                strategy_ids=strategy_ids or [],
                run_id=str(run_id),
            )
            payload = RiskBudgetingService.result_to_jsonable(result)
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
            metrics=RISK_BUDGET_CYCLE_METRICS,
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
            cycle_span.set_attribute("ratp.governance_action", "risk_budgeting")
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
                    metrics=RISK_BUDGET_CYCLE_METRICS,
                    component=COMPONENT,
                    run_id=str(run_id),
                    duration_seconds=perf_counter() - cycle_wall_start,
                )
                session.commit()
                return result or {}
            except Exception as exc:
                record_cycle_failed(
                    logger=logger,
                    metrics=RISK_BUDGET_CYCLE_METRICS,
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
    run_risk_budgeting_cycle()
