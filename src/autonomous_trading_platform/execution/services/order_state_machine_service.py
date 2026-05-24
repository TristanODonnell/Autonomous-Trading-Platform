from datetime import UTC, datetime
from uuid import UUID, uuid4

from autonomous_trading_platform.contracts.common.enums import OrderEvent, OrderStatus
from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)

from ..errors import InvalidOrderTransitionError

VALID_TRANSITIONS = {
    OrderStatus.NEW: {
        OrderEvent.PENDING_SUBMIT: OrderStatus.PENDING_NEW,
        OrderEvent.SUBMIT: OrderStatus.SUBMITTED,
        OrderEvent.REJECT: OrderStatus.REJECTED,
    },
    OrderStatus.PENDING_NEW: {
        OrderEvent.SUBMIT: OrderStatus.SUBMITTED,
        OrderEvent.REJECT: OrderStatus.REJECTED,
        OrderEvent.EXPIRE: OrderStatus.EXPIRED,
    },
    OrderStatus.SUBMITTED: {
        OrderEvent.PARTIAL_FILL: OrderStatus.PARTIALLY_FILLED,
        OrderEvent.FULL_FILL: OrderStatus.FILLED,
        OrderEvent.CANCEL: OrderStatus.CANCELED,
        OrderEvent.REJECT: OrderStatus.REJECTED,
        OrderEvent.REQUEST_CANCEL: OrderStatus.PENDING_CANCEL,
        OrderEvent.EXPIRE: OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderEvent.PARTIAL_FILL: OrderStatus.PARTIALLY_FILLED,
        OrderEvent.FULL_FILL: OrderStatus.FILLED,
        OrderEvent.CANCEL: OrderStatus.CANCELED,
        OrderEvent.REQUEST_CANCEL: OrderStatus.PENDING_CANCEL,
        OrderEvent.EXPIRE: OrderStatus.EXPIRED,
    },
    OrderStatus.PENDING_CANCEL: {
        OrderEvent.CANCEL: OrderStatus.CANCELED,
        OrderEvent.FULL_FILL: OrderStatus.FILLED,
        OrderEvent.PARTIAL_FILL: OrderStatus.PARTIALLY_FILLED,
        OrderEvent.EXPIRE: OrderStatus.EXPIRED,
    },
    OrderStatus.FILLED: {},
    OrderStatus.CANCELED: {},
    OrderStatus.REJECTED: {},
    OrderStatus.EXPIRED: {},
}


class OrderStateMachineService:
    def __init__(self, audit_logger: AuditLogRepository) -> None:
        self.audit_logger = audit_logger

    def apply_event(
        self,
        order_id: UUID,
        current_status: OrderStatus,
        event: OrderEvent,
        event_timestamp: datetime | None = None,
        run_id: str | None = None,
        metadata: dict | None = None,
    ) -> OrderStatus:
        timestamp = event_timestamp or datetime.now(UTC)

        # 1. Resolve next state
        try:
            next_status = VALID_TRANSITIONS[current_status][event]
        except KeyError as exc:
            raise InvalidOrderTransitionError(
                f"Invalid transition: {current_status} -> {event}"
            ) from exc

        # 2. Record audit log
        audit_event = AuditLogEvent(
            event_id=str(uuid4()),
            run_id=run_id,
            event_type="order_transition",
            component="order_state_machine",
            event_timestamp=timestamp,
            message=(
                f"Order {order_id} transitioned from {current_status} to {next_status} via {event}"
            ),
            metadata={
                "order_id": str(order_id),
                "from_status": current_status.value,
                "to_status": next_status.value,
                "event": event.value,
                **(metadata or {}),
            },
        )

        self.audit_logger.add(audit_event)

        # 3. Return new state
        return next_status
