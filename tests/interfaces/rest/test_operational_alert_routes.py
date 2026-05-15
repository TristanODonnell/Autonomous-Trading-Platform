from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from tests.conftest import auth_headers


def test_operational_alert_routes_support_ack_snooze_unsnooze_resolve(
    client: TestClient,
    db_session: Session,
) -> None:
    create_response = client.post(
        "/api/v1/operations/alerts",
        headers=auth_headers(role="operator"),
        json={
            "fingerprint": "broker:failure-rate",
            "alert_name": "RATPBrokerApiFailureRateHigh",
            "category": "broker",
            "severity": "critical",
            "source": "prometheus",
            "environment": "test",
            "summary": "Broker failure rate is high",
            "details": {"broker": "alpaca", "endpoint": "orders.submit"},
            "recommended_action": "Pause order submission if failures continue.",
        },
    )

    assert create_response.status_code == 201
    alert = create_response.json()["data"]
    alert_id = alert["alert_id"]
    assert alert["status"] == "active"

    ack_response = client.post(
        f"/api/v1/operations/alerts/{alert_id}/acknowledge",
        headers=auth_headers(role="operator"),
        json={"note": "Investigating broker status"},
    )
    assert ack_response.status_code == 200
    assert ack_response.json()["data"]["status"] == "acknowledged"

    snooze_response = client.post(
        f"/api/v1/operations/alerts/{alert_id}/snooze",
        headers=auth_headers(role="operator"),
        json={
            "snoozed_until": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            "note": "Known broker maintenance window",
        },
    )
    assert snooze_response.status_code == 200
    assert snooze_response.json()["data"]["status"] == "snoozed"

    unsnooze_response = client.post(
        f"/api/v1/operations/alerts/{alert_id}/unsnooze",
        headers=auth_headers(role="operator"),
        json={"note": "Maintenance ended"},
    )
    assert unsnooze_response.status_code == 200
    assert unsnooze_response.json()["data"]["status"] == "acknowledged"

    resolve_response = client.post(
        f"/api/v1/operations/alerts/{alert_id}/resolve",
        headers=auth_headers(role="operator"),
        json={"note": "Broker recovered"},
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["data"]["status"] == "resolved"

    list_response = client.get(
        "/api/v1/operations/alerts?status=resolved",
        headers=auth_headers(role="operator"),
    )
    assert list_response.status_code == 200
    assert len(list_response.json()["data"]["alerts"]) == 1

    event_types = {row.event_type for row in db_session.query(AuditLogRow).all()}
    assert {
        "OPERATIONAL_ALERT_CREATED",
        "OPERATIONAL_ALERT_ACKNOWLEDGED",
        "OPERATIONAL_ALERT_SNOOZED",
        "OPERATIONAL_ALERT_UNSNOOZED",
        "OPERATIONAL_ALERT_RESOLVED",
    }.issubset(event_types)


def test_operational_alert_routes_require_operator_or_admin(client: TestClient) -> None:
    response = client.get("/api/v1/operations/alerts", headers=auth_headers(role="researcher"))

    assert response.status_code == 403
