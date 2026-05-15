from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.operational_alert_service import (
    OperationalAlertService,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow


def test_operational_alert_lifecycle_persists_state_and_audit_events(
    db_session: Session,
) -> None:
    service = OperationalAlertService(db_session)

    alert = service.create_or_update_alert(
        fingerprint="runtime:trading_cycle:failed",
        alert_name="RATPTradingCycleFailure",
        category="runtime",
        severity="critical",
        source="prometheus",
        environment="test",
        job_name="trading_cycle",
        strategy_id=None,
        summary="Trading cycle failed",
        details={"component": "scheduler.run_trading_cycle"},
        recommended_action="Review runtime job failures before resuming trading.",
    )

    acknowledged = service.acknowledge_alert(
        alert_id=alert.alert_id,
        actor="operator-1",
        note="Investigating",
    )
    acknowledged_by = acknowledged.acknowledged_by
    snoozed = service.snooze_alert(
        alert_id=alert.alert_id,
        actor="operator-1",
        snoozed_until=datetime.now(UTC) + timedelta(minutes=30),
        note="Suppressing during recovery",
    )
    snoozed_status = snoozed.status
    unsnoozed = service.unsnooze_alert(
        alert_id=alert.alert_id,
        actor="operator-1",
        note="Recovery complete",
    )
    unsnoozed_status = unsnoozed.status
    resolved = service.resolve_alert(
        alert_id=alert.alert_id,
        actor="operator-1",
        note="Cycle recovered",
    )

    assert acknowledged_by == "operator-1"
    assert snoozed_status == "snoozed"
    assert unsnoozed_status == "acknowledged"
    assert resolved.status == "resolved"
    assert resolved.resolved_by == "operator-1"
    assert [note["action"] for note in resolved.notes] == [
        "acknowledged",
        "snoozed",
        "unsnoozed",
        "resolved",
    ]

    event_types = [
        row.event_type
        for row in db_session.query(AuditLogRow).order_by(AuditLogRow.event_timestamp.asc()).all()
    ]
    assert event_types == [
        "OPERATIONAL_ALERT_CREATED",
        "OPERATIONAL_ALERT_ACKNOWLEDGED",
        "OPERATIONAL_ALERT_SNOOZED",
        "OPERATIONAL_ALERT_UNSNOOZED",
        "OPERATIONAL_ALERT_RESOLVED",
    ]


def test_operational_alert_rejects_invalid_severity(db_session: Session) -> None:
    service = OperationalAlertService(db_session)

    with pytest.raises(ValueError, match="Unsupported alert severity"):
        service.create_or_update_alert(
            fingerprint="bad-severity",
            alert_name="BadSeverity",
            category="runtime",
            severity="page_everyone",
            source="test",
            environment="test",
            summary="bad",
            details={},
        )
