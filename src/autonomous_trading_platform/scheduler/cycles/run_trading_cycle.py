from datetime import UTC, datetime

from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.common.errors import (
    PersistentInfrastructureError,
    TransientInfrastructureError,
)
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.execution.errors import ExecutionError
from autonomous_trading_platform.execution.services.trading_freeze_service import (
    TradingFreezeService,
)
from autonomous_trading_platform.safety.errors import SafetyError
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_base_metadata,
    build_trading_cycle_dependencies,
    build_trading_cycle_window,
    build_trading_run_manifest,
    new_trading_run_id,
)
from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
    check_ingestion_readiness_job,
)
from autonomous_trading_platform.scheduler.jobs.run_order_reconciliation_job import (
    run_order_reconciliation_job,
)
from autonomous_trading_platform.scheduler.jobs.run_order_submission_job import (
    run_order_submission_job,
)
from autonomous_trading_platform.scheduler.jobs.run_risk_snapshot_job import (
    run_risk_snapshot_job,
)
from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
    run_trading_evaluation_job,
)


def run_trading_cycle(now_utc: datetime | None = None):
    if now_utc is None:
        now_utc = datetime.now(UTC)

    trading_cycle_dependencies = build_trading_cycle_dependencies()

    settings = trading_cycle_dependencies.settings
    session = trading_cycle_dependencies.session
    audit_logger = trading_cycle_dependencies.audit_logger
    manifest_service = trading_cycle_dependencies.manifest_service
    safety_context = trading_cycle_dependencies.safety_context
    freeze_service = TradingFreezeService()
    component = "scheduler.run_trading_cycle"
    expected_symbols = {"SPY"}

    if freeze_service.is_trading_frozen():
        audit_logger.record_run_completed(
            run_id="frozen_skip",
            component=component,
            metadata={
                "status": "skipped_due_to_freeze",
            },
        )
        return
    run_id = new_trading_run_id()

    trading_cycle_window = build_trading_cycle_window(
        now_utc=now_utc,
        # ingestion_grace_seconds= # overload default
    )

    manifest = build_trading_run_manifest(
        run_id=run_id,
        now_utc=now_utc,
        cycle_start=trading_cycle_window.cycle_start,
        cycle_end=trading_cycle_window.cycle_end,
    )
    manifest.status = "running"
    manifest.current_step = "starting"
    manifest.last_successful_step = None
    manifest.bar_timestamp = trading_cycle_window.cycle_end
    manifest.error_message = None
    manifest_service.save(manifest)

    base_metadata = build_trading_base_metadata(
        cycle_start=trading_cycle_window.cycle_start,
        cycle_end=trading_cycle_window.cycle_end,
        expected_symbols=expected_symbols,
        manifest=manifest,
    )

    try:
        audit_logger.record_run_started(
            run_id=str(run_id),
            component=component,
            metadata=base_metadata,
        )

        if settings.trading_environment is TradingEnvironment.LIVE:
            safety_context.live_trading_gate_service.assert_live_trading_allowed(
                manifest.broker_account_id
            )
        print("step: ingestion_readiness")
        manifest.current_step = "ingestion_readiness"
        manifest_service.save(manifest)
        ingestion_result = check_ingestion_readiness_job(now_utc=now_utc)
        print("readiness:", ingestion_result.ready)
        print("safe_mode:", ingestion_result.safe_mode)
        print("reason:", ingestion_result.reason)

        if not ingestion_result.ready:
            if settings.skip_evaluation_on_ingestion_failure:
                audit_logger.record_run_completed(
                    run_id=str(run_id),
                    component=component,
                    metadata={
                        **base_metadata,
                        "degraded_mode": "skip_evaluation",
                        "reason": ingestion_result.reason,
                    },
                )
                manifest.status = "completed"
                manifest.current_step = None
                manifest.error_message = ingestion_result.reason
                manifest_service.save(manifest)
                return
            raise RuntimeError(f"Ingestion readiness failed: {ingestion_result.reason}")

        manifest.last_successful_step = "ingestion_readiness"
        manifest_service.save(manifest)

        print("step: trading_evaluation")

        manifest.current_step = "trading_evaluation"
        manifest_service.save(manifest)

        try:
            _, generated_intents = run_trading_evaluation_job(
                now_utc=now_utc,
                trading_cycle_dependencies=trading_cycle_dependencies,
                manifest=manifest,
            )
        except Exception as exc:
            if settings.hold_positions_on_evaluation_failure:
                audit_logger.record_run_completed(
                    run_id=str(run_id),
                    component=component,
                    metadata={
                        **base_metadata,
                        "degraded_mode": "hold_positions",
                        "reason": str(exc),
                    },
                )
                manifest.status = "completed"
                manifest.current_step = None
                manifest.error_message = str(exc)
                manifest_service.save(manifest)
                return
            raise

        manifest.last_successful_step = "trading_evaluation"
        manifest_service.save(manifest)

        print("step: order_submission")

        manifest.current_step = "order_submission"
        manifest_service.save(manifest)

        run_order_submission_job(
            now_utc=now_utc,
            trading_cycle_dependencies=trading_cycle_dependencies,
            manifest=manifest,
            run_id=run_id,
            generated_intents=generated_intents,
        )

        manifest.last_successful_step = "order_submission"
        manifest_service.save(manifest)

        print("step: order_reconciliation")

        manifest.current_step = "order_reconciliation"
        manifest_service.save(manifest)

        try:
            run_order_reconciliation_job(now_utc=now_utc)
        except Exception as exc:
            if settings.freeze_trading_on_reconciliation_failure:
                freeze_service.freeze_trading(
                    reason=f"reconciliation_failure: {exc}",
                    source=component,
                )
            manifest.status = "failed"
            manifest.error_message = str(exc)
            manifest_service.save(manifest)
            raise

        manifest.last_successful_step = "order_reconciliation"
        manifest_service.save(manifest)

        print("step: risk_snapshot")

        manifest.current_step = "risk_snapshot"
        manifest_service.save(manifest)

        run_risk_snapshot_job(
            now_utc=now_utc,
            trading_cycle_dependencies=trading_cycle_dependencies,
            run_id=run_id,
        )

        manifest.last_successful_step = "risk_snapshot"
        manifest_service.save(manifest)

        audit_logger.record_run_completed(
            run_id=str(run_id),
            component=component,
            metadata=base_metadata,
        )

        manifest.status = "completed"
        manifest.current_step = None
        manifest.error_message = None
        manifest_service.save(manifest)

    except TransientInfrastructureError as exc:
        # retry case → let Airflow retry
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
                "failure_class": "transient",
                "action": "retry_by_airflow",
            },
        )
        manifest.status = "failed"
        manifest.error_message = str(exc)
        manifest_service.save(manifest)
        raise
    except (SafetyError, ExecutionError, PersistentInfrastructureError) as exc:
        # persistent failure → stop trading, require manual intervention
        freeze_service.freeze_trading(
            reason=f"critical_failure: {exc}",
            source=component,
        )
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
                "failure_class": "persistent",
                "action": "manual_intervention_required",
            },
        )
        manifest.status = "failed"
        manifest.error_message = str(exc)
        manifest_service.save(manifest)
        raise

    except Exception as exc:
        # unknown → fail closed
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
                "failure_class": "unknown",
            },
        )
        manifest.status = "failed"
        manifest.error_message = str(exc)
        manifest_service.save(manifest)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    print("starting trading cycle")
    run_trading_cycle(parse_datetime("2025-02-14T21:00:00Z"))
    print("returned from trading cycle")
