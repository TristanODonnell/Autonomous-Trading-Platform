from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from tests.conftest import auth_headers


def test_recent_activity_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/activity/recent")

    assert response.status_code == 401


def test_recent_activity_returns_feed_schema(
    client: TestClient,
    db_session: Session,
) -> None:
    db_session.add(
        AuditLogRow(
            event_id="activity-audit-1",
            run_id=None,
            event_type="operator_action",
            component="controls",
            event_timestamp=datetime.now(UTC),
            message="Operator action recorded",
            event_metadata={"severity": "warning"},
        )
    )
    db_session.flush()

    response = client.get("/api/v1/activity/recent?limit=1", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert "data" in body
    assert "meta" in body

    items = body["data"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["event_type"] == "operator_action"
    assert item["description"] == "Operator action recorded"
    assert isinstance(item["timestamp"], str)
    assert item["severity"] == "warning"


def test_recent_activity_rejects_invalid_limit(client: TestClient) -> None:
    response = client.get("/api/v1/activity/recent?limit=0", headers=auth_headers())

    assert response.status_code == 422
