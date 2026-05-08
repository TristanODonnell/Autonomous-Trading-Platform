from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from tests.conftest import auth_headers


def test_trading_mode_requires_operator_or_admin_role(client: TestClient) -> None:
    response = client.put(
        "/api/v1/system/trading-mode",
        json={"mode": "simulation"},
        headers=auth_headers(role="researcher"),
    )

    assert response.status_code == 403


def test_trading_mode_updates_runtime_state_and_audits(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.put(
        "/api/v1/system/trading-mode",
        json={"mode": "simulation", "rationale": "research run"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["previous_mode"] == "paper"
    assert data["mode"] == "simulation"
    assert data["rationale"] == "research run"
    assert data["updated_by"] == "test-user"

    control_state = db_session.get(RuntimeControlState, "global")
    audit_log = db_session.query(AuditLogRow).one()
    assert control_state is not None
    assert control_state.trading_mode == "simulation"
    assert audit_log.event_type == "TRADING_MODE_CHANGED"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["previous_mode"] == "paper"
    assert audit_log.event_metadata["mode"] == "simulation"


def test_trading_mode_rejects_direct_simulation_to_live_transition(
    client: TestClient,
    db_session: Session,
) -> None:
    db_session.add(
        RuntimeControlState(
            control_id="global",
            trading_enabled=True,
            trading_paused=False,
            kill_switch_enabled=False,
            trading_mode="simulation",
            reason=None,
            updated_by=None,
        )
    )
    db_session.flush()

    response = client.put(
        "/api/v1/system/trading-mode",
        json={"mode": "live"},
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 409
    assert "Invalid trading mode transition" in response.json()["detail"]
