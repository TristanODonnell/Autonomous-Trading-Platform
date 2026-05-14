from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from autonomous_trading_platform.application.services.auto_demotion_service import (
    AutoDemotionService,
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

JOB_NAME = "strategy_auto_demotion_cycle"


def run_governance_demotion_cycle(
    now_utc: datetime | None = None,
    trigger_source: str = "scheduler",
) -> dict:
    return run_strategy_auto_demotion_cycle(now_utc=now_utc, trigger_source=trigger_source)


def run_strategy_auto_demotion_cycle(
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
        governance_action="auto_demotion",
        input_settings={
            "auto_demote_on_breach": bool(settings.auto_demote_on_breach),
            "max_strategy_drawdown": float(settings.max_strategy_drawdown),
            "notify_drawdown_alerts": bool(settings.notify_drawdown_alerts),
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
            result = AutoDemotionService(session=session).run(
                run_id=str(run_id),
                actor=trigger_source,
            )
            payload = AutoDemotionService.result_to_jsonable(result)
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
                "component": "scheduler.run_strategy_auto_demotion_cycle",
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
    run_strategy_auto_demotion_cycle()
