"""E-02 — Order reconciliation service tests including broker 404 → EXPIRED handling."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock
from uuid import UUID

import httpx
import pytest

from autonomous_trading_platform.contracts.common.enums import (
    OrderEvent,
    OrderStatus,
)
from autonomous_trading_platform.execution.mappers.broker_order_mapper import BrokerOrderMapper
from autonomous_trading_platform.execution.services.order_reconciliation_service import (
    OrderReconciliationService,
    ReconciliationInput,
)
from autonomous_trading_platform.execution.services.order_state_machine_service import (
    OrderStateMachineService,
)

_NOW = datetime(2026, 5, 22, 10, 0, tzinfo=UTC)
_INTENT_ID = UUID("00000000-0000-0000-0000-000000000010")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000011")
_ORDER_ID = UUID("00000000-0000-0000-0000-000000000012")
_BROKER_ORDER_ID = "bo-alpaca-001"


def _make_tracked(
    *,
    current_status: OrderStatus = OrderStatus.SUBMITTED,
    previous_filled_qty: str = "0",
    previous_avg_fill_price: str | None = None,
) -> ReconciliationInput:
    return ReconciliationInput(
        order_id=_ORDER_ID,
        broker_order_id=_BROKER_ORDER_ID,
        intent_id=_INTENT_ID,
        run_id=_RUN_ID,
        account_id="acct-001",
        current_status=current_status,
        previous_filled_qty=Decimal(previous_filled_qty),
        previous_avg_fill_price=Decimal(previous_avg_fill_price)
        if previous_avg_fill_price
        else None,
    )


def _make_broker_payload(
    *,
    status: str = "filled",
    filled_qty: str = "100",
    avg_fill_price: str = "150.00",
) -> dict:
    return {
        "id": _BROKER_ORDER_ID,
        "client_order_id": "co-001",
        "symbol": "AAPL",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": status,
        "filled_qty": filled_qty,
        "filled_avg_price": avg_fill_price,
        "qty": "100",
        "submitted_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
    }


def _make_service() -> tuple[OrderReconciliationService, Mock, Mock]:
    audit_logger = Mock()
    order_execution_service = Mock()
    state_machine = OrderStateMachineService(audit_logger=audit_logger)
    mapper = BrokerOrderMapper()
    service = OrderReconciliationService(
        order_execution_service=order_execution_service,
        broker_order_mapper=mapper,
        order_state_machine_service=state_machine,
    )
    return service, order_execution_service, audit_logger


# ---------------------------------------------------------------------------
# Normal reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_full_fill_transitions_to_filled() -> None:
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.return_value = _make_broker_payload(status="filled", filled_qty="100")
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED, previous_filled_qty="0")

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.next_status == OrderStatus.FILLED
    assert result.event_applied == OrderEvent.FULL_FILL
    assert result.fill is not None
    assert result.fill.quantity == Decimal("100")
    assert result.broker_not_found is False


def test_reconcile_partial_fill_transitions_to_partially_filled() -> None:
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.return_value = _make_broker_payload(
        status="partially_filled", filled_qty="50", avg_fill_price="149.00"
    )
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED, previous_filled_qty="0")

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.next_status == OrderStatus.PARTIALLY_FILLED
    assert result.event_applied == OrderEvent.PARTIAL_FILL
    assert result.fill is not None
    assert result.fill.quantity == Decimal("50")


def test_reconcile_no_change_produces_no_fill_and_preserves_status() -> None:
    """When broker state matches tracked state exactly, no fill is generated and status is preserved."""
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.return_value = _make_broker_payload(
        status="partially_filled", filled_qty="50", avg_fill_price="149.00"
    )
    tracked = _make_tracked(
        current_status=OrderStatus.PARTIALLY_FILLED,
        previous_filled_qty="50",
        previous_avg_fill_price="149.00",
    )

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.next_status == OrderStatus.PARTIALLY_FILLED
    assert result.fill is None  # no incremental fill — delta qty is zero


def test_reconcile_pending_cancel_transitions_correctly() -> None:
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.return_value = _make_broker_payload(
        status="pending_cancel", filled_qty="0", avg_fill_price="0"
    )
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED)

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.next_status == OrderStatus.PENDING_CANCEL
    assert result.event_applied == OrderEvent.REQUEST_CANCEL


def test_reconcile_broker_expired_transitions_to_expired() -> None:
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.return_value = _make_broker_payload(
        status="expired", filled_qty="0", avg_fill_price="0"
    )
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED)

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.next_status == OrderStatus.EXPIRED
    assert result.event_applied == OrderEvent.EXPIRE
    assert result.fill is None


def test_reconcile_done_for_day_transitions_to_expired() -> None:
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.return_value = _make_broker_payload(
        status="done_for_day", filled_qty="0", avg_fill_price="0"
    )
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED)

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.next_status == OrderStatus.EXPIRED


# ---------------------------------------------------------------------------
# E-02: Broker 404 → EXPIRED handling
# ---------------------------------------------------------------------------


def _make_404_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://paper-api.alpaca.markets/v2/orders/{_BROKER_ORDER_ID}")
    response = httpx.Response(404, request=request)
    return httpx.HTTPStatusError("Not Found", request=request, response=response)


@pytest.mark.parametrize(
    "current_status",
    [
        OrderStatus.SUBMITTED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.PENDING_NEW,
        OrderStatus.PENDING_CANCEL,
    ],
)
def test_broker_404_transitions_expirable_order_to_expired(
    current_status: OrderStatus,
) -> None:
    service, execution_svc, audit_logger = _make_service()
    execution_svc.get_order.side_effect = _make_404_error()
    tracked = _make_tracked(current_status=current_status)

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.next_status == OrderStatus.EXPIRED
    assert result.event_applied == OrderEvent.EXPIRE
    assert result.fill is None
    assert result.broker_order is None
    assert result.broker_not_found is True

    # Audit log should record the EXPIRE transition.
    audit_logger.add.assert_called_once()
    audit_event = audit_logger.add.call_args.args[0]
    assert audit_event.metadata["to_status"] == OrderStatus.EXPIRED
    assert audit_event.metadata["event"] == OrderEvent.EXPIRE
    assert "broker_order_not_found_404" in str(audit_event.metadata)


def test_broker_404_on_terminal_order_reraises() -> None:
    """404 for an already-terminal order is unexpected — re-raise for investigation."""
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.side_effect = _make_404_error()
    tracked = _make_tracked(current_status=OrderStatus.FILLED)

    with pytest.raises(httpx.HTTPStatusError):
        service.reconcile_order(tracked, now=_NOW)


def test_broker_404_for_new_order_reraises() -> None:
    """NEW orders are not yet reconcilable; 404 is unexpected."""
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.side_effect = _make_404_error()
    tracked = _make_tracked(current_status=OrderStatus.NEW)

    with pytest.raises(httpx.HTTPStatusError):
        service.reconcile_order(tracked, now=_NOW)


def test_broker_non_404_http_error_reraises() -> None:
    service, execution_svc, _ = _make_service()
    request = httpx.Request("GET", "https://paper-api.alpaca.markets/v2/orders/x")
    response = httpx.Response(500, request=request)
    execution_svc.get_order.side_effect = httpx.HTTPStatusError(
        "Internal Server Error", request=request, response=response
    )
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED)

    with pytest.raises(httpx.HTTPStatusError):
        service.reconcile_order(tracked, now=_NOW)


def test_broker_404_does_not_create_stale_open_order_loop() -> None:
    """
    Reconciliation must not return SUBMITTED after a 404.  Callers that
    check result.next_status and only close is_open when terminal must
    see EXPIRED here, not SUBMITTED.
    """
    service, execution_svc, _ = _make_service()
    execution_svc.get_order.side_effect = _make_404_error()
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED)

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.next_status != OrderStatus.SUBMITTED
    assert result.next_status == OrderStatus.EXPIRED


# ---------------------------------------------------------------------------
# E-02: Status regression detection
# ---------------------------------------------------------------------------


def test_reconcile_status_regression_is_logged_before_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    When broker reports a non-terminal status for an already-terminal order,
    the regression is logged as a warning and an error is raised (cannot apply
    the invalid state machine transition).
    """
    import logging

    from autonomous_trading_platform.execution.errors import InvalidOrderTransitionError

    service, execution_svc, _ = _make_service()
    execution_svc.get_order.return_value = _make_broker_payload(
        status="partially_filled", filled_qty="50", avg_fill_price="150.00"
    )
    tracked = _make_tracked(
        current_status=OrderStatus.FILLED,
        previous_filled_qty="100",
        previous_avg_fill_price="150.00",
    )

    with caplog.at_level(logging.WARNING), pytest.raises(InvalidOrderTransitionError):
        service.reconcile_order(tracked, now=_NOW)

    assert any("status_regression" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# E-02: duplicate fill idempotency via reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_duplicate_broker_update_produces_no_fill() -> None:
    """Polling the same broker state twice must not produce two fills."""
    service, execution_svc, _ = _make_service()
    payload = _make_broker_payload(
        status="partially_filled", filled_qty="50", avg_fill_price="149.00"
    )
    execution_svc.get_order.return_value = payload
    tracked = _make_tracked(
        current_status=OrderStatus.PARTIALLY_FILLED,
        previous_filled_qty="50",
        previous_avg_fill_price="149.00",
    )

    result = service.reconcile_order(tracked, now=_NOW)

    assert result.fill is None
    assert result.next_status == OrderStatus.PARTIALLY_FILLED
