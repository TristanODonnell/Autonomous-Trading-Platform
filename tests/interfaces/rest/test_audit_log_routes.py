from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from tests.conftest import auth_headers


def _audit_row(
    event_id: str,
    *,
    event_type: str = "STRATEGY_ENABLED",
    component: str = "strategies",
    message: str = "test event",
    timestamp: datetime | None = None,
    metadata: dict | None = None,
) -> AuditLogRow:
    return AuditLogRow(
        event_id=event_id,
        run_id=None,
        event_type=event_type,
        component=component,
        event_timestamp=timestamp or datetime.now(UTC),
        message=message,
        event_metadata=metadata,
    )


def test_audit_log_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/audit-log")

    assert response.status_code == 401


def test_audit_log_requires_operator_or_admin(client: TestClient) -> None:
    response = client.get("/api/v1/audit-log", headers=auth_headers(role="researcher"))

    assert response.status_code == 403


def test_audit_log_returns_empty_list_when_no_events(client: TestClient) -> None:
    response = client.get("/api/v1/audit-log", headers=auth_headers(role="operator"))

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert "data" in body
    assert "meta" in body
    assert body["data"]["events"] == []
    assert body["data"]["pagination"]["total"] == 0
    assert body["data"]["pagination"]["page"] == 1
    assert body["data"]["pagination"]["page_size"] == 20


def test_audit_log_returns_events_with_correct_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        _audit_row(
            "evt-shape-1",
            event_type="STRATEGY_DISABLED",
            message="STRATEGY_DISABLED by operator-1: pausing for maintenance",
            timestamp=now,
            metadata={
                "actor": "operator-1",
                "reason": "pausing for maintenance",
                "strategy_id": "momentum_v1",
            },
        )
    )
    db_session.flush()

    response = client.get("/api/v1/audit-log", headers=auth_headers(role="admin"))

    assert response.status_code == 200
    events = response.json()["data"]["events"]
    assert len(events) == 1
    evt = events[0]
    assert evt["event_id"] == "evt-shape-1"
    assert evt["action_type"] == "STRATEGY_DISABLED"
    assert evt["description"] == "STRATEGY_DISABLED by operator-1: pausing for maintenance"
    assert evt["user"] == "operator-1"
    assert evt["strategy_id"] == "momentum_v1"
    assert evt["rationale"] == "pausing for maintenance"
    assert "timestamp" in evt


def test_audit_log_returns_none_for_missing_metadata_fields(
    client: TestClient,
    db_session: Session,
) -> None:
    db_session.add(_audit_row("evt-no-meta", event_type="SYSTEM_START", message="system started"))
    db_session.flush()

    response = client.get("/api/v1/audit-log", headers=auth_headers(role="operator"))

    assert response.status_code == 200
    evt = response.json()["data"]["events"][0]
    assert evt["user"] is None
    assert evt["strategy_id"] is None
    assert evt["rationale"] is None


def test_audit_log_paginates_results(
    client: TestClient,
    db_session: Session,
) -> None:
    base = datetime.now(UTC)
    for i in range(5):
        db_session.add(_audit_row(f"evt-pg-{i}", timestamp=base + timedelta(seconds=i)))
    db_session.flush()

    page1 = client.get(
        "/api/v1/audit-log",
        params={"page": 1, "page_size": 2},
        headers=auth_headers(role="operator"),
    )
    assert page1.status_code == 200
    data1 = page1.json()["data"]
    assert data1["pagination"]["total"] == 5
    assert data1["pagination"]["page"] == 1
    assert data1["pagination"]["page_size"] == 2
    page1_event_ids = [event["event_id"] for event in data1["events"]]
    assert page1_event_ids == ["evt-pg-4", "evt-pg-3"]

    page2 = client.get(
        "/api/v1/audit-log",
        params={"page": 2, "page_size": 2},
        headers=auth_headers(role="operator"),
    )
    assert page2.status_code == 200
    data2 = page2.json()["data"]
    assert data2["pagination"]["total"] == 5
    assert data2["pagination"]["page"] == 2
    assert data2["pagination"]["page_size"] == 2
    page2_event_ids = [event["event_id"] for event in data2["events"]]
    assert page2_event_ids == ["evt-pg-2", "evt-pg-1"]

    page3 = client.get(
        "/api/v1/audit-log",
        params={"page": 3, "page_size": 2},
        headers=auth_headers(role="operator"),
    )
    assert page3.status_code == 200
    data3 = page3.json()["data"]
    assert data3["pagination"]["total"] == 5
    assert data3["pagination"]["page"] == 3
    assert data3["pagination"]["page_size"] == 2
    page3_event_ids = [event["event_id"] for event in data3["events"]]
    assert page3_event_ids == ["evt-pg-0"]

    paged_event_ids = page1_event_ids + page2_event_ids + page3_event_ids
    assert paged_event_ids == [
        "evt-pg-4",
        "evt-pg-3",
        "evt-pg-2",
        "evt-pg-1",
        "evt-pg-0",
    ]
    assert len(paged_event_ids) == len(set(paged_event_ids))


