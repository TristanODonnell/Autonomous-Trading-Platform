from datetime import UTC, datetime
from time import perf_counter

from autonomous_trading_platform.cli.helpers import parse_datetime
from autonomous_trading_platform.common.errors import (
    PersistentInfrastructureError,
    TransientInfrastructureError,
)
from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.execution.errors import ExecutionError
from autonomous_trading_platform.execution.services.trading_freeze_service import (
    TradingFreezeService,
)
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    CycleMetricSet,
    StepMetricSet,
    record_cycle_completed,
    record_cycle_failed,
    record_cycle_started,
    record_step_completed,
    record_step_failed,
    record_step_started,
)
from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    trading_cycle_degraded,
    trading_cycle_duration,
    trading_cycle_failures,
    trading_cycle_runs,
    trading_cycle_step_duration,
    trading_cycle_step_runs,
)
from autonomous_trading_platform.observability.telemetry import setup_telemetry
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.runtime.services.runtime_control_service import (
    RuntimeControlService,
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
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.repositories.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.runtime_job_run_repository import (
    RuntimeJobRunRepository,
)

logger = get_logger(__name__)
TRADING_CYCLE_METRICS = CycleMetricSet(
    runs=trading_cycle_runs,
    failures=trading_cycle_failures,
    duration=trading_cycle_duration,
)
TRADING_STEP_METRICS = StepMetricSet(
    runs=trading_cycle_step_runs,
    duration=trading_cycle_step_duration,
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

    cycle_wall_start = perf_counter()

    if freeze_service.is_trading_frozen():
        logger.warning(
            "trading_cycle_skipped_due_to_freeze: trading is currently frozen",
            extra=LogContext(
                component=component,
                incident_type="trading_frozen",
            ).to_extra(),
        )
        trading_cycle_runs.add(
            1,
            {
                "component": component,
                "status": "skipped_due_to_freeze",
            },
        )
        audit_logger.record_run_completed(
            run_id="frozen_skip",
            component=component,
            metadata={
                "status": "skipped_due_to_freeze",
            },
        )
        return
    run_id = new_trading_run_id()
    runtime_job_run_repository = RuntimeJobRunRepository(session)
    job_run_id = str(run_id)
    job_started_at = now_utc

    def _save_runtime_job_run(
        *,
        status: str,
        completed_at: datetime | None,
        error_message: str | None,
        output_summary_json: dict | None,
    ) -> None:
        runtime_job_run_repository.save(
            RuntimeJobRun(
                job_run_id=job_run_id,
                job_name="trading_cycle",
                parent_job_run_id=None,
                status=status,
                trigger_type="scheduler",
                started_at=job_started_at,
                completed_at=completed_at,
                duration_ms=(
                    int((perf_counter() - cycle_wall_start) * 1000)
                    if completed_at is not None
                    else None
                ),
                error_message=error_message,
                correlation_id=str(run_id),
                input_summary_json={
                    "component": component,
                    "trading_environment": settings.trading_environment.value,
                },
                output_summary_json=output_summary_json,
            )
        )

    _save_runtime_job_run(
        status="running",
        completed_at=None,
        error_message=None,
        output_summary_json=None,
    )

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
    runtime_control_repository = RuntimeControlStateRepository(session)
    runtime_control_service = RuntimeControlService(runtime_control_repository)

    control_block_reason = runtime_control_service.get_cycle_block_reason(
        expected_trading_mode=settings.trading_environment.value,
    )

    if control_block_reason is not None:
        logger.warning(
            "trading_cycle_skipped_due_to_runtime_control_state",
            extra=LogContext(
                run_id=str(run_id),
                component=component,
                incident_type=control_block_reason,
            ).to_extra(),
        )

        audit_logger.record_run_completed(
            run_id=str(run_id),
            component=component,
            metadata={
                **base_metadata,
                "status": "skipped",
                "skip_reason": control_block_reason,
            },
        )

        _save_runtime_job_run(
            status="completed",
            completed_at=datetime.now(UTC),
            error_message=None,
            output_summary_json={
                "status": "skipped",
                "skip_reason": control_block_reason,
                "last_successful_step": manifest.last_successful_step,
                "current_step": None,
            },
        )

        manifest.status = "completed"
        manifest.current_step = None
        manifest.error_message = control_block_reason
        manifest_service.save(manifest)

        trading_cycle_runs.add(
            1,
            {
                "component": component,
                "status": "skipped",
            },
        )

        trading_cycle_duration.record(
            perf_counter() - cycle_wall_start,
            {
                "component": component,
                "status": "skipped",
            },
        )

        return

    record_cycle_started(
        logger=logger,
        metrics=TRADING_CYCLE_METRICS,
        component=component,
        run_id=str(run_id),
    )

    with start_span("trading_cycle.run", timespan=SpanTimespan.CYCLE) as cycle_span:
        cycle_span.set_attribute("ratp.run_id", str(run_id))
        cycle_span.set_attribute("ratp.component", component)
        cycle_span.set_attribute("ratp.cycle_start", trading_cycle_window.cycle_start.isoformat())
        cycle_span.set_attribute("ratp.cycle_end", trading_cycle_window.cycle_end.isoformat())
        cycle_span.set_attribute("ratp.expected_symbol_count", len(expected_symbols))
        cycle_span.set_attribute("ratp.trading_environment", settings.trading_environment.value)
        cycle_span.set_attribute("ratp.strategy_id", manifest.strategy_id)
        cycle_span.set_attribute("ratp.dataset_version", manifest.dataset_version)
        cycle_span.set_attribute("ratp.universe_version", manifest.universe_version)
        cycle_span.set_attribute("ratp.bar_timestamp", manifest.bar_timestamp.isoformat())

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
            # STEP 1: INGESTION READINESS
            step = "ingestion_readiness"
            manifest.current_step = "ingestion_readiness"
            manifest_service.save(manifest)
            record_step_started(
                logger=logger,
                metrics=TRADING_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()
            try:
                with start_span(
                    "trading_cycle.ingestion_readiness", timespan=SpanTimespan.STEP
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.step", step)

                    ingestion_result = check_ingestion_readiness_job(
                        run_id=str(run_id),
                        now_utc=now_utc,
                    )

                    step_span.set_attribute("ratp.ingestion.ready", ingestion_result.ready)
                    step_span.set_attribute(
                        "ratp.ingestion.safe_mode", bool(ingestion_result.safe_mode)
                    )
                    if ingestion_result.reason is not None:
                        step_span.set_attribute(
                            "ratp.ingestion.reason", str(ingestion_result.reason)
                        )

                duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=duration,
                )

                if not ingestion_result.ready:
                    if settings.skip_evaluation_on_ingestion_failure:
                        logger.warning(
                            "trading_cycle_degraded",
                            extra=LogContext(
                                run_id=str(run_id),
                                component=component,
                                incident_type="degraded_mode",
                                error_message=str(ingestion_result.reason),
                            ).to_extra(),
                        )
                        trading_cycle_degraded.add(
                            1,
                            {
                                "component": component,
                                "mode": "skip_evaluation",
                            },
                        )
                        audit_logger.record_run_completed(
                            run_id=str(run_id),
                            component=component,
                            metadata={
                                **base_metadata,
                                "degraded_mode": "skip_evaluation",
                                "reason": ingestion_result.reason,
                            },
                        )
                        _save_runtime_job_run(
                            status="completed",
                            completed_at=datetime.now(UTC),
                            error_message=None,
                            output_summary_json={
                                "last_successful_step": manifest.last_successful_step,
                                "current_step": None,
                            },
                        )
                        manifest.status = "completed"
                        manifest.current_step = None
                        manifest.error_message = ingestion_result.reason
                        manifest_service.save(manifest)

                        total_duration = perf_counter() - cycle_wall_start
                        trading_cycle_runs.add(
                            1,
                            {
                                "component": component,
                                "status": "completed_degraded",
                            },
                        )
                        trading_cycle_duration.record(
                            total_duration,
                            {
                                "component": component,
                                "status": "completed_degraded",
                            },
                        )
                        return
                    raise RuntimeError(f"Ingestion readiness failed: {ingestion_result.reason}")

                manifest.last_successful_step = "ingestion_readiness"

                latest_raw_bars = (
                    session.query(DatasetVersions)
                    .filter(DatasetVersions.dataset_name == "raw_bars")
                    .order_by(DatasetVersions.created_at.desc())
                    .first()
                )
                if latest_raw_bars is not None:
                    manifest.dataset_version = latest_raw_bars.dataset_version_id

                manifest_service.save(manifest)

            except Exception as exc:
                duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=duration,
                )
                raise

            # STEP 2: TRADING EVALUATION
            step = "trading_evaluation"
            manifest.current_step = step
            manifest_service.save(manifest)
            record_step_started(
                logger=logger,
                metrics=TRADING_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()
            try:
                with start_span(
                    "trading_cycle.trading_evaluation", timespan=SpanTimespan.STEP
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.step", step)

                    try:
                        _, generated_intents = run_trading_evaluation_job(
                            now_utc=now_utc,
                            trading_cycle_dependencies=trading_cycle_dependencies,
                            manifest=manifest,
                        )
                        generated_intents = list(generated_intents)
                    except Exception as exc:
                        if settings.hold_positions_on_evaluation_failure:
                            logger.warning(
                                "trading_cycle_degraded",
                                extra=LogContext(
                                    run_id=str(run_id),
                                    component=component,
                                    incident_type="degraded_mode",
                                    error_message=str(exc),
                                ).to_extra(),
                            )
                            trading_cycle_degraded.add(
                                1,
                                {
                                    "component": component,
                                    "mode": "hold_positions",
                                },
                            )
                            audit_logger.record_run_completed(
                                run_id=str(run_id),
                                component=component,
                                metadata={
                                    **base_metadata,
                                    "degraded_mode": "hold_positions",
                                    "reason": str(exc),
                                },
                            )
                            _save_runtime_job_run(
                                status="completed",
                                completed_at=datetime.now(UTC),
                                error_message=None,
                                output_summary_json={
                                    "last_successful_step": manifest.last_successful_step,
                                    "current_step": None,
                                },
                            )
                            manifest.status = "completed"
                            manifest.current_step = None
                            manifest.error_message = str(exc)
                            manifest_service.save(manifest)

                            total_duration = perf_counter() - cycle_wall_start
                            trading_cycle_runs.add(
                                1,
                                {
                                    "component": component,
                                    "status": "completed_degraded",
                                },
                            )
                            trading_cycle_duration.record(
                                total_duration,
                                {
                                    "component": component,
                                    "status": "completed_degraded",
                                },
                            )
                            return
                        raise

                    step_span.set_attribute("ratp.generated_intent_count", len(generated_intents))

                duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=duration,
                )
                manifest.last_successful_step = "trading_evaluation"
                manifest_service.save(manifest)

            except Exception as exc:
                duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=duration,
                )
                raise

            # STEP 3: ORDER SUBMISSION
            step = "order_submission"
            manifest.current_step = step
            manifest_service.save(manifest)
            record_step_started(
                logger=logger,
                metrics=TRADING_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()

            try:
                with start_span(
                    "trading_cycle.order_submission", timespan=SpanTimespan.STEP
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.step", step)
                    step_span.set_attribute("ratp.generated_intent_count", len(generated_intents))

                    run_order_submission_job(
                        now_utc=now_utc,
                        trading_cycle_dependencies=trading_cycle_dependencies,
                        manifest=manifest,
                        run_id=run_id,
                        generated_intents=generated_intents,
                    )
                duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=duration,
                )
                manifest.last_successful_step = "order_submission"
                manifest_service.save(manifest)

            except Exception as exc:
                duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=duration,
                )
                raise

            # STEP 4: ORDER RECONCILIATION
            step = "order_reconciliation"
            manifest.current_step = step
            manifest_service.save(manifest)
            record_step_started(
                logger=logger,
                metrics=TRADING_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()

            try:
                with start_span(
                    "trading_cycle.order_reconciliation", timespan=SpanTimespan.STEP
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.step", step)

                    run_order_reconciliation_job(run_id=str(run_id), now_utc=now_utc)

                duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=duration,
                )
                manifest.last_successful_step = step
                manifest_service.save(manifest)

            except Exception as exc:
                if settings.freeze_trading_on_reconciliation_failure:
                    freeze_service.freeze_trading(
                        reason=f"reconciliation_failure: {exc}",
                        source=component,
                    )
                duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=duration,
                )
                manifest.status = "failed"
                manifest.error_message = str(exc)
                manifest_service.save(manifest)
                raise

            # STEP 5: RISK SNAPSHOT
            step = "risk_snapshot"
            manifest.current_step = step
            manifest_service.save(manifest)
            record_step_started(
                logger=logger,
                metrics=TRADING_STEP_METRICS,
                step=step,
                component=component,
                run_id=str(run_id),
            )

            step_start = perf_counter()

            try:
                with start_span(
                    "trading_cycle.risk_snapshot", timespan=SpanTimespan.STEP
                ) as step_span:
                    step_span.set_attribute("ratp.run_id", str(run_id))
                    step_span.set_attribute("ratp.step", step)

                    run_risk_snapshot_job(
                        now_utc=now_utc,
                        trading_cycle_dependencies=trading_cycle_dependencies,
                        run_id=run_id,
                    )

                duration = perf_counter() - step_start
                record_step_completed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    duration_seconds=duration,
                )
                manifest.last_successful_step = "risk_snapshot"
                manifest_service.save(manifest)

            except Exception as exc:
                duration = perf_counter() - step_start
                record_step_failed(
                    logger=logger,
                    metrics=TRADING_STEP_METRICS,
                    step=step,
                    component=component,
                    run_id=str(run_id),
                    exc=exc,
                    duration_seconds=duration,
                )
                raise

            audit_logger.record_run_completed(
                run_id=str(run_id),
                component=component,
                metadata=base_metadata,
            )
            _save_runtime_job_run(
                status="completed",
                completed_at=datetime.now(UTC),
                error_message=None,
                output_summary_json={
                    "last_successful_step": manifest.last_successful_step,
                    "current_step": None,
                },
            )
            manifest.status = "completed"
            manifest.current_step = None
            manifest.error_message = None
            manifest_service.save(manifest)

            total_duration = perf_counter() - cycle_wall_start
            record_cycle_completed(
                logger=logger,
                metrics=TRADING_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                duration_seconds=total_duration,
            )

        except TransientInfrastructureError as exc:
            total_duration = perf_counter() - cycle_wall_start
            _save_runtime_job_run(
                status="failed",
                completed_at=datetime.now(UTC),
                error_message=str(exc),
                output_summary_json={
                    "last_successful_step": manifest.last_successful_step,
                    "current_step": manifest.current_step,
                },
            )
            record_cycle_failed(
                logger=logger,
                metrics=TRADING_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                exc=exc,
                duration_seconds=total_duration,
                failure_class="transient",
            )
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
            total_duration = perf_counter() - cycle_wall_start
            _save_runtime_job_run(
                status="failed",
                completed_at=datetime.now(UTC),
                error_message=str(exc),
                output_summary_json={
                    "last_successful_step": manifest.last_successful_step,
                    "current_step": manifest.current_step,
                },
            )
            record_cycle_failed(
                logger=logger,
                metrics=TRADING_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                exc=exc,
                duration_seconds=total_duration,
                failure_class="persistent",
            )
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
            _save_runtime_job_run(
                status="failed",
                completed_at=datetime.now(UTC),
                error_message=str(exc),
                output_summary_json={
                    "last_successful_step": manifest.last_successful_step,
                    "current_step": manifest.current_step,
                },
            )
            total_duration = perf_counter() - cycle_wall_start
            record_cycle_failed(
                logger=logger,
                metrics=TRADING_CYCLE_METRICS,
                component=component,
                run_id=str(run_id),
                exc=exc,
                duration_seconds=total_duration,
                failure_class="unknown",
            )
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
    setup_telemetry("scheduler-trading-cycle")
    run_trading_cycle(parse_datetime("2025-02-14T21:00:00Z"))
