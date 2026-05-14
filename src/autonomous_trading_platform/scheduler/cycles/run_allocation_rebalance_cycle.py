from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    QualityBasedReallocationService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.runtime.services.pipeline_failure_notification_service import (
    PipelineFailureNotificationService,
)
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner
from autonomous_trading_platform.scheduler.cycles.governance_automation_common import (
    complete_governance_manifest,
    create_governance_manifest,
    fail_governance_manifest,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

JOB_NAME = "strategy_allocation_rebalance_cycle"


def run_allocation_rebalance_cycle(
    now_utc: datetime | None = None,
    trigger_source: str = "scheduler",
) -> dict:
    return run_strategy_allocation_rebalance_cycle(now_utc=now_utc, trigger_source=trigger_source)


def run_strategy_allocation_rebalance_cycle(
    now_utc: datetime | None = None,
    trigger_source: str = "scheduler",
) -> dict:
    if now_utc is None:
        now_utc = datetime.now(UTC)

    session = get_session()
    run_id = uuid4()
    settings = OperatorSettingsRepository(session).get_or_create_default()
    manifest = create_governance_manifest(
        session=session,
        run_id=run_id,
        job_name=JOB_NAME,
        governance_action="allocation_rebalance",
        input_settings={
            "auto_rebalance_enabled": bool(settings.auto_rebalance_enabled),
            "rebalance_frequency": settings.rebalance_frequency,
            "allocation_targets_source": "capital_allocation_policies + allocation_overrides",
            "now_utc": now_utc.isoformat(),
        },
    )
    session.commit()

    runner = RuntimeJobRunner(
        repository=RuntimeJobRunRepository(session),
        failure_notifier=PipelineFailureNotificationService(session),
    )

    def job() -> dict:
        try:
            result = QualityBasedReallocationService(session=session).rebalance(
                run_id=str(run_id),
                actor=trigger_source,
            )
            payload = QualityBasedReallocationService.result_to_jsonable(result)
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
        result = runner.run(
            job_name=JOB_NAME,
            trigger_type=trigger_source,
            correlation_id=str(run_id),
            input_summary_json={
                "component": "scheduler.run_strategy_allocation_rebalance_cycle",
                "run_manifest_id": str(run_id),
            },
            job=job,
            output_summary_json=lambda payload: payload,
        )
        session.commit()
        return result or {}
    except Exception:
        session.commit()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    run_strategy_allocation_rebalance_cycle()
