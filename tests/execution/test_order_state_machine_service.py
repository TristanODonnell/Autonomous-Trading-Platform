from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from autonomous_trading_platform.contracts.common.enums import OrderEvent, OrderStatus
from autonomous_trading_platform.execution.errors import InvalidOrderTransitionError
from autonomous_trading_platform.execution.services.order_state_machine_service import (
    VALID_TRANSITIONS,
    OrderStateMachineService,
)


@pytest.fixture
def audit_logger() -> Mock:
    return Mock()


@pytest.fixture
def service(audit_logger: Mock) -> OrderStateMachineService:
    return OrderStateMachineService(audit_logger=audit_logger)


def test_apply_event_all_valid_transitions_updates_state_and_writes_audit_log(
    service: OrderStateMachineService,
    audit_logger: Mock,
) -> None:
    order_id = uuid4()
    timestamp = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)

    for current_status, transitions in VALID_TRANSITIONS.items():
        for event, expected_next_status in transitions.items():
            audit_logger.reset_mock()

            result = service.apply_event(
                order_id=order_id,
                current_status=current_status,
                event=event,
                event_timestamp=timestamp,
                run_id="test-run",
                metadata={"source": "pytest"},
            )

            assert result == expected_next_status

            audit_logger.add.assert_called_once()
            audit_event = audit_logger.add.call_args.args[0]

            assert audit_event.run_id == "test-run"
            assert audit_event.event_type == "order_transition"
            assert audit_event.component == "order_state_machine"
            assert audit_event.event_timestamp == timestamp
            assert str(order_id) in audit_event.message
            assert UUID(audit_event.metadata["order_id"]) == order_id
            assert audit_event.metadata["from_status"] == current_status
            assert audit_event.metadata["to_status"] == expected_next_status
            assert audit_event.metadata["event"] == event
            assert audit_event.metadata["source"] == "pytest"


@pytest.mark.parametrize(
    ("current_status", "event"),
    [
        (OrderStatus.NEW, OrderEvent.PARTIAL_FILL),
        (OrderStatus.NEW, OrderEvent.FULL_FILL),
        (OrderStatus.NEW, OrderEvent.CANCEL),
        (OrderStatus.SUBMITTED, OrderEvent.SUBMIT),
        (OrderStatus.PARTIALLY_FILLED, OrderEvent.SUBMIT),
        (OrderStatus.PARTIALLY_FILLED, OrderEvent.REJECT),
    ],
)
def test_apply_event_invalid_transition_raises_error(
    service: OrderStateMachineService,
    audit_logger: Mock,
    current_status: OrderStatus,
    event: OrderEvent,
) -> None:
    order_id = uuid4()

    with pytest.raises(InvalidOrderTransitionError) as exc_info:
        service.apply_event(
            order_id=order_id,
            current_status=current_status,
            event=event,
        )

    assert "Invalid transition" in str(exc_info.value)
    audit_logger.add.assert_not_called()


@pytest.mark.parametrize(
    "terminal_status",
    [
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    ],
)
@pytest.mark.parametrize("event", list(OrderEvent))
def test_apply_event_rejects_all_events_after_terminal_state(
    service: OrderStateMachineService,
    audit_logger: Mock,
    terminal_status: OrderStatus,
    event: OrderEvent,
) -> None:
    order_id = uuid4()

    with pytest.raises(InvalidOrderTransitionError) as exc_info:
        service.apply_event(
            order_id=order_id,
            current_status=terminal_status,
            event=event,
        )

    assert "Invalid transition" in str(exc_info.value)
    audit_logger.add.assert_not_called()


def test_apply_event_uses_current_time_when_timestamp_not_provided(
    service: OrderStateMachineService,
    audit_logger: Mock,
) -> None:
    order_id = uuid4()

    result = service.apply_event(
        order_id=order_id,
        current_status=OrderStatus.NEW,
        event=OrderEvent.SUBMIT,
    )

    assert result == OrderStatus.SUBMITTED

    audit_logger.add.assert_called_once()
    audit_event = audit_logger.add.call_args.args[0]
    assert audit_event.event_timestamp is not None
