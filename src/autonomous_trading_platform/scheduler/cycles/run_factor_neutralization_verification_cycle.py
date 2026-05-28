from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from autonomous_trading_platform.application.services.factor_neutralization_service import (
    FactorNeutralizationConfig,
    FactorNeutralizationService,
)
from autonomous_trading_platform.contracts.runtime.factor_neutralization import (
    FactorNeutralizationMode,
    FactorNeutralizationRequest,
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
from autonomous_trading_platform.storage.sor.repositories.core.factor_exposure_snapshot_repository import (
    FactorExposureSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

JOB_NAME = "factor_neutralization_verification_cycle"
COMPONENT = "scheduler.run_factor_neutralization_verification_cycle"
FACTOR_NEUTRALIZATION_CYCLE_METRICS = CycleMetricSet(
    runs=governance_cycle_runs,
    failures=governance_cycle_failures,
    duration=governance_cycle_duration,
)
logger = get_logger(__name__)


def run_factor_neutralization_verification_cycle(
    now_utc: datetime | None = None,
    trigger_source: str = "scheduler",
    portfolio_id: str | None = None,
    mode: FactorNeutralizationMode = FactorNeutralizationMode.OBSERVE_ONLY,
    enabled: bool = True,
) -> dict:
    """Run an observe/advisory factor-neutrality verification from latest snapshot."""
    if now_utc is None:
        now_utc = datetime.now(UTC)

    session = get_session()
    run_id = uuid4()
    cycle_wall_start = perf_counter()

    manifest = create_governance_manifest(
        session=session,
        run_id=run_id,
        job_name=JOB_NAME,
        governance_action="factor_neutralization_verification",
        input_settings={
            "now_utc": now_utc.isoformat(),
            "portfolio_id": portfolio_id,
            "mode": str(mode),
            "enabled": enabled,
        },
    )
    session.commit()

    runner = RuntimeJobRunner(
        repository=RuntimeJobRunRepository(session),
        failure_notifier=PipelineFailureNotificationService(session),
    )

    def job() -> dict:
        try:
            snapshot = FactorExposureSnapshotRepository(session).get_latest_snapshot(
                portfolio_id=portfolio_id
            )
            if snapshot is None:
                payload = {
                    "run_id": str(run_id),
                    "skipped_reason": "missing_factor_exposure_snapshot",
                }
            else:
                request = _request_from_snapshot(snapshot)
                result = FactorNeutralizationService(
                    session=session,
                    config=FactorNeutralizationConfig(enabled=enabled, mode=mode),
                ).neutralize(request=request, run_id=str(run_id))
                payload = FactorNeutralizationService.result_to_jsonable(result)
            complete_governance_manifest(
                session=session, manifest=manifest, output_decisions=payload
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
            metrics=FACTOR_NEUTRALIZATION_CYCLE_METRICS,
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
            cycle_span.set_attribute("ratp.governance_action", "factor_neutralization_verification")
            try:
                result = runner.run(
                    job_name=JOB_NAME,
                    trigger_type=trigger_source,
                    correlation_id=str(run_id),
                    input_summary_json={"component": COMPONENT, "run_manifest_id": str(run_id)},
                    job=job,
                    output_summary_json=lambda payload: payload,
                )
                record_cycle_completed(
                    logger=logger,
                    metrics=FACTOR_NEUTRALIZATION_CYCLE_METRICS,
                    component=COMPONENT,
                    run_id=str(run_id),
                    duration_seconds=perf_counter() - cycle_wall_start,
                )
                session.commit()
                return result or {}
            except Exception as exc:
                record_cycle_failed(
                    logger=logger,
                    metrics=FACTOR_NEUTRALIZATION_CYCLE_METRICS,
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


def _request_from_snapshot(snapshot) -> FactorNeutralizationRequest:
    assets: list[str] = []
    weights: dict[str, float] = {}
    factor_exposures: dict[str, dict[str, float]] = {}
    sector_map: dict[str, str] = {}
    for row in snapshot.symbol_exposures:
        symbol = row["symbol"]
        assets.append(symbol)
        weights[symbol] = float(row["weight"])
        if row.get("sector"):
            sector_map[symbol] = row["sector"]
        factor_exposures[symbol] = {
            factor_name: float(payload["exposure"])
            for factor_name, payload in row.get("exposures", {}).items()
            if payload.get("exposure") is not None
        }
    return FactorNeutralizationRequest(
        assets=assets,
        current_weights=weights,
        factor_exposures=factor_exposures,
        sector_map=sector_map,
        factor_snapshot_id=snapshot.snapshot_id,
        portfolio_id=snapshot.portfolio_id,
    )


if __name__ == "__main__":
    run_factor_neutralization_verification_cycle()
