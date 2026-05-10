from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import OrderSource, OrderStatus
from autonomous_trading_platform.execution.errors import BrokerRuntimeSyncError
from autonomous_trading_platform.execution.services.broker_runtime_sync_service import (
    BrokerRuntimeSyncService,
)
from autonomous_trading_platform.storage.sor.models.broker_account_snapshots import (
    BrokerAccountSnapshot,
)
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot

ACCOUNT_PAYLOAD = {
    "id": "paper-account-1",
    "status": "ACTIVE",
    "currency": "USD",
    "cash": "1000.25",
    "buying_power": "2000.50",
    "equity": "3000.75",
    "portfolio_value": "3000.75",
}


class FakeBrokerClient:
    def __init__(
        self,
        account: dict = ACCOUNT_PAYLOAD,
        *,
        positions: list[dict] | None = None,
        open_orders: list[dict] | None = None,
        orders: dict[str, dict] | None = None,
    ) -> None:
        self.account = dict(account)
        self.positions = positions if positions is not None else []
        self.open_orders = open_orders if open_orders is not None else []
        self.orders = orders if orders is not None else {}

    def get_account(self) -> dict:
        if isinstance(self.account, Exception):
            raise self.account
        return dict(self.account)

    def get_positions(self) -> list[dict]:
        return list(self.positions)

    def list_open_orders(self) -> list[dict]:
        return list(self.open_orders)

    def get_order_by_id(self, order_id: str) -> dict:
        return dict(self.orders[order_id])


def _settings():
    return cast(
        Settings,
        SimpleNamespace(trading_environment=TradingEnvironment.PAPER),
    )


def _service(db_session: Session, account: dict = ACCOUNT_PAYLOAD) -> BrokerRuntimeSyncService:
    return BrokerRuntimeSyncService(
        session=db_session,
        broker_client=FakeBrokerClient(account),
        settings=_settings(),
    )


