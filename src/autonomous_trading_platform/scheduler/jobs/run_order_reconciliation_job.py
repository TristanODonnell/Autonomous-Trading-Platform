from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.common.errors import TransientInfrastructureError
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
            try:
                result = execution_context.order_reconciliation_service.reconcile_order(
                    tracked_order,
                    now=resolved_now,
                )
            except TimeoutError as exc:
                raise TransientInfrastructureError(f"reconciliation timeout: {exc}") from exc
            except ConnectionError as exc:
                raise TransientInfrastructureError(
                    f"reconciliation connection error: {exc}"
                ) from exc

            with SorUnitOfWork(session) as uow:
                uow.broker_orders.upsert(result.broker_order)

                if result.fill is not None:
                    uow.fills.upsert(result.fill)
                    execution_context.post_fill_accounting_service.apply_fill(
                        uow=uow,
                        fill=result.fill,
                        now_utc=resolved_now,
                    )

                execution_context.order_runtime_state_service.apply_reconciliation_result(
                    uow=uow,
                    result=result,
                    now_utc=resolved_now,
                )
    finally:
        session.close()
