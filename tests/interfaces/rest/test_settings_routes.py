from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
    AllocationOverrides,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.operator_settings import OperatorSettingsRow
from tests.conftest import auth_headers


def test_get_settings_returns_default_operator_settings(client: TestClient) -> None:
    response = client.get("/api/v1/settings", headers=auth_headers(role="operator"))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "risk_tolerance": "medium",
        "max_drawdown_limit": "0.1",
        "rebalance_frequency": "weekly",
        "auto_promote_enabled": False,
        "per_strategy_cap": "0.25",
    }


def test_put_settings_requires_admin(client: TestClient) -> None:
    response = client.put(
        "/api/v1/settings",
        json={"risk_tolerance": "high", "reason": "operator tried"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin role required."


def test_put_settings_validates_out_of_range_values(client: TestClient) -> None:
    response = client.put(
        "/api/v1/settings",
        json={
            "max_drawdown_limit": "1.5",
            "reason": "bad drawdown",
        },
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 422
    assert "max_drawdown_limit" in response.text


def test_put_settings_persists_changes_and_records_audit_log(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.put(
        "/api/v1/settings",
        json={
            "risk_tolerance": "high",
            "max_drawdown_limit": "0.15",
            "rebalance_frequency": "daily",
            "auto_promote_enabled": True,
            "per_strategy_cap": "0.30",
            "reason": "increase operator risk appetite",
        },
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["risk_tolerance"] == "high"
    assert Decimal(str(data["max_drawdown_limit"])) == Decimal("0.15")
    assert data["rebalance_frequency"] == "daily"
    assert data["auto_promote_enabled"] is True
    assert Decimal(str(data["per_strategy_cap"])) == Decimal("0.3")

    row = db_session.get(OperatorSettingsRow, "default")
    assert row is not None
    assert row.updated_by == "test-user"
    assert row.risk_tolerance == "high"

    audit_log = db_session.query(AuditLogRow).one()
    assert audit_log.event_type == "OPERATOR_SETTINGS_UPDATED"
    assert audit_log.component == "settings"
    assert audit_log.event_metadata is not None
    assert audit_log.event_metadata["actor"] == "test-user"
    assert audit_log.event_metadata["reason"] == "increase operator risk appetite"
    assert audit_log.event_metadata["changes"]["risk_tolerance"] == {
        "previous": "medium",
        "new": "high",
    }


def test_get_settings_returns_consistent_state_after_put(client: TestClient) -> None:
    put_response = client.put(
        "/api/v1/settings",
        json={
            "risk_tolerance": "low",
            "rebalance_frequency": "monthly",
            "reason": "reduce risk",
        },
        headers=auth_headers(role="admin"),
    )
    get_response = client.get("/api/v1/settings", headers=auth_headers(role="operator"))

    assert put_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json()["data"] == put_response.json()["data"]


def test_risk_profile_returns_plain_language_summary(client: TestClient) -> None:
    response = client.put(
        "/api/v1/settings",
        json={
            "max_drawdown_limit": "0.10",
            "auto_promote_enabled": False,
            "reason": "profile fixture",
        },
        headers=auth_headers(role="admin"),
    )
    assert response.status_code == 200

    profile_response = client.get(
        "/api/v1/settings/risk-profile",
        headers=auth_headers(role="operator"),
    )

    assert profile_response.status_code == 200
    data = profile_response.json()["data"]
    assert "drawdown exceeds 10.0%" in data["summary"]
    assert "Auto-promotion is off" in data["summary"]
    assert data["bullets"] != []


def test_advanced_settings_returns_overrides_and_read_only_state(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        AllocationOverrides(
            override_id="override-1",
            strategy_id="momentum_v1",
            overridden_by="risk-manager-1",
            override_reason="tighten max drawdown",
            max_pct_of_capital=None,
            max_position_size_usd=None,
            max_drawdown_allowed=0.08,
            is_active=True,
            created_at=now,
            expires_at=None,
        )
    )
    db_session.flush()

    operator_response = client.get(
        "/api/v1/settings/advanced",
        headers=auth_headers(role="operator"),
    )
    admin_response = client.get(
        "/api/v1/settings/advanced",
        headers=auth_headers(role="admin"),
    )

    assert operator_response.status_code == 200
    operator_data = operator_response.json()["data"]
    assert operator_data["read_only"] is True
    assert operator_data["per_strategy_max_drawdown_overrides"] == [
        {
            "strategy_id": "momentum_v1",
            "max_drawdown_allowed": "0.08",
            "updated_by": "risk-manager-1",
            "reason": "tighten max drawdown",
        }
    ]
    assert operator_data["position_size_caps_per_asset"][0]["asset"] == "*"
    assert operator_data["cost_model_configuration"]["slippage_rate"] == "0.0001"

    assert admin_response.status_code == 200
    assert admin_response.json()["data"]["read_only"] is False
