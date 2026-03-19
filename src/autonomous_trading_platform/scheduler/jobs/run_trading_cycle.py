from datetime import UTC, datetime

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.common.enums import (
    OrderEvent,
    OrderStatus,
    StrategyEvent,
)
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_base_metadata,
    build_trading_cycle_dependencies,
    build_trading_cycle_window,
    build_trading_run_manifest,
    new_trading_run_id,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork
from autonomous_trading_platform.strategy.jobs.evaluate_strategy_job import EvaluateStrategyJob


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

    strategy_context = trading_cycle_dependencies.strategy_context
    safety_context = trading_cycle_dependencies.safety_context
    execution_context = trading_cycle_dependencies.execution_context

    signal_writer = strategy_context.signal_writer
    strategy_checkpoint_writer = strategy_context.strategy_checkpoint_writer
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

        evaluate_strategy_job = EvaluateStrategyJob(
            readiness_service=strategy_context.strategy_bar_readiness_service,
            evaluation_service=strategy_context.strategy_evaluation_service,
            signal_writer=signal_writer,
            checkpoint_writer=strategy_checkpoint_writer,
        )

        shadow_mode_enabled = safety_context.shadow_mode_service.is_enabled()

        strategy_job_result = evaluate_strategy_job.run(now_utc)

        if strategy_job_result.signals:
            with SorUnitOfWork(session) as uow:
                execution_context.strategy_runtime_state_service.apply_event(
                    uow=uow,
                    strategy_id=manifest.strategy_id,
                    event=StrategyEvent.SIGNAL_GENERATED,
                    now_utc=now_utc,
                )
        generated_intents = execution_context.portfolio_construction_service.generate_order_intents(
            signals=strategy_job_result.signals,
            positions={},
            prices={"SPY": 500.0},
            run_id=run_id,
            strategy_id=manifest.strategy_id,
            bar_timestamp=strategy_job_result.target_bar_timestamp,
            now=now_utc,
        )

        order_intents_created = False

        for intent in generated_intents:
            if not order_intents_created:
                with SorUnitOfWork(session) as uow:
                    execution_context.strategy_runtime_state_service.apply_event(
                        uow=uow,
                        strategy_id=manifest.strategy_id,
                        event=StrategyEvent.SIGNAL_GENERATED,
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

            safety_context.order_throttle_service.assert_order_allowed_for_submission(
                order_intent=intent,
                now=now_utc,
                bar_timestamp=intent.bar_timestamp,
            )

            if shadow_mode_enabled:
                continue

            try:
                response = execution_context.order_execution_service.submit(intent)

                broker_order = execution_context.broker_order_mapper.to_broker_order(
                    payload=response,
                    intent_id=intent.intent_id,
                    run_id=run_id,
                    account_id=manifest.broker_account_id,
                )

                order_status = execution_context.order_state_machine_service.apply_event(
                    order_id=intent.order_id,
                    current_status=order_status,
                    event=OrderEvent.SUBMIT,
                    event_timestamp=now_utc,
                    run_id=str(run_id),
                    metadata={
                        "symbol": intent.symbol,
                        "strategy_id": intent.strategy_id,
                        "broker_order_id": broker_order.broker_order_id,
                        "broker_status": broker_order.status.value,
                    },
                )

                with SorUnitOfWork(session) as uow:
                    uow.broker_orders.upsert(broker_order)
                    execution_context.order_runtime_state_service.record_submitted_order(
                        uow=uow,
                        intent=intent,
                        broker_order=broker_order,
                        run_id=run_id,
                        strategy_id=manifest.strategy_id,
                        account_id=manifest.broker_account_id,
                        now_utc=now_utc,
                    )

            except Exception as exc:
                execution_context.order_state_machine_service.apply_event(
                    order_id=intent.order_id,
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

        # -----------------------------
        # Reconciliation stage
        # -----------------------------
        with SorUnitOfWork(session) as uow:
            tracked_orders = (
                execution_context.order_runtime_state_service.list_reconciliation_inputs(
                    uow=uow,
                )
            )

        for tracked_order in tracked_orders:
            result = execution_context.order_reconciliation_service.reconcile_order(
                tracked_order,
                now=now_utc,
            )

            with SorUnitOfWork(session) as uow:
                uow.broker_orders.upsert(result.broker_order)

                if result.fill is not None:
                    uow.fills.upsert(result.fill)

                execution_context.order_runtime_state_service.apply_reconciliation_result(
                    uow=uow,
                    result=result,
                    now_utc=now_utc,
                )

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
