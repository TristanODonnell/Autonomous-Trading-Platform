from __future__ import annotations

from datetime import datetime
from uuid import UUID

from autonomous_trading_platform.common.errors import (
    TransientInfrastructureError,
)
from autonomous_trading_platform.contracts.common.enums import (
    OrderEvent,
    OrderStatus,
    StrategyEvent,
)
from autonomous_trading_platform.contracts.runtime.run_manifest import RunManifest
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    TradingCycleDependencies,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


def run_order_submission_job(
    *,
    now_utc: datetime,
    trading_cycle_dependencies: TradingCycleDependencies,
    manifest: RunManifest,
    run_id: UUID,
    generated_intents: list,
) -> None:

    safety_context = trading_cycle_dependencies.safety_context
    execution_context = trading_cycle_dependencies.execution_context
    session = trading_cycle_dependencies.session
    shadow_mode_enabled = safety_context.shadow_mode_service.is_enabled()

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

        safety_context.order_throttle_service.assert_order_allowed_for_submission(
            order_intent=intent,
            now=now_utc,
            bar_timestamp=intent.bar_timestamp,
        )

        if shadow_mode_enabled:
            continue

        try:
            try:
                response = execution_context.order_execution_service.submit(intent)
            except TimeoutError as exc:
                raise TransientInfrastructureError(f"broker timeout: {exc}") from exc
            except ConnectionError as exc:
                raise TransientInfrastructureError(f"broker connection error: {exc}") from exc

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

        except TransientInfrastructureError:
            raise

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
