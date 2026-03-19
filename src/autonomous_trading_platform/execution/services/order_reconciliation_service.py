from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import OrderEvent, OrderStatus
from autonomous_trading_platform.contracts.trading.broker_order import BrokerOrder
from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.execution.mappers.broker_order_mapper import (
    BrokerOrderMapper,
)
from autonomous_trading_platform.execution.services.order_state_machine_service import (
    OrderStateMachineService,
)


@dataclass(frozen=True)
class ReconciliationInput:
    order_id: str
    broker_order_id: str
    intent_id: UUID
    run_id: UUID
    account_id: str
    current_status: OrderStatus
    previous_filled_qty: Decimal
    previous_avg_fill_price: Decimal | None


@dataclass(frozen=True)
class ReconciliationResult:
    order_id: str
    broker_order: BrokerOrder
    next_status: OrderStatus
    event_applied: OrderEvent | None
    fill: Fill | None


class OrderReconciliationService:
    def __init__(
        self,
        *,
        order_execution_service: Any,
        broker_order_mapper: BrokerOrderMapper,
        order_state_machine_service: OrderStateMachineService,
    ) -> None:
        self.order_execution_service = order_execution_service
        self.broker_order_mapper = broker_order_mapper
        self.order_state_machine_service = order_state_machine_service

    def reconcile_order(
        self,
        tracked_order: ReconciliationInput,
        *,
        now: datetime | None = None,
    ) -> ReconciliationResult:
        timestamp = now or datetime.now(UTC)

        raw_payload = self.order_execution_service.get_order(tracked_order.broker_order_id)

        broker_order = self.broker_order_mapper.to_broker_order(
            payload=raw_payload,
            intent_id=tracked_order.intent_id,
            run_id=tracked_order.run_id,
            account_id=tracked_order.account_id,
        )

        fill_result = self.broker_order_mapper.extract_incremental_fill(
            broker_order=broker_order,
            previous_filled_qty=tracked_order.previous_filled_qty,
            previous_avg_fill_price=tracked_order.previous_avg_fill_price,
        )

        event = self.broker_order_mapper.to_order_event(broker_order)

        next_status = tracked_order.current_status
        if event is not None and broker_order.status != tracked_order.current_status:
            next_status = self.order_state_machine_service.apply_event(
                order_id=tracked_order.order_id,
                current_status=tracked_order.current_status,
                event=event,
                event_timestamp=timestamp,
                run_id=str(tracked_order.run_id),
                metadata={
                    "broker_order_id": broker_order.broker_order_id,
                    "broker_status": broker_order.status.value,
                    "filled_qty": str(broker_order.filled_qty),
                    "avg_fill_price": (
                        str(broker_order.avg_fill_price)
                        if broker_order.avg_fill_price is not None
                        else None
                    ),
                },
            )

        return ReconciliationResult(
            order_id=tracked_order.order_id,
            broker_order=broker_order,
            next_status=next_status,
            event_applied=event,
            fill=fill_result.fill,
        )
