from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.execution.services.order_reconciliation_service import (
    ReconciliationInput,
    ReconciliationResult,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.tracked_orders import TrackedOrder
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

logger = get_logger(__name__)


class OrderRuntimeStateService:
    """Coordinates persisted runtime order state across submission and reconciliation."""

    def record_submitted_order(
        self,
        *,
        uow: SorUnitOfWork,
        intent,
        broker_order,
        run_id: UUID,
        strategy_id: str,
        account_id: str,
        now_utc: datetime,
    ) -> TrackedOrder:
        row = TrackedOrder(
            order_id=intent.intent_id,
            intent_id=intent.intent_id,
            run_id=run_id,
            strategy_id=strategy_id,
            account_id=account_id,
            symbol=intent.symbol,
            broker_order_id=broker_order.broker_order_id,
            current_status=OrderStatus.SUBMITTED,
            previous_filled_qty=broker_order.filled_qty,
            previous_avg_fill_price=broker_order.avg_fill_price,
            is_open=True,
            created_at=now_utc,
            updated_at=now_utc,
        )
        return uow.tracked_orders.upsert(row)

    def record_rejected_order(
        self,
        *,
        uow: SorUnitOfWork,
        intent,
        run_id: UUID,
        strategy_id: str,
        account_id: str,
        now_utc: datetime,
    ) -> TrackedOrder:
        row = TrackedOrder(
            order_id=intent.intent_id,
            intent_id=intent.intent_id,
            run_id=run_id,
            strategy_id=strategy_id,
            account_id=account_id,
            symbol=intent.symbol,
            broker_order_id=None,
            current_status=OrderStatus.REJECTED,
            previous_filled_qty=Decimal("0"),
            previous_avg_fill_price=None,
            is_open=False,
            created_at=now_utc,
            updated_at=now_utc,
        )
        return uow.tracked_orders.upsert(row)

    def list_reconciliation_inputs(
        self,
        *,
        uow: SorUnitOfWork,
    ) -> list[ReconciliationInput]:
        rows = uow.tracked_orders.list_reconcilable_orders()
        return [
            ReconciliationInput(
                order_id=row.order_id,
                broker_order_id=row.broker_order_id,
                intent_id=row.intent_id,
                run_id=row.run_id,
                account_id=row.account_id,
                current_status=row.current_status,
                previous_filled_qty=row.previous_filled_qty,
                previous_avg_fill_price=row.previous_avg_fill_price,
            )
            for row in rows
            if row.broker_order_id is not None
        ]

    def apply_reconciliation_result(
        self,
        *,
        uow: SorUnitOfWork,
        result: ReconciliationResult,
        now_utc: datetime,
    ) -> None:
        existing = uow.tracked_orders.get_by_order_id(result.order_id)
        if existing is None:
            return

        if result.broker_order is None:
            existing.current_status = result.next_status
            existing.is_open = result.next_status in {
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIALLY_FILLED,
            }
            existing.updated_at = now_utc
            uow.tracked_orders.upsert(existing)
            return

        broker_order = result.broker_order

        existing.broker_order_id = broker_order.broker_order_id
        existing.current_status = result.next_status

        # Monotonic guard: never rewind filled_qty to a stale lower value from
        # an out-of-order broker snapshot. Quantity can only progress forward.
        broker_filled_qty = broker_order.filled_qty
        if broker_filled_qty < existing.previous_filled_qty:
            logger.warning(
                "runtime_state.monotonic_qty_guard_triggered",
                extra={
                    "broker_order_id": broker_order.broker_order_id,
                    "intent_id": str(result.order_id),
                    "previous_filled_qty": str(existing.previous_filled_qty),
                    "broker_filled_qty": str(broker_filled_qty),
                },
            )
            broker_filled_qty = existing.previous_filled_qty

        existing.previous_filled_qty = broker_filled_qty
        existing.previous_avg_fill_price = broker_order.avg_fill_price
        existing.is_open = result.next_status in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        }
        existing.updated_at = now_utc

        uow.tracked_orders.upsert(existing)
