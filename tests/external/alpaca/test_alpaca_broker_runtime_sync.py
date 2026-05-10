from __future__ import annotations

from collections.abc import Generator
from contextlib import suppress
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.execution.clients.alpaca_broker_client import AlpacaBrokerClient
from autonomous_trading_platform.execution.services.broker_runtime_sync_service import (
    BrokerRuntimeSyncService,
)
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot

pytestmark = [
    pytest.mark.external,
    pytest.mark.alpaca,
    pytest.mark.smoke,
]


def _safe_limit_order_payload(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "qty": "1",
        "side": "buy",
        "type": "limit",
        "limit_price": "0.01",
        "time_in_force": "day",
    }


@pytest.fixture()
def broker_runtime_sync_service(
    db_session: Session,
    alpaca_paper_client: AlpacaBrokerClient,
    alpaca_paper_settings: Settings,
) -> BrokerRuntimeSyncService:
    return BrokerRuntimeSyncService(
        session=db_session,
        broker_client=alpaca_paper_client,
        settings=alpaca_paper_settings,
    )


@pytest.fixture()
def submitted_sync_order(
    alpaca_paper_client: AlpacaBrokerClient,
    alpaca_smoke_symbol: str,
) -> Generator[dict[str, Any], None, None]:
    order = alpaca_paper_client.submit_order(_safe_limit_order_payload(alpaca_smoke_symbol))
    yield order
    order_id = order.get("id")
    if isinstance(order_id, str) and order.get("status") not in {
        "canceled",
        "expired",
        "filled",
        "rejected",
    }:
        with suppress(Exception):
            alpaca_paper_client.cancel_order(order_id)


def test_real_alpaca_cash_snapshot_reconciliation_smoke(
    broker_runtime_sync_service: BrokerRuntimeSyncService,
) -> None:
    result = broker_runtime_sync_service.sync_broker_runtime_state(run_id=uuid4())
    consistency = broker_runtime_sync_service.validate_broker_runtime_consistency()

    assert result.cash_snapshot_id is not None
    assert consistency.ok is True
    assert consistency.mismatches == []


def test_real_alpaca_position_synchronization_smoke(
    db_session: Session,
    broker_runtime_sync_service: BrokerRuntimeSyncService,
) -> None:
    snapshot = broker_runtime_sync_service.sync_positions_from_broker(run_id=uuid4())
    persisted = db_session.get(PositionSnapshot, snapshot.snapshot_id)

    assert persisted is not None
    assert len(persisted.positions) >= 0


def test_real_alpaca_open_order_synchronization_smoke(
    db_session: Session,
    broker_runtime_sync_service: BrokerRuntimeSyncService,
    submitted_sync_order: dict[str, Any],
) -> None:
    account = broker_runtime_sync_service.sync_account_snapshot()
    broker_order_ids = broker_runtime_sync_service.sync_open_orders_from_broker(
        run_id=uuid4(),
        account_id=account.broker_account_id,
    )

    assert submitted_sync_order["id"] in broker_order_ids
    assert db_session.get(BrokerOrder, submitted_sync_order["id"]) is not None


def test_real_alpaca_order_status_persistence_smoke(
    db_session: Session,
    broker_runtime_sync_service: BrokerRuntimeSyncService,
    submitted_sync_order: dict[str, Any],
) -> None:
    account = broker_runtime_sync_service.sync_account_snapshot()
    broker_order_id = broker_runtime_sync_service.sync_order_status(
        order_id=submitted_sync_order["id"],
        run_id=uuid4(),
        account_id=account.broker_account_id,
    )
    persisted = db_session.get(BrokerOrder, broker_order_id)

    assert persisted is not None
    assert persisted.broker_order_id == submitted_sync_order["id"]


def test_real_alpaca_fill_reconciliation_safe_no_fill_smoke(
    db_session: Session,
    broker_runtime_sync_service: BrokerRuntimeSyncService,
    submitted_sync_order: dict[str, Any],
) -> None:
    account = broker_runtime_sync_service.sync_account_snapshot()
    fill_ids = broker_runtime_sync_service.reconcile_order_fills(
        order_id=submitted_sync_order["id"],
        run_id=uuid4(),
        account_id=account.broker_account_id,
    )

    assert isinstance(fill_ids, tuple)
    for fill_id in fill_ids:
        assert db_session.get(Fill, fill_id) is not None
