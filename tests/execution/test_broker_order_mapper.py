"""F-05 — Out-of-order broker update protection tests for BrokerOrderMapper."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.trading.broker_order import BrokerOrder
from autonomous_trading_platform.execution.mappers.broker_order_mapper import BrokerOrderMapper

_INTENT_ID = UUID("00000000-0000-0000-0000-000000000010")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000011")
_NOW = datetime(2025, 1, 1, 15, 0, tzinfo=UTC)


def _make_broker_order(
    *,
    filled_qty: str = "0",
    avg_fill_price: str | None = None,
    status: OrderStatus = OrderStatus.SUBMITTED,
    updated_at: datetime = _NOW,
    broker_order_id: str = "bo-001",
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        client_order_id="co-001",
        intent_id=_INTENT_ID,
        run_id=_RUN_ID,
        broker="alpaca",
        account_id="acct-001",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        extended_hours=False,
        qty=Decimal("100"),
        status=status,
        updated_at=updated_at,
        filled_qty=Decimal(filled_qty),
        avg_fill_price=Decimal(avg_fill_price) if avg_fill_price is not None else None,
    )


@pytest.fixture
def mapper() -> BrokerOrderMapper:
    return BrokerOrderMapper()


# ---------------------------------------------------------------------------
# Normal fill extraction
# ---------------------------------------------------------------------------


def test_normal_fill_returns_fill_and_incremental_qty(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(filled_qty="50", avg_fill_price="100.25")
    result = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("0"),
        previous_avg_fill_price=None,
    )

    assert result.fill is not None
    assert result.fill.quantity == Decimal("50")
    assert result.fill.price == Decimal("100.25")
    assert result.new_filled_qty == Decimal("50")


def test_partial_fill_delta_is_incremental(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(filled_qty="75", avg_fill_price="100.10")
    result = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("50"),
        previous_avg_fill_price=Decimal("100.00"),
    )

    assert result.fill is not None
    assert result.fill.quantity == Decimal("25")
    assert result.new_filled_qty == Decimal("75")


# ---------------------------------------------------------------------------
# F-05: out-of-order / stale broker updates
# ---------------------------------------------------------------------------


def test_quantity_regression_emits_critical_log(
    mapper: BrokerOrderMapper, caplog: pytest.LogCaptureFixture
) -> None:
    order = _make_broker_order(filled_qty="50", avg_fill_price="100.25")

    with caplog.at_level(logging.CRITICAL):
        result = mapper.extract_incremental_fill(
            broker_order=order,
            previous_filled_qty=Decimal("100"),
            previous_avg_fill_price=Decimal("100.00"),
            received_at=_NOW,
        )

    assert result.fill is None
    assert any("quantity_regression_detected" in r.message for r in caplog.records)
    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical_records) >= 1


def test_quantity_regression_preserves_monotonic_filled_qty(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(filled_qty="50")
    result = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("100"),
        previous_avg_fill_price=None,
    )

    # Must not rewind: new_filled_qty should be the previous (higher) value.
    assert result.new_filled_qty == Decimal("100")
    assert result.fill is None


def test_quantity_regression_critical_log_includes_diagnostic_context(
    mapper: BrokerOrderMapper, caplog: pytest.LogCaptureFixture
) -> None:
    order = _make_broker_order(filled_qty="30", avg_fill_price="99.00", broker_order_id="bo-xyz")

    with caplog.at_level(logging.CRITICAL):
        mapper.extract_incremental_fill(
            broker_order=order,
            previous_filled_qty=Decimal("80"),
            previous_avg_fill_price=Decimal("100.00"),
            received_at=_NOW,
        )

    record = next(r for r in caplog.records if "quantity_regression_detected" in r.message)
    assert record.levelno == logging.CRITICAL


def test_duplicate_broker_update_returns_no_fill(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(filled_qty="100", avg_fill_price="100.00")
    result = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("100"),
        previous_avg_fill_price=Decimal("100.00"),
    )

    assert result.fill is None
    assert result.new_filled_qty == Decimal("100")


def test_duplicate_broker_update_emits_debug_log(
    mapper: BrokerOrderMapper, caplog: pytest.LogCaptureFixture
) -> None:
    order = _make_broker_order(filled_qty="100", avg_fill_price="100.00")

    with caplog.at_level(logging.DEBUG):
        mapper.extract_incremental_fill(
            broker_order=order,
            previous_filled_qty=Decimal("100"),
            previous_avg_fill_price=Decimal("100.00"),
            received_at=_NOW,
        )

    assert any("duplicate_broker_update" in r.message for r in caplog.records)


def test_duplicate_update_does_not_duplicate_fill_accounting(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(filled_qty="50", avg_fill_price="100.00")
    first = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("0"),
        previous_avg_fill_price=None,
    )
    # Simulate second reconciliation cycle with same broker state.
    second = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=first.new_filled_qty,
        previous_avg_fill_price=Decimal("100.00"),
    )

    assert first.fill is not None
    assert second.fill is None  # no duplicate fill


# ---------------------------------------------------------------------------
# Snapshot metadata enrichment
# ---------------------------------------------------------------------------


def test_fill_metadata_includes_broker_update_timestamp(mapper: BrokerOrderMapper) -> None:
    update_ts = datetime(2025, 1, 1, 15, 5, tzinfo=UTC)
    order = _make_broker_order(filled_qty="10", avg_fill_price="99.50", updated_at=update_ts)
    result = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("0"),
        previous_avg_fill_price=None,
    )

    assert result.fill is not None
    meta = result.fill.metadata or {}
    assert "broker_update_timestamp" in meta
    assert "2025-01-01T15:05:00" in meta["broker_update_timestamp"]


def test_fill_metadata_includes_received_at_when_provided(mapper: BrokerOrderMapper) -> None:
    received = datetime(2025, 1, 1, 15, 6, tzinfo=UTC)
    order = _make_broker_order(filled_qty="10", avg_fill_price="99.50")
    result = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("0"),
        previous_avg_fill_price=None,
        received_at=received,
    )

    assert result.fill is not None
    meta = result.fill.metadata or {}
    assert "received_at" in meta


def test_fill_metadata_omits_received_at_when_not_provided(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(filled_qty="10", avg_fill_price="99.50")
    result = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("0"),
        previous_avg_fill_price=None,
        received_at=None,
    )

    assert result.fill is not None
    meta = result.fill.metadata or {}
    assert "received_at" not in meta


def test_fill_metadata_includes_previous_state_for_audit(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(filled_qty="75", avg_fill_price="100.50")
    result = mapper.extract_incremental_fill(
        broker_order=order,
        previous_filled_qty=Decimal("50"),
        previous_avg_fill_price=Decimal("100.00"),
    )

    assert result.fill is not None
    meta = result.fill.metadata or {}
    assert meta["previous_filled_qty"] == "50"
    assert meta["previous_avg_fill_price"] == "100.00"


# ---------------------------------------------------------------------------
# Monotonic quantity progression
# ---------------------------------------------------------------------------


def test_filled_qty_only_increases_across_reconciliation_cycles(
    mapper: BrokerOrderMapper,
) -> None:
    orders = [
        _make_broker_order(filled_qty="0"),
        _make_broker_order(filled_qty="25", avg_fill_price="100.00"),
        _make_broker_order(filled_qty="50", avg_fill_price="100.10"),
        _make_broker_order(filled_qty="25", avg_fill_price="100.00"),  # stale
        _make_broker_order(filled_qty="75", avg_fill_price="100.20"),
    ]

    tracked_qty = Decimal("0")
    fills_seen = 0
    for order in orders:
        result = mapper.extract_incremental_fill(
            broker_order=order,
            previous_filled_qty=tracked_qty,
            previous_avg_fill_price=None,
        )
        if result.fill is not None:
            fills_seen += 1
        # tracked_qty must never decrease
        assert result.new_filled_qty >= tracked_qty
        tracked_qty = result.new_filled_qty

    assert tracked_qty == Decimal("75")
    assert fills_seen == 3  # only the three real progress events


# ---------------------------------------------------------------------------
# Status regression detection (via order_reconciliation_service integration)
# ---------------------------------------------------------------------------


def test_to_order_event_returns_none_for_submitted(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(status=OrderStatus.SUBMITTED)
    assert mapper.to_order_event(order) is None


def test_to_order_event_returns_full_fill_for_filled(mapper: BrokerOrderMapper) -> None:
    from autonomous_trading_platform.contracts.common.enums import OrderEvent

    order = _make_broker_order(status=OrderStatus.FILLED, filled_qty="100", avg_fill_price="100")
    assert mapper.to_order_event(order) == OrderEvent.FULL_FILL


def test_to_order_event_returns_cancel_for_canceled(mapper: BrokerOrderMapper) -> None:
    from autonomous_trading_platform.contracts.common.enums import OrderEvent

    order = _make_broker_order(status=OrderStatus.CANCELED)
    assert mapper.to_order_event(order) == OrderEvent.CANCEL


# ---------------------------------------------------------------------------
# E-02: new status mappings and order events
# ---------------------------------------------------------------------------


def test_to_order_event_returns_none_for_pending_new(mapper: BrokerOrderMapper) -> None:
    order = _make_broker_order(status=OrderStatus.PENDING_NEW)
    assert mapper.to_order_event(order) is None


def test_to_order_event_returns_request_cancel_for_pending_cancel(
    mapper: BrokerOrderMapper,
) -> None:
    from autonomous_trading_platform.contracts.common.enums import OrderEvent

    order = _make_broker_order(status=OrderStatus.PENDING_CANCEL)
    assert mapper.to_order_event(order) == OrderEvent.REQUEST_CANCEL


def test_to_order_event_returns_expire_for_expired(mapper: BrokerOrderMapper) -> None:
    from autonomous_trading_platform.contracts.common.enums import OrderEvent

    order = _make_broker_order(status=OrderStatus.EXPIRED)
    assert mapper.to_order_event(order) == OrderEvent.EXPIRE


@pytest.mark.parametrize(
    ("broker_status", "expected"),
    [
        ("pending_cancel", OrderStatus.PENDING_CANCEL),
        ("expired", OrderStatus.EXPIRED),
        ("done_for_day", OrderStatus.EXPIRED),
        ("canceled", OrderStatus.CANCELED),
        ("new", OrderStatus.SUBMITTED),
        ("accepted", OrderStatus.SUBMITTED),
        ("pending_new", OrderStatus.SUBMITTED),
        ("partially_filled", OrderStatus.PARTIALLY_FILLED),
        ("filled", OrderStatus.FILLED),
        ("rejected", OrderStatus.REJECTED),
    ],
)
def test_map_order_status_covers_all_broker_statuses(
    mapper: BrokerOrderMapper,
    broker_status: str,
    expected: OrderStatus,
) -> None:
    payload = {
        "id": "bo-001",
        "client_order_id": "co-001",
        "symbol": "AAPL",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": broker_status,
        "filled_qty": "0",
        "qty": "10",
    }
    from uuid import UUID

    order = mapper.to_broker_order(
        payload=payload,
        intent_id=UUID("00000000-0000-0000-0000-000000000001"),
        run_id=UUID("00000000-0000-0000-0000-000000000002"),
        account_id="acct-001",
    )
    assert order.status == expected


def test_expired_is_distinct_from_canceled(mapper: BrokerOrderMapper) -> None:
    """expired and done_for_day map to EXPIRED, not CANCELED."""
    for broker_status in ("expired", "done_for_day"):
        payload = {
            "id": "bo-001",
            "client_order_id": "co-001",
            "symbol": "AAPL",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "status": broker_status,
            "filled_qty": "0",
            "qty": "10",
        }
        from uuid import UUID

        order = mapper.to_broker_order(
            payload=payload,
            intent_id=UUID("00000000-0000-0000-0000-000000000001"),
            run_id=UUID("00000000-0000-0000-0000-000000000002"),
            account_id="acct-001",
        )
        assert order.status == OrderStatus.EXPIRED, (
            f"Expected EXPIRED for broker status {broker_status!r}"
        )
