from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from tests.conftest import auth_headers


def test_kill_switch_requires_operator_or_admin_role(client: TestClient) -> None:
    response = client.post(
        "/api/v1/controls/kill-switch",
        json={"reason": "emergency halt"},
        headers=auth_headers(role="researcher"),
    )

    assert response.status_code == 403


def test_kill_switch_halts_trading_and_cancels_open_orders(
    client: TestClient,
    db_session: Session,
) -> None:
    intent_id = uuid4()
    run_id = uuid4()
    db_session.add(
        BrokerOrder(
            broker_order_id="route-open-order",
            client_order_id="route-client-open-order",
            intent_id=intent_id,
            run_id=run_id,
            broker="alpaca",
            account_id="paper",
            symbol="MSFT",
            side=Side.SELL,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            qty=Decimal("3"),
            notional=None,
            limit_price=None,
            stop_price=None,
            status=OrderStatus.NEW,
            submitted_at=None,
            updated_at=datetime.now(UTC),
            filled_qty=Decimal("0"),
            avg_fill_price=None,
            last_error=None,
            raw_broker_payload=None,
            requested_qty=Decimal("3"),
        )
    )
    db_session.flush()

    response = client.post(
        "/api/v1/controls/kill-switch",
        json={"reason": "emergency halt"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "halted"
    assert data["kill_switch_active"] is True
    assert data["canceled_order_count"] == 1
    assert data["reason"] == "emergency halt"
    assert data["triggered_by"] == "test-user"

    control_state = db_session.get(RuntimeControlState, "global")
    broker_order = db_session.get(BrokerOrder, "route-open-order")
    audit_log = db_session.query(AuditLogRow).one()
    assert control_state is not None
    assert control_state.kill_switch_enabled is True
    assert control_state.trading_enabled is False
    assert broker_order is not None
    assert broker_order.status == OrderStatus.CANCELED
    assert audit_log.event_type == "KILL_SWITCH_ACTIVATED"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["actor"] == "test-user"
    assert audit_log.event_metadata["reason"] == "emergency halt"


def test_pause_and_resume_require_operator_or_admin_role(client: TestClient) -> None:
    pause_response = client.post(
        "/api/v1/controls/pause",
        json={"rationale": "market data incident"},
        headers=auth_headers(role="researcher"),
    )
    resume_response = client.post(
        "/api/v1/controls/resume",
        json={"rationale": "market data recovered"},
        headers=auth_headers(role="researcher"),
    )

    assert pause_response.status_code == 403
    assert resume_response.status_code == 403


def test_pause_and_resume_update_runtime_control_state(
    client: TestClient,
    db_session: Session,
) -> None:
    pause_response = client.post(
        "/api/v1/controls/pause",
        json={"rationale": "market data incident"},
        headers=auth_headers(role="operator"),
    )
    resume_response = client.post(
        "/api/v1/controls/resume",
        json={"rationale": "market data recovered"},
        headers=auth_headers(role="operator"),
    )

    assert pause_response.status_code == 200
    assert resume_response.status_code == 200
    assert pause_response.json()["data"]["status"] == "paused"
    assert pause_response.json()["data"]["trading_paused"] is True
    assert resume_response.json()["data"]["status"] == "resumed"
    assert resume_response.json()["data"]["trading_paused"] is False

    control_state = db_session.get(RuntimeControlState, "global")
    audit_logs = db_session.query(AuditLogRow).order_by(AuditLogRow.event_timestamp.asc()).all()
    assert control_state is not None
    assert control_state.trading_paused is False
    assert control_state.reason == "market data recovered"
    assert [row.event_type for row in audit_logs] == [
        "TRADING_PAUSED",
        "TRADING_RESUMED",
    ]
    assert audit_logs[0].event_metadata is not None
    assert audit_logs[0].event_metadata["actor"] == "test-user"
    assert audit_logs[0].event_metadata["reason"] == "market data incident"
