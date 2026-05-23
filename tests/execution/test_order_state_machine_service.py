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
        (OrderStatus.NEW, OrderEvent.REQUEST_CANCEL),
        (OrderStatus.NEW, OrderEvent.EXPIRE),
        (OrderStatus.SUBMITTED, OrderEvent.SUBMIT),
        (OrderStatus.SUBMITTED, OrderEvent.PENDING_SUBMIT),
        (OrderStatus.PARTIALLY_FILLED, OrderEvent.SUBMIT),
        (OrderStatus.PARTIALLY_FILLED, OrderEvent.REJECT),
        (OrderStatus.PARTIALLY_FILLED, OrderEvent.PENDING_SUBMIT),
        (OrderStatus.PENDING_NEW, OrderEvent.PARTIAL_FILL),
        (OrderStatus.PENDING_NEW, OrderEvent.FULL_FILL),
        (OrderStatus.PENDING_NEW, OrderEvent.CANCEL),
        (OrderStatus.PENDING_NEW, OrderEvent.PENDING_SUBMIT),
        (OrderStatus.PENDING_NEW, OrderEvent.REQUEST_CANCEL),
        (OrderStatus.PENDING_CANCEL, OrderEvent.SUBMIT),
        (OrderStatus.PENDING_CANCEL, OrderEvent.REJECT),
        (OrderStatus.PENDING_CANCEL, OrderEvent.PENDING_SUBMIT),
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
        OrderStatus.EXPIRED,
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


# ---------------------------------------------------------------------------
# E-02: PENDING_NEW lifecycle
# ---------------------------------------------------------------------------


def test_pending_new_transition_from_new(
    service: OrderStateMachineService, audit_logger: Mock
) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.NEW,
        event=OrderEvent.PENDING_SUBMIT,
    )
    assert result == OrderStatus.PENDING_NEW


def test_pending_new_advances_to_submitted(
    service: OrderStateMachineService, audit_logger: Mock
) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.PENDING_NEW,
        event=OrderEvent.SUBMIT,
    )
    assert result == OrderStatus.SUBMITTED


def test_pending_new_can_be_rejected(service: OrderStateMachineService, audit_logger: Mock) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.PENDING_NEW,
        event=OrderEvent.REJECT,
    )
    assert result == OrderStatus.REJECTED


def test_pending_new_can_expire(service: OrderStateMachineService, audit_logger: Mock) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.PENDING_NEW,
        event=OrderEvent.EXPIRE,
    )
    assert result == OrderStatus.EXPIRED


# ---------------------------------------------------------------------------
# E-02: PENDING_CANCEL lifecycle
# ---------------------------------------------------------------------------


def test_pending_cancel_transition_from_submitted(
    service: OrderStateMachineService, audit_logger: Mock
) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.SUBMITTED,
        event=OrderEvent.REQUEST_CANCEL,
    )
    assert result == OrderStatus.PENDING_CANCEL


def test_pending_cancel_transition_from_partially_filled(
    service: OrderStateMachineService, audit_logger: Mock
) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.PARTIALLY_FILLED,
        event=OrderEvent.REQUEST_CANCEL,
    )
    assert result == OrderStatus.PENDING_CANCEL


def test_pending_cancel_resolves_to_canceled(
    service: OrderStateMachineService, audit_logger: Mock
) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.PENDING_CANCEL,
        event=OrderEvent.CANCEL,
    )
    assert result == OrderStatus.CANCELED


def test_pending_cancel_can_be_filled_before_cancel_confirmed(
    service: OrderStateMachineService, audit_logger: Mock
) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.PENDING_CANCEL,
        event=OrderEvent.FULL_FILL,
    )
    assert result == OrderStatus.FILLED


def test_pending_cancel_can_expire(service: OrderStateMachineService, audit_logger: Mock) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.PENDING_CANCEL,
        event=OrderEvent.EXPIRE,
    )
    assert result == OrderStatus.EXPIRED


# ---------------------------------------------------------------------------
# E-02: EXPIRED lifecycle
# ---------------------------------------------------------------------------


def test_submitted_can_expire(service: OrderStateMachineService, audit_logger: Mock) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.SUBMITTED,
        event=OrderEvent.EXPIRE,
    )
    assert result == OrderStatus.EXPIRED


def test_partially_filled_can_expire(service: OrderStateMachineService, audit_logger: Mock) -> None:
    result = service.apply_event(
        order_id=uuid4(),
        current_status=OrderStatus.PARTIALLY_FILLED,
        event=OrderEvent.EXPIRE,
    )
    assert result == OrderStatus.EXPIRED


def test_expired_is_terminal(service: OrderStateMachineService, audit_logger: Mock) -> None:
    with pytest.raises(InvalidOrderTransitionError):
        service.apply_event(
            order_id=uuid4(),
            current_status=OrderStatus.EXPIRED,
            event=OrderEvent.SUBMIT,
        )


# ---------------------------------------------------------------------------
# E-02: invalid regressions are blocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terminal_from", "attempt_event"),
    [
        (OrderStatus.FILLED, OrderEvent.SUBMIT),
        (OrderStatus.FILLED, OrderEvent.PARTIAL_FILL),
        (OrderStatus.CANCELED, OrderEvent.PARTIAL_FILL),
        (OrderStatus.EXPIRED, OrderEvent.SUBMIT),
        (OrderStatus.REJECTED, OrderEvent.SUBMIT),
    ],
)
def test_terminal_state_regressions_are_blocked(
    service: OrderStateMachineService,
    audit_logger: Mock,
    terminal_from: OrderStatus,
    attempt_event: OrderEvent,
) -> None:
    with pytest.raises(InvalidOrderTransitionError):
        service.apply_event(
            order_id=uuid4(),
            current_status=terminal_from,
            event=attempt_event,
        )
    audit_logger.add.assert_not_called()