def _order_payload(**overrides) -> dict:
    payload = {
        "id": "broker-order-1",
        "client_order_id": "client-order-1",
        "symbol": "AAPL",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "qty": "1",
        "limit_price": "0.01",
        "status": "accepted",
        "filled_qty": "0",
        "filled_avg_price": None,
        "submitted_at": "2026-05-10T12:00:00Z",
        "updated_at": "2026-05-10T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_sync_account_snapshot_persists_broker_account_state(db_session: Session) -> None:
    observed_at = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    snapshot = _service(db_session).sync_account_snapshot(observed_at=observed_at)

    persisted = db_session.get(BrokerAccountSnapshot, snapshot.snapshot_id)

    assert persisted is not None
    assert persisted.broker == "alpaca"
    assert persisted.trading_environment == "paper"
    assert persisted.broker_account_id == "paper-account-1"
    assert persisted.account_status == "ACTIVE"
    assert persisted.cash == Decimal("1000.25")
    assert persisted.buying_power == Decimal("2000.50")
    assert persisted.equity == Decimal("3000.75")
    assert persisted.portfolio_value == Decimal("3000.75")
    assert persisted.observed_at.tzinfo is not None


def test_sync_cash_snapshot_from_account_persists_cash_runtime_state(
    db_session: Session,
) -> None:
    run_id = uuid4()
    observed_at = datetime(2026, 5, 10, 12, 5, tzinfo=UTC)
    snapshot = _service(db_session).sync_cash_snapshot_from_account(
        run_id=run_id,
        observed_at=observed_at,
        capital_bucket=Decimal("3000.75"),
    )

    persisted = db_session.get(CashSnapshot, snapshot.snapshot_id)

    assert persisted is not None
    assert persisted.run_id == run_id
    assert persisted.timestamp.tzinfo is not None
    assert persisted.currency == "USD"
    assert persisted.cash == Decimal("1000.25")
    assert persisted.buying_power == Decimal("2000.50")
    assert persisted.reserved_cash == Decimal("0")
    assert persisted.equity == Decimal("3000.75")
    assert persisted.source == OrderSource.BROKER_RECONCILED
    assert persisted.capital_bucket == Decimal("3000.75")


def test_sync_broker_runtime_state_persists_account_and_cash_snapshots(
    db_session: Session,
) -> None:
    run_id = uuid4()
    result = _service(db_session).sync_broker_runtime_state(run_id=run_id)

    account_snapshot = db_session.get(BrokerAccountSnapshot, result.account_snapshot_id)
    cash_snapshot = db_session.get(CashSnapshot, result.cash_snapshot_id)

    assert result.broker_account_id == "paper-account-1"
    assert account_snapshot is not None
    assert cash_snapshot is not None
    assert cash_snapshot.run_id == run_id
    assert cash_snapshot.cash == account_snapshot.cash
    assert cash_snapshot.buying_power == account_snapshot.buying_power
    assert cash_snapshot.equity == account_snapshot.equity


def test_validate_broker_runtime_consistency_succeeds_after_sync(
    db_session: Session,
) -> None:
    service = _service(db_session)
    sync_result = service.sync_broker_runtime_state(run_id=uuid4())

    result = service.validate_broker_runtime_consistency()

    assert result.ok is True
    assert result.mismatches == []
    assert result.account_snapshot_id == sync_result.account_snapshot_id
    assert result.cash_snapshot_id == sync_result.cash_snapshot_id


def test_validate_broker_runtime_consistency_reports_decimal_mismatch(
    db_session: Session,
) -> None:
    service = _service(db_session)
    service.sync_broker_runtime_state(run_id=uuid4())
    mismatched_account = dict(ACCOUNT_PAYLOAD)
    mismatched_account["cash"] = "999.00"
    service.broker_client.account = mismatched_account  # type: ignore[attr-defined]

    result = service.validate_broker_runtime_consistency()

    assert result.ok is False
    assert "account.cash" in result.mismatches
    assert "cash.cash" in result.mismatches


def test_validate_broker_runtime_consistency_reports_missing_persisted_state(
    db_session: Session,
) -> None:
    result = _service(db_session).validate_broker_runtime_consistency()

    assert result.ok is False
    assert "missing_broker_account_snapshot" in result.mismatches
    assert "missing_cash_snapshot" in result.mismatches


def test_sync_account_snapshot_rejects_missing_account_id(db_session: Session) -> None:
    payload = dict(ACCOUNT_PAYLOAD)
    payload.pop("id")

    with pytest.raises(ValueError, match="missing account id"):
        _service(db_session, payload).sync_account_snapshot()


def test_sync_positions_from_broker_persists_position_snapshot(db_session: Session) -> None:
    run_id = uuid4()
    broker_client = FakeBrokerClient(
        positions=[
            {
                "symbol": "AAPL",
                "qty": "2",
                "avg_entry_price": "100.00",
                "current_price": "101.00",
                "market_value": "202.00",
                "unrealized_pl": "2.00",
            }
        ]
    )
    service = BrokerRuntimeSyncService(
        session=db_session,
        broker_client=broker_client,
        settings=_settings(),
    )

    snapshot = service.sync_positions_from_broker(run_id=run_id)
    persisted = db_session.get(PositionSnapshot, snapshot.snapshot_id)

    assert persisted is not None
    assert persisted.run_id == run_id
    assert persisted.source == OrderSource.BROKER_RECONCILED
    assert len(persisted.positions) == 1
    assert persisted.positions[0].symbol == "AAPL"
    assert persisted.positions[0].quantity == Decimal("2")
    assert persisted.positions[0].market_value == Decimal("202.00")


def test_sync_open_orders_from_broker_persists_broker_orders(db_session: Session) -> None:
    run_id = uuid4()
    broker_client = FakeBrokerClient(open_orders=[_order_payload()])
    service = BrokerRuntimeSyncService(
        session=db_session,
        broker_client=broker_client,
        settings=_settings(),
    )

    broker_order_ids = service.sync_open_orders_from_broker(
        run_id=run_id,
        account_id="paper-account-1",
    )
    persisted = db_session.get(BrokerOrder, "broker-order-1")

    assert broker_order_ids == ("broker-order-1",)
    assert persisted is not None
    assert persisted.run_id == run_id
    assert persisted.account_id == "paper-account-1"
    assert persisted.status == OrderStatus.SUBMITTED
    assert persisted.raw_broker_payload["id"] == "broker-order-1"


def test_sync_order_status_persists_latest_broker_order_state(db_session: Session) -> None:
    run_id = uuid4()
    broker_client = FakeBrokerClient(orders={"broker-order-1": _order_payload(status="canceled")})
    service = BrokerRuntimeSyncService(
        session=db_session,
        broker_client=broker_client,
        settings=_settings(),
    )

    broker_order_id = service.sync_order_status(
        order_id="broker-order-1",
        run_id=run_id,
        account_id="paper-account-1",
    )
    persisted = db_session.get(BrokerOrder, broker_order_id)

    assert persisted is not None
    assert persisted.status == OrderStatus.CANCELED


def test_reconcile_order_fills_persists_incremental_fill(db_session: Session) -> None:
    run_id = uuid4()
    broker_client = FakeBrokerClient(
        orders={
            "broker-order-1": _order_payload(
                status="filled",
                filled_qty="1",
                filled_avg_price="100.00",
                updated_at="2026-05-10T12:01:00Z",
            )
        }
    )
    service = BrokerRuntimeSyncService(
        session=db_session,
        broker_client=broker_client,
        settings=_settings(),
    )

    fill_ids = service.reconcile_order_fills(
        order_id="broker-order-1",
        run_id=run_id,
        account_id="paper-account-1",
    )
    persisted_order = db_session.get(BrokerOrder, "broker-order-1")
    persisted_fill = db_session.get(Fill, fill_ids[0])

    assert len(fill_ids) == 1
    assert persisted_order is not None
    assert persisted_order.status == OrderStatus.FILLED
    assert persisted_order.filled_qty == Decimal("1")
    assert persisted_fill is not None
    assert persisted_fill.broker_order_id == "broker-order-1"
    assert persisted_fill.quantity == Decimal("1")
    assert persisted_fill.price == Decimal("100.00")


def test_reconcile_order_fills_is_idempotent_without_new_fill_delta(
    db_session: Session,
) -> None:
    run_id = uuid4()
    broker_client = FakeBrokerClient(
        orders={
            "broker-order-1": _order_payload(
                status="filled",
                filled_qty="1",
                filled_avg_price="100.00",
                updated_at="2026-05-10T12:01:00Z",
            )
        }
    )
    service = BrokerRuntimeSyncService(
        session=db_session,
        broker_client=broker_client,
        settings=_settings(),
    )
    service.reconcile_order_fills(
        order_id="broker-order-1",
        run_id=run_id,
        account_id="paper-account-1",
    )

    fill_ids = service.reconcile_order_fills(
        order_id="broker-order-1",
        run_id=run_id,
        account_id="paper-account-1",
    )

    assert fill_ids == ()


def test_broker_fetch_failure_raises_deterministic_sync_error(db_session: Session) -> None:
    broker_client = FakeBrokerClient()
    broker_client.account = RuntimeError("broker unavailable")  # type: ignore[assignment]
    service = BrokerRuntimeSyncService(
        session=db_session,
        broker_client=broker_client,
        settings=_settings(),
    )

    with pytest.raises(BrokerRuntimeSyncError, match="Broker account fetch failed"):
        service.sync_account_snapshot()
