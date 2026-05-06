# autonomous_trading_platform/scheduler/jobs/run_order_submission_job.py
from __future__ import annotations

from datetime import datetime
from time import perf_counter
from uuid import UUID

from autonomous_trading_platform.common.errors import (
    TransientInfrastructureError,
)
from autonomous_trading_platform.contracts.common.enums import (
    OrderEvent,
    OrderStatus,
    StrategyEvent,
)
from autonomous_trading_platform.contracts.execution.execution_policy_config import (
    ExecutionPolicyConfig,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.execution.errors import OrderNotAllowedForSubmissionError
from autonomous_trading_platform.execution.policy.errors import ExecutionPolicyError
from autonomous_trading_platform.observability.enums import SpanTimespan
from autonomous_trading_platform.observability.lifecycle import (
    JobMetricSet,
    record_job_completed,
    record_job_failed,
    record_job_started,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    order_submission_job_duration,
    order_submission_job_failures,
    order_submission_job_runs,
    order_submission_latency_seconds,
    order_submission_risk_rejection_count,
)
from autonomous_trading_platform.observability.tracing import start_span
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    TradingCycleDependencies,
)
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)

ORDER_SUBMISSION_JOB_METRICS = JobMetricSet(
    runs=order_submission_job_runs,
    failures=order_submission_job_failures,
    duration=order_submission_job_duration,
)


def _resolve_execution_policy(intent) -> ExecutionPolicyConfig:

    if intent.metadata and "execution_policy" in intent.metadata:
        return ExecutionPolicyConfig.from_dict(intent.metadata["execution_policy"])
    return ExecutionPolicyConfig.passthrough()


