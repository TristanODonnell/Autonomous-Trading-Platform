from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    build_trading_cycle_dependencies,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


def run_order_reconciliation_job(now_utc: datetime | None = None) -> None:
    resolved_now = now_utc or datetime.now(UTC)
    dependencies = build_trading_cycle_dependencies()
    session = dependencies.session
    execution_context = dependencies.execution_context
    try:
        with SorUnitOfWork(session) as uow:
            tracked_orders = (
                execution_context.order_runtime_state_service.list_reconciliation_inputs(
                    uow=uow,
                )
            )

        for tracked_order in tracked_orders:
            result = execution_context.order_reconciliation_service.reconcile_order(
                tracked_order,
                now=resolved_now,
            )

            with SorUnitOfWork(session) as uow:
                uow.broker_orders.upsert(result.broker_order)

                if result.fill is not None:
                    uow.fills.upsert(result.fill)

                execution_context.order_runtime_state_service.apply_reconciliation_result(
                    uow=uow,
                    result=result,
                    now_utc=resolved_now,
                )
    finally:
        session.close()