def test_audit_log_returns_events_newest_first(
    client: TestClient,
    db_session: Session,
) -> None:
    base = datetime.now(UTC)
    db_session.add_all(
        [
            _audit_row("evt-oldest", timestamp=base - timedelta(hours=2)),
            _audit_row("evt-middle", timestamp=base - timedelta(hours=1)),
            _audit_row("evt-newest", timestamp=base),
        ]
    )
    db_session.flush()

    response = client.get("/api/v1/audit-log", headers=auth_headers(role="operator"))
    events = response.json()["data"]["events"]

    assert events[0]["event_id"] == "evt-newest"
    assert events[2]["event_id"] == "evt-oldest"


def test_audit_log_filters_by_action_type(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            _audit_row("evt-enabled", event_type="STRATEGY_ENABLED", timestamp=now),
            _audit_row("evt-disabled", event_type="STRATEGY_DISABLED", timestamp=now),
        ]
    )
    db_session.flush()

    response = client.get(
        "/api/v1/audit-log",
        params={"action_type": "STRATEGY_ENABLED"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 200
    events = response.json()["data"]["events"]
    assert len(events) == 1
    assert events[0]["action_type"] == "STRATEGY_ENABLED"
    assert response.json()["data"]["pagination"]["total"] == 1


def test_audit_log_filters_by_date_range(
    client: TestClient,
    db_session: Session,
) -> None:
    base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    db_session.add_all(
        [
            _audit_row("evt-before", timestamp=base - timedelta(days=5)),
            _audit_row("evt-in-range", timestamp=base),
            _audit_row("evt-after", timestamp=base + timedelta(days=5)),
        ]
    )
    db_session.flush()

    response = client.get(
        "/api/v1/audit-log",
        params={
            "from_date": (base - timedelta(days=1)).isoformat(),
            "to_date": (base + timedelta(days=1)).isoformat(),
        },
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 200
    events = response.json()["data"]["events"]
    assert len(events) == 1
    assert events[0]["event_id"] == "evt-in-range"


def test_audit_log_filters_by_user_id(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            _audit_row("evt-user-a", timestamp=now, metadata={"actor": "user-a", "reason": "test"}),
            _audit_row("evt-user-b", timestamp=now, metadata={"actor": "user-b", "reason": "test"}),
        ]
    )
    db_session.flush()

    response = client.get(
        "/api/v1/audit-log",
        params={"user_id": "user-a"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 200
    events = response.json()["data"]["events"]
    assert len(events) == 1
    assert events[0]["event_id"] == "evt-user-a"
    assert events[0]["user"] == "user-a"


def test_audit_log_filters_by_strategy_id(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            _audit_row(
                "evt-strat-a",
                timestamp=now,
                metadata={"actor": "user-1", "reason": "test", "strategy_id": "strat-a"},
            ),
            _audit_row(
                "evt-strat-b",
                timestamp=now,
                metadata={"actor": "user-1", "reason": "test", "strategy_id": "strat-b"},
            ),
            _audit_row(
                "evt-no-strat",
                timestamp=now,
                metadata={"actor": "user-1", "reason": "test"},
            ),
        ]
    )
    db_session.flush()

    response = client.get(
        "/api/v1/audit-log",
        params={"strategy_id": "strat-a"},
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 200
    events = response.json()["data"]["events"]
    assert len(events) == 1
    assert events[0]["event_id"] == "evt-strat-a"
    assert events[0]["strategy_id"] == "strat-a"