def run_order_submission_job(
    *,
    now_utc: datetime,
    trading_cycle_dependencies: TradingCycleDependencies,
    manifest: RunManifest,
    run_id: UUID,
    generated_intents: list,
) -> None:
    component = "scheduler.jobs.order_submission_job"
    job = "order_submission_job"
    job_start = perf_counter()

    safety_context = trading_cycle_dependencies.safety_context
    execution_context = trading_cycle_dependencies.execution_context
    session = trading_cycle_dependencies.session
    shadow_mode_enabled = safety_context.shadow_mode_service.is_enabled()

    record_job_started(
        logger=logger,
        metrics=ORDER_SUBMISSION_JOB_METRICS,
        job=job,
        component=component,
        run_id=str(run_id),
    )
    try:
        with start_span(
            "order_submission_job.run",
            timespan=SpanTimespan.JOB,
        ) as job_span:
            job_span.set_attribute("ratp.run_id", run_id)
            job_span.set_attribute("ratp.component", component)
            job_span.set_attribute("ratp.job", job)
            job_span.set_attribute("ratp.now_utc", now_utc.isoformat())

            order_intents_created = False

            with SorUnitOfWork(session) as uow:
                current_state = execution_context.strategy_runtime_state_service.get_current_state(
                    uow=uow,
                    strategy_id=manifest.strategy_id,
                )
                print(
                    f"[SUBMISSION] strategy_id={manifest.strategy_id} "
                    f"current_state_before_transition={current_state}"
                )

            for intent in generated_intents:
                if not order_intents_created:
                    with SorUnitOfWork(session) as uow:
                        execution_context.strategy_runtime_state_service.apply_event(
                            uow=uow,
                            strategy_id=manifest.strategy_id,
                            event=StrategyEvent.ORDER_INTENTS_CREATED,
                            now_utc=now_utc,
                        )
                    order_intents_created = True

                order_status = OrderStatus.NEW

                idempotency_key = (
                    safety_context.order_idempotency_service.assert_not_duplicate_within_window(
                        intent,
                        now_utc,
                    )
                )
                intent.idempotency_key = idempotency_key

                with SorUnitOfWork(session) as uow:
                    uow.order_intents.upsert(
                        OrderIntents(
                            intent_id=intent.intent_id,
                            idempotency_key=intent.idempotency_key,
                            run_id=intent.run_id,
                            strategy_id=intent.strategy_id,
                            timestamp=intent.timestamp,
                            bar_timestamp=intent.bar_timestamp,
                            symbol=intent.symbol,
                            side=intent.side,
                            qty=intent.qty,
                            notional=intent.notional,
                            order_type=intent.order_type,
                            limit_price=intent.limit_price,
                            stop_price=intent.stop_price,
                            time_in_force=intent.time_in_force,
                            extended_hours=intent.extended_hours,
                            client_order_id=intent.client_order_id,
                            meta=intent.metadata,
                        )
                    )

                # ── RISK CHECK ────────────────────────────────────
                with start_span(
                    "order_submission_job.risk_check",
                    timespan=SpanTimespan.STEP,
                ) as risk_span:
                    risk_span.set_attribute("ratp.run_id", str(run_id))
                    risk_span.set_attribute("ratp.component", component)
                    risk_span.set_attribute("ratp.job", job)
                    risk_span.set_attribute("ratp.order_intent_id", str(intent.intent_id))
                    risk_span.set_attribute("ratp.symbol", intent.symbol)
                    risk_span.set_attribute("ratp.bar_timestamp", intent.bar_timestamp.isoformat())

                    try:
                        safety_context.order_throttle_service.assert_order_allowed_for_submission(
                            order_intent=intent,
                            now=now_utc,
                            bar_timestamp=intent.bar_timestamp,
                        )
                        risk_span.set_attribute("ratp.risk_check.allowed", True)

                    except OrderNotAllowedForSubmissionError:
                        risk_span.set_attribute("ratp.risk_check.allowed", False)
                        risk_span.set_attribute("ratp.risk_check.rejected", True)
                        order_submission_risk_rejection_count.add(
                            1, {"component": component, "job": job}
                        )
                        raise

                if shadow_mode_enabled:
                    continue

                try:
                    policy_config = _resolve_execution_policy(intent)

                    with start_span(
                        "order_submission_job.execution_policy",
                        timespan=SpanTimespan.STEP,
                    ) as policy_span:
                        policy_span.set_attribute("ratp.run_id", str(run_id))
                        policy_span.set_attribute("ratp.component", component)
                        policy_span.set_attribute("ratp.job", job)
                        policy_span.set_attribute("ratp.order_intent_id", str(intent.intent_id))
                        policy_span.set_attribute("ratp.symbol", intent.symbol)
                        policy_span.set_attribute(
                            "ratp.policy_mode", policy_config.policy_mode.value
                        )

                        try:
                            policy_result = execution_context.execution_policy_engine.apply(
                                intent=intent,
                                config=policy_config,
                            )
                            submit_intent = policy_result.transformed_intent

                            policy_span.set_attribute(
                                "ratp.policy_mode_applied",
                                policy_result.policy_mode_applied.value,
                            )
                            policy_span.set_attribute(
                                "ratp.order_type_resolved",
                                submit_intent.order_type.value,
                            )
                            policy_span.set_attribute("ratp.num_slices", len(policy_result.slices))
                            if policy_result.reference_price is not None:
                                policy_span.set_attribute(
                                    "ratp.reference_price",
                                    str(policy_result.reference_price),
                                )
                            if policy_result.expected_slippage is not None:
                                policy_span.set_attribute(
                                    "ratp.expected_slippage_bps",
                                    str(policy_result.expected_slippage.slippage_bps),
                                )

                        except ExecutionPolicyError as policy_exc:
                            # Policy errors are non-fatal: log, fall back to
                            # original intent, and let the order proceed.
                            # This ensures a misconfigured policy (e.g. LIMIT
                            # with no quote) never silently drops an order.
                            logger.warning(
                                "execution_policy.fallback_to_original_intent",
                                extra={
                                    "symbol": intent.symbol,
                                    "intent_id": str(intent.intent_id),
                                    "policy_mode": policy_config.policy_mode.value,
                                    "error": str(policy_exc),
                                },
                            )
                            policy_span.set_attribute("ratp.policy_fallback", True)
                            policy_span.set_attribute("ratp.policy_error", str(policy_exc))
                            submit_intent = intent
                            policy_result = None

                    # ── SUBMIT ────────────────────────────────────────────────
                    try:
                        submit_start = perf_counter()
                        response = execution_context.order_execution_service.submit(submit_intent)
                        submit_duration = perf_counter() - submit_start

                        order_submission_latency_seconds.record(
                            submit_duration, {"component": component, "job": job}
                        )
                        job_span.set_attribute(
                            "ratp.order_submission.last_latency_seconds", submit_duration
                        )
                    except TimeoutError as exc:
                        raise TransientInfrastructureError(f"broker timeout: {exc}") from exc
                    except ConnectionError as exc:
                        raise TransientInfrastructureError(
                            f"broker connection error: {exc}"
                        ) from exc

                    broker_order = execution_context.broker_order_mapper.to_broker_order(
                        payload=response,
                        intent_id=submit_intent.intent_id,
                        run_id=run_id,
                        account_id=manifest.broker_account_id,
                    )

                    order_status = execution_context.order_state_machine_service.apply_event(
                        order_id=intent.intent_id,
                        current_status=order_status,
                        event=OrderEvent.SUBMIT,
                        event_timestamp=now_utc,
                        run_id=str(run_id),
                        metadata={
                            "symbol": submit_intent.symbol,
                            "strategy_id": submit_intent.strategy_id,
                            "broker_order_id": broker_order.broker_order_id,
                            "broker_status": broker_order.status.value,
                            "policy_mode": (
                                policy_result.policy_mode_applied.value
                                if policy_result
                                else "passthrough"
                            ),
                        },
                    )

                    with SorUnitOfWork(session) as uow:
                        uow.broker_orders.upsert(
                            execution_context.broker_order_mapper.to_orm_row(broker_order)
                        )
                        execution_context.order_runtime_state_service.record_submitted_order(
                            uow=uow,
                            intent=submit_intent,
                            broker_order=broker_order,
                            run_id=run_id,
                            strategy_id=manifest.strategy_id,
                            account_id=manifest.broker_account_id,
                            now_utc=now_utc,
                        )

                    if policy_result is not None and policy_config.record_fill_quality:
                        try:
                            execution_context.realised_slippage_service.record_submission_context(
                                intent=submit_intent,
                                broker_order=broker_order,
                                policy_result=policy_result,
                                submit_duration_seconds=submit_duration,
                                now_utc=now_utc,
                            )
                        except Exception as analytics_exc:
                            # Analytics failure is non-fatal. Log and continue.
                            logger.warning(
                                "execution_policy.fill_quality_record_failed",
                                extra={
                                    "symbol": submit_intent.symbol,
                                    "intent_id": str(submit_intent.intent_id),
                                    "error": str(analytics_exc),
                                },
                            )

                except TransientInfrastructureError:
                    raise

                except Exception as exc:
                    execution_context.order_state_machine_service.apply_event(
                        order_id=intent.intent_id,
                        current_status=order_status,
                        event=OrderEvent.REJECT,
                        event_timestamp=now_utc,
                        run_id=str(run_id),
                        metadata={
                            "symbol": intent.symbol,
                            "strategy_id": intent.strategy_id,
                            "error": str(exc),
                        },
                    )

                    with SorUnitOfWork(session) as uow:
                        execution_context.order_runtime_state_service.record_rejected_order(
                            uow=uow,
                            intent=intent,
                            run_id=run_id,
                            strategy_id=manifest.strategy_id,
                            account_id=manifest.broker_account_id,
                            now_utc=now_utc,
                        )

                    raise

        duration = perf_counter() - job_start
        record_job_completed(
            logger=logger,
            metrics=ORDER_SUBMISSION_JOB_METRICS,
            job=job,
            component=component,
            run_id=str(run_id),
            duration_seconds=duration,
        )

    except Exception as exc:
        duration = perf_counter() - job_start
        record_job_failed(
            logger=logger,
            metrics=ORDER_SUBMISSION_JOB_METRICS,
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
