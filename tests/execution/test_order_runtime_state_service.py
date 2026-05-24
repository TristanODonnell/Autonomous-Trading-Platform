"""F-05 — Monotonic fill quantity protection in OrderRuntimeStateService."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import (
    OrderEvent,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.trading.broker_order import BrokerOrder
from autonomous_trading_platform.execution.services.order_reconciliation_service import (
    ReconciliationResult,
)
from autonomous_trading_platform.execution.services.order_runtime_state_service import (
    OrderRuntimeStateService,
)
from autonomous_trading_platform.storage.sor.models.tracked_orders import TrackedOrder
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

_INTENT_ID = UUID("00000000-0000-0000-0000-000000000020")
_ORDER_ID = UUID("00000000-0000-0000-0000-000000000021")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000022")
_NOW = datetime(2025, 1, 1, 15, 0, tzinfo=UTC)


def _make_broker_order(
    *,
    filled_qty: str = "0",
    avg_fill_price: str | None = None,
    status: OrderStatus = OrderStatus.SUBMITTED,
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id="bo-001",
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
        updated_at=_NOW,
        filled_qty=Decimal(filled_qty),
        avg_fill_price=Decimal(avg_fill_price) if avg_fill_price is not None else None,
    )


def _make_tracked_order(*, previous_filled_qty: str = "0") -> TrackedOrder:
    return TrackedOrder(
        order_id=_ORDER_ID,
        intent_id=_INTENT_ID,
        run_id=_RUN_ID,
        strategy_id="strat-alpha",
        account_id="acct-001",
        symbol="AAPL",
        broker_order_id="bo-001",
        current_status=OrderStatus.PARTIALLY_FILLED,
        previous_filled_qty=Decimal(previous_filled_qty),
        previous_avg_fill_price=None,
        is_open=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_reconciliation_result(broker_order: BrokerOrder) -> ReconciliationResult:
    return ReconciliationResult(
        order_id=_ORDER_ID,
        broker_order=broker_order,
        next_status=broker_order.status,
        event_applied=None,
        fill=None,
    )


class FakeTrackedOrdersRepo:
    def __init__(self, row: TrackedOrder) -> None:
        self._row = row

    def get_by_order_id(self, order_id) -> TrackedOrder | None:
        if self._row.order_id == order_id:
            return self._row
        return None

    def upsert(self, row: TrackedOrder) -> TrackedOrder:
        self._row = row
        return row


class FakeUnitOfWork:
    def __init__(self, tracked_order: TrackedOrder) -> None:
        self.tracked_orders = FakeTrackedOrdersRepo(tracked_order)


class TestMonotonicFillQuantityProtection:
    def test_forward_progress_updates_qty(self) -> None:
        svc = OrderRuntimeStateService()
        tracked = _make_tracked_order(previous_filled_qty="50")
        uow = FakeUnitOfWork(tracked)

        broker_order = _make_broker_order(filled_qty="75", avg_fill_price="100.00")
        result = _make_reconciliation_result(broker_order)

        svc.apply_reconciliation_result(uow=cast(SorUnitOfWork, uow), result=result, now_utc=_NOW)

        assert uow.tracked_orders._row.previous_filled_qty == Decimal("75")

    def test_stale_regression_does_not_rewind_qty(self) -> None:
        svc = OrderRuntimeStateService()
        tracked = _make_tracked_order(previous_filled_qty="100")
        uow = FakeUnitOfWork(tracked)

        # Broker reports stale lower qty.
        broker_order = _make_broker_order(filled_qty="50", avg_fill_price="100.00")
        result = _make_reconciliation_result(broker_order)

        svc.apply_reconciliation_result(uow=cast(SorUnitOfWork, uow), result=result, now_utc=_NOW)

        # Monotonic guard must preserve the higher previously-tracked quantity.
        assert uow.tracked_orders._row.previous_filled_qty == Decimal("100")

    def test_stale_regression_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        svc = OrderRuntimeStateService()
        tracked = _make_tracked_order(previous_filled_qty="100")
        uow = FakeUnitOfWork(tracked)

        broker_order = _make_broker_order(filled_qty="50", avg_fill_price="100.00")
        result = _make_reconciliation_result(broker_order)

        with caplog.at_level(logging.WARNING):
            svc.apply_reconciliation_result(
                uow=cast(SorUnitOfWork, uow), result=result, now_utc=_NOW
            )

        assert any("monotonic_qty_guard_triggered" in r.message for r in caplog.records)

    def test_equal_qty_does_not_trigger_monotonic_guard(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = OrderRuntimeStateService()
        tracked = _make_tracked_order(previous_filled_qty="50")
        uow = FakeUnitOfWork(tracked)

        broker_order = _make_broker_order(filled_qty="50", avg_fill_price="100.00")
        result = _make_reconciliation_result(broker_order)

        with caplog.at_level(logging.WARNING):
            svc.apply_reconciliation_result(
                uow=cast(SorUnitOfWork, uow), result=result, now_utc=_NOW
            )

        assert not any("monotonic_qty_guard_triggered" in r.message for r in caplog.records)
        assert uow.tracked_orders._row.previous_filled_qty == Decimal("50")

    def test_zero_to_positive_progression_is_normal(self) -> None:
        svc = OrderRuntimeStateService()
        tracked = _make_tracked_order(previous_filled_qty="0")
        uow = FakeUnitOfWork(tracked)

        broker_order = _make_broker_order(filled_qty="25", avg_fill_price="100.00")
        result = _make_reconciliation_result(broker_order)

        svc.apply_reconciliation_result(uow=cast(SorUnitOfWork, uow), result=result, now_utc=_NOW)

        assert uow.tracked_orders._row.previous_filled_qty == Decimal("25")


class TestStatusRegressionDetection:
    """Status regression is logged by order_reconciliation_service; test integration."""

    def test_status_update_recorded_correctly(self) -> None:
        svc = OrderRuntimeStateService()
        tracked = _make_tracked_order(previous_filled_qty="50")
        uow = FakeUnitOfWork(tracked)

        broker_order = _make_broker_order(
            filled_qty="100", avg_fill_price="100.00", status=OrderStatus.FILLED
        )
        result = ReconciliationResult(
            order_id=_ORDER_ID,
            broker_order=broker_order,
            next_status=OrderStatus.FILLED,
            event_applied=None,
            fill=None,
        )

        svc.apply_reconciliation_result(uow=cast(SorUnitOfWork, uow), result=result, now_utc=_NOW)

        row = uow.tracked_orders._row
        assert row.current_status == OrderStatus.FILLED
        assert row.is_open is False
        assert row.previous_filled_qty == Decimal("100")


class TestBrokerOrderNotFoundRuntimeUpdate:
    def test_missing_broker_order_updates_status_without_rewinding_state(self) -> None:
        svc = OrderRuntimeStateService()
        tracked = _make_tracked_order(previous_filled_qty="50")
        tracked.previous_avg_fill_price = Decimal("100.00")
        uow = FakeUnitOfWork(tracked)
        result = ReconciliationResult(
            order_id=_ORDER_ID,
            broker_order=None,
            next_status=OrderStatus.EXPIRED,
            event_applied=OrderEvent.EXPIRE,
            fill=None,
            broker_not_found=True,
        )

        svc.apply_reconciliation_result(uow=cast(SorUnitOfWork, uow), result=result, now_utc=_NOW)

        row = uow.tracked_orders._row
        assert row.current_status == OrderStatus.EXPIRED
        assert row.is_open is False
        assert row.broker_order_id == "bo-001"
        assert row.previous_filled_qty == Decimal("50")
        assert row.previous_avg_fill_price == Decimal("100.00")
