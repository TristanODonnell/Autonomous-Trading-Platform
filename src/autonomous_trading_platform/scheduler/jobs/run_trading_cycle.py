from datetime import UTC, datetime

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_base_metadata,
    build_trading_cycle_dependencies,
    build_trading_cycle_window,
    build_trading_run_manifest,
    new_trading_run_id,
)
from autonomous_trading_platform.scheduler.jobs.run_order_reconciliation_job import (
    run_order_reconciliation_job,
)
from autonomous_trading_platform.scheduler.jobs.run_order_submission_job import (
    run_order_submission_job,
)
from autonomous_trading_platform.scheduler.jobs.run_trading_evaluation_job import (
    run_trading_evaluation_job,
)


def run_trading_cycle():
    """
    Entry point for airflow DAG.
    """
    now_utc = datetime.now(UTC)
    trading_cycle_dependencies = build_trading_cycle_dependencies()

    settings = trading_cycle_dependencies.settings
    session = trading_cycle_dependencies.session
    audit_logger = trading_cycle_dependencies.audit_logger
    manifest_service = trading_cycle_dependencies.manifest_service

    safety_context = trading_cycle_dependencies.safety_context

    run_id = new_trading_run_id()

    trading_cycle_window = build_trading_cycle_window(
        now_utc=now_utc,
        # ingestion_grace_seconds= # overload default
    )

    component = "scheduler.run_trading_cycle"
    expected_symbols = {"SPY"}

    manifest = build_trading_run_manifest(
        run_id=run_id,
        now_utc=now_utc,
        cycle_start=trading_cycle_window.cycle_start,
        cycle_end=trading_cycle_window.cycle_end,
    )
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

        strategy_job_result, generated_intents = run_trading_evaluation_job(
            now_utc=now_utc,
            trading_cycle_dependencies=trading_cycle_dependencies,
            manifest=manifest,
        )

        run_order_submission_job(
            now_utc=now_utc,
            trading_cycle_dependencies=trading_cycle_dependencies,
            manifest=manifest,
            run_id=run_id,
            generated_intents=generated_intents,
        )

        run_order_reconciliation_job(now_utc=now_utc)

        audit_logger.record_run_completed(
            run_id=str(run_id),
            component=component,
            metadata=base_metadata,
        )
    except Exception as exc:
        audit_logger.record_run_failed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "error": str(exc),
            },
        )
        raise
    finally:
        session.close()
