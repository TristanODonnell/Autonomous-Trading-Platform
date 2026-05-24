"""E-01 — BrokerStreamFillProcessor tests.

Verifies that stream events are processed with the same idempotent fill
extraction as polling, so fills cannot be double-counted regardless of
delivery path (stream vs poll).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.execution.mappers.broker_order_mapper import BrokerOrderMapper
from autonomous_trading_platform.execution.services.broker_stream_fill_processor import (
    UPDATE_SOURCE_STREAM,
    BrokerStreamFillProcessor,
)
from autonomous_trading_platform.execution.services.order_reconciliation_service import (
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


def _make_processor() -> BrokerStreamFillProcessor:
    audit_logger = Mock()
    state_machine = OrderStateMachineService(audit_logger=audit_logger)
    return BrokerStreamFillProcessor(
        broker_order_mapper=BrokerOrderMapper(),
        order_state_machine_service=state_machine,
    )


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


def _make_fill_event(
    *,
    event_type: str = "fill",
    broker_order_id: str = _BROKER_ORDER_ID,
    status: str = "filled",
    filled_qty: str = "100",
    avg_fill_price: str = "150.00",
    stream_received_at: str | None = None,
) -> dict:
    payload: dict = {
        "event": event_type,
        "order": {
            "id": broker_order_id,
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
        },
    }
    if stream_received_at:
        payload["stream_received_at"] = stream_received_at
    return payload


# ---------------------------------------------------------------------------
# E-01: websocket fill processing
# ---------------------------------------------------------------------------


def test_stream_fill_event_extracts_fill_and_transitions_to_filled() -> None:
    processor = _make_processor()
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED, previous_filled_qty="0")
    event = _make_fill_event(event_type="fill", status="filled", filled_qty="100")

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=_NOW)

    assert result.order_found is True
    assert result.fill_extracted is True
    assert result.fill is not None
    assert result.fill.quantity == Decimal("100")
    assert result.fill.price == Decimal("150.00")
    assert result.new_status == OrderStatus.FILLED
    assert result.status_transitioned is True
    assert result.skipped is False


def test_stream_fill_event_tags_fill_with_stream_source() -> None:
    processor = _make_processor()
    tracked = _make_tracked()
    event = _make_fill_event(status="filled", filled_qty="100")

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=_NOW)

    assert result.fill is not None
    assert result.fill.metadata is not None
    assert result.fill.metadata["update_source"] == UPDATE_SOURCE_STREAM


def test_stream_fill_event_includes_stream_received_at_in_metadata() -> None:
    processor = _make_processor()
    tracked = _make_tracked()
    event = _make_fill_event(status="filled", filled_qty="100")

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=_NOW)

    assert result.fill is not None
    assert "stream_received_at" in (result.fill.metadata or {})


def test_stream_partial_fill_transitions_to_partially_filled() -> None:
    processor = _make_processor()
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED, previous_filled_qty="0")
    event = _make_fill_event(event_type="partial_fill", status="partially_filled", filled_qty="50")

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=_NOW)

    assert result.fill is not None
    assert result.fill.quantity == Decimal("50")
    assert result.new_status == OrderStatus.PARTIALLY_FILLED


# ---------------------------------------------------------------------------
# E-01: poll + stream duplicate — no double fill
# ---------------------------------------------------------------------------


def test_stream_event_after_poll_produces_no_duplicate_fill() -> None:
    """
    Scenario: polling reconciliation already saw filled_qty=100 and updated
    previous_filled_qty to 100. When the same fill arrives later via stream,
    delta_qty = 0 → no fill extracted.
    """
    processor = _make_processor()
    # tracked reflects state AFTER the polling cycle already processed the fill
    tracked = _make_tracked(
        current_status=OrderStatus.FILLED,
        previous_filled_qty="100",
        previous_avg_fill_price="150.00",
    )
    event = _make_fill_event(status="filled", filled_qty="100")

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=_NOW)

    # No new fill — delta qty is zero
    assert result.fill_extracted is False
    assert result.fill is None
    # Status already FILLED from polling, broker says FILLED → no regression, no event
    assert result.status_transitioned is False


def test_poll_after_stream_produces_no_duplicate_fill() -> None:
    """
    Scenario: stream delivered fill first (tracked updated), poll then sees same state.
    After stream updated tracked to filled_qty=100, a poll reconciliation would see
    delta_qty = 0 → no fill.  We simulate by calling process_event twice.
    """
    processor = _make_processor()
    tracked_initial = _make_tracked(previous_filled_qty="0")
    event = _make_fill_event(status="filled", filled_qty="100")

    first_result = processor.process_event(
        event, tracked_order=tracked_initial, stream_received_at=_NOW
    )
    assert first_result.fill is not None
    assert first_result.fill.quantity == Decimal("100")

    # Second call simulates poll seeing same state (tracked updated by caller after first).
    tracked_updated = _make_tracked(
        current_status=OrderStatus.FILLED,
        previous_filled_qty="100",
        previous_avg_fill_price="150.00",
    )
    second_result = processor.process_event(
        event, tracked_order=tracked_updated, stream_received_at=_NOW
    )

    assert second_result.fill is None
    assert second_result.fill_extracted is False


# ---------------------------------------------------------------------------
# E-01: event ordering robustness (monotonic qty)
# ---------------------------------------------------------------------------


def test_stale_stream_event_does_not_rewind_filled_qty() -> None:
    """
    Delayed websocket event (older broker snapshot) must not regress qty.
    """
    processor = _make_processor()
    # Tracked already reflects 75 filled
    tracked = _make_tracked(
        current_status=OrderStatus.PARTIALLY_FILLED,
        previous_filled_qty="75",
        previous_avg_fill_price="150.00",
    )
    # Stale event reports only 50 filled (out-of-order)
    stale_event = _make_fill_event(
        event_type="partial_fill", status="partially_filled", filled_qty="50"
    )

    result = processor.process_event(stale_event, tracked_order=tracked, stream_received_at=_NOW)

    # Must not produce a fill (delta < 0 is treated as stale by mapper)
    assert result.fill is None
    assert result.fill_extracted is False


def test_multiple_incremental_stream_events_accumulate_correctly() -> None:
    """Successive partial fills via stream accumulate without double-counting."""
    processor = _make_processor()
    tracked_qty = Decimal("0")

    fill_sequence = [("25", "149.00"), ("50", "150.00"), ("75", "151.00"), ("100", "150.50")]
    statuses = ["partially_filled", "partially_filled", "partially_filled", "filled"]

    total_fill_qty = Decimal("0")
    for (qty, price), status in zip(fill_sequence, statuses, strict=False):
        event = _make_fill_event(
            event_type="partial_fill" if status == "partially_filled" else "fill",
            status=status,
            filled_qty=qty,
            avg_fill_price=price,
        )
        result = processor.process_event(
            event,
            tracked_order=_make_tracked(
                current_status=(
                    OrderStatus.SUBMITTED if total_fill_qty == 0 else OrderStatus.PARTIALLY_FILLED
                ),
                previous_filled_qty=str(tracked_qty),
                previous_avg_fill_price=str(price) if tracked_qty > 0 else None,
            ),
            stream_received_at=_NOW,
        )
        if result.fill is not None:
            total_fill_qty += result.fill.quantity
            tracked_qty = Decimal(qty)

    assert total_fill_qty == Decimal("100")


# ---------------------------------------------------------------------------
# E-01: skip conditions
# ---------------------------------------------------------------------------


def test_event_with_no_broker_order_id_is_skipped() -> None:
    processor = _make_processor()
    event = {"event": "fill", "order": {}}  # no 'id' key

    result = processor.process_event(event, tracked_order=Mock())

    assert result.skipped is True
    assert result.skip_reason == "missing_broker_order_id"
    assert result.order_found is False


def test_event_for_untracked_order_is_skipped() -> None:
    processor = _make_processor()
    event = _make_fill_event(broker_order_id="unknown-broker-id")

    result = processor.process_event(event, tracked_order=None, stream_received_at=_NOW)

    assert result.skipped is True
    assert result.skip_reason == "order_not_tracked"
    assert result.order_found is False
    assert result.broker_order_id == "unknown-broker-id"


# ---------------------------------------------------------------------------
# E-01: cancel/expire via stream
# ---------------------------------------------------------------------------


def test_stream_cancel_event_transitions_to_canceled() -> None:
    processor = _make_processor()
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED)
    event = _make_fill_event(
        event_type="canceled", status="canceled", filled_qty="0", avg_fill_price="0"
    )

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=_NOW)

    assert result.new_status == OrderStatus.CANCELED
    assert result.status_transitioned is True


def test_stream_expired_event_transitions_to_expired() -> None:
    processor = _make_processor()
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED)
    event = _make_fill_event(
        event_type="expired", status="expired", filled_qty="0", avg_fill_price="0"
    )

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=_NOW)

    assert result.new_status == OrderStatus.EXPIRED
    assert result.status_transitioned is True


def test_stream_pending_cancel_event_transitions_to_pending_cancel() -> None:
    processor = _make_processor()
    tracked = _make_tracked(current_status=OrderStatus.SUBMITTED)
    event = _make_fill_event(
        event_type="pending_cancel", status="pending_cancel", filled_qty="0", avg_fill_price="0"
    )

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=_NOW)

    assert result.new_status == OrderStatus.PENDING_CANCEL
    assert result.status_transitioned is True


# ---------------------------------------------------------------------------
# E-01: stream_received_at defaults to now when not provided
# ---------------------------------------------------------------------------


def test_process_event_defaults_received_at_to_now() -> None:
    processor = _make_processor()
    tracked = _make_tracked()
    event = _make_fill_event(status="filled", filled_qty="100")

    result = processor.process_event(event, tracked_order=tracked, stream_received_at=None)

    # Should succeed (not raise) even when received_at is omitted
    assert result.fill is not None
    assert "stream_received_at" in (result.fill.metadata or {})
