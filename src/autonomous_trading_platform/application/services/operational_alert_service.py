from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.runtime.services.audit_logging_service import AuditLoggingService
from autonomous_trading_platform.storage.sor.models.operational_alerts import OperationalAlert

ALERT_SEVERITIES = {"info", "warning", "critical"}
ALERT_CATEGORIES = {"runtime", "broker", "reconciliation", "risk_governance", "control_infra"}
ALERT_STATUSES = {"active", "acknowledged", "snoozed", "resolved"}


class OperationalAlertService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.audit = AuditLoggingService(session)

    def list_alerts(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[OperationalAlert]:
        stmt = select(OperationalAlert)
        if status is not None:
            stmt = stmt.where(OperationalAlert.status == self._normalize_status(status))
        if severity is not None:
            stmt = stmt.where(OperationalAlert.severity == self._normalize_severity(severity))
        if category is not None:
            stmt = stmt.where(OperationalAlert.category == self._normalize_category(category))

        return list(
            self.session.scalars(
                stmt.order_by(OperationalAlert.updated_at.desc()).limit(limit)
            ).all()
        )

    def create_or_update_alert(
        self,
        *,
        fingerprint: str,
        alert_name: str,
        category: str,
        severity: str,
        source: str,
        environment: str,
        summary: str,
        details: dict,
        recommended_action: str | None = None,
        job_name: str | None = None,
        strategy_id: str | None = None,
    ) -> OperationalAlert:
        now = datetime.now(UTC)
        existing = self._get_by_fingerprint(fingerprint)
        if existing is not None:
            existing.alert_name = alert_name
            existing.category = self._normalize_category(category)
            existing.severity = self._normalize_severity(severity)
            if existing.status == "resolved":
                existing.status = "active"
                existing.resolved_at = None
                existing.resolved_by = None
            existing.source = source
            existing.environment = environment or "unknown"
            existing.job_name = job_name
            existing.strategy_id = strategy_id
            existing.summary = summary
            existing.details = details
            existing.recommended_action = recommended_action
            existing.updated_at = now
            alert = existing
            event_type = "OPERATIONAL_ALERT_UPDATED"
        else:
            alert = OperationalAlert(
                alert_id=str(uuid4()),
                fingerprint=fingerprint,
                alert_name=alert_name,
                category=self._normalize_category(category),
                severity=self._normalize_severity(severity),
                status="active",
                source=source,
                environment=environment or "unknown",
                job_name=job_name,
                strategy_id=strategy_id,
                summary=summary,
                details=details,
                recommended_action=recommended_action,
                created_at=now,
                updated_at=now,
                notes=[],
            )
            self.session.add(alert)
            event_type = "OPERATIONAL_ALERT_CREATED"

        self._audit(event_type, alert, actor="system", note=summary)
        self.session.commit()
        return alert

    def acknowledge_alert(
        self, *, alert_id: str, actor: str, note: str | None = None
    ) -> OperationalAlert:
        alert = self._require_alert(alert_id)
        now = datetime.now(UTC)
        alert.status = "acknowledged"
        alert.acknowledged_at = now
        alert.acknowledged_by = actor
        alert.updated_at = now
        self._append_note(alert, actor=actor, action="acknowledged", note=note, occurred_at=now)
        self._audit("OPERATIONAL_ALERT_ACKNOWLEDGED", alert, actor=actor, note=note)
        self.session.commit()
        return alert

    def resolve_alert(
        self, *, alert_id: str, actor: str, note: str | None = None
    ) -> OperationalAlert:
        alert = self._require_alert(alert_id)
        now = datetime.now(UTC)
        alert.status = "resolved"
        alert.resolved_at = now
        alert.resolved_by = actor
        alert.snoozed_until = None
        alert.updated_at = now
        self._append_note(alert, actor=actor, action="resolved", note=note, occurred_at=now)
        self._audit("OPERATIONAL_ALERT_RESOLVED", alert, actor=actor, note=note)
        self.session.commit()
        return alert

    def snooze_alert(
        self,
        *,
        alert_id: str,
        actor: str,
        snoozed_until: datetime,
        note: str | None = None,
    ) -> OperationalAlert:
        alert = self._require_alert(alert_id)
        now = datetime.now(UTC)
        if snoozed_until <= now:
            raise ValueError("snoozed_until must be in the future")
        alert.status = "snoozed"
        alert.snoozed_at = now
        alert.snoozed_by = actor
        alert.snoozed_until = snoozed_until
        alert.updated_at = now
        self._append_note(alert, actor=actor, action="snoozed", note=note, occurred_at=now)
        self._audit("OPERATIONAL_ALERT_SNOOZED", alert, actor=actor, note=note)
        self.session.commit()
        return alert

    def unsnooze_alert(
        self,
        *,
        alert_id: str,
        actor: str,
        note: str | None = None,
    ) -> OperationalAlert:
        alert = self._require_alert(alert_id)
        now = datetime.now(UTC)
        alert.status = "acknowledged" if alert.acknowledged_at is not None else "active"
        alert.snoozed_until = None
        alert.updated_at = now
        self._append_note(alert, actor=actor, action="unsnoozed", note=note, occurred_at=now)
        self._audit("OPERATIONAL_ALERT_UNSNOOZED", alert, actor=actor, note=note)
        self.session.commit()
        return alert

    def add_note(self, *, alert_id: str, actor: str, note: str) -> OperationalAlert:
        alert = self._require_alert(alert_id)
        now = datetime.now(UTC)
        alert.updated_at = now
        self._append_note(alert, actor=actor, action="noted", note=note, occurred_at=now)
        self._audit("OPERATIONAL_ALERT_NOTE_ADDED", alert, actor=actor, note=note)
        self.session.commit()
        return alert

    def _get_by_fingerprint(self, fingerprint: str) -> OperationalAlert | None:
        return cast(
            OperationalAlert | None,
            self.session.scalar(
                select(OperationalAlert).where(OperationalAlert.fingerprint == fingerprint)
            ),
        )

    def _require_alert(self, alert_id: str) -> OperationalAlert:
        alert = cast(OperationalAlert | None, self.session.get(OperationalAlert, alert_id))
        if alert is None:
            raise LookupError(f"Operational alert not found: {alert_id}")
        return alert

    @staticmethod
    def _append_note(
        alert: OperationalAlert,
        *,
        actor: str,
        action: str,
        note: str | None,
        occurred_at: datetime,
    ) -> None:
        if note is None or not note.strip():
            return
        alert.notes = [
            *(alert.notes or []),
            {
                "actor": actor,
                "action": action,
                "note": note.strip(),
                "occurred_at": occurred_at.isoformat(),
            },
        ]

    def _audit(
        self,
        event_type: str,
        alert: OperationalAlert,
        *,
        actor: str,
        note: str | None,
    ) -> None:
        self.audit.record_event(
            run_id=None,
            event_type=event_type,
            component="operations.alerts",
            message=f"{event_type}: {alert.alert_name}",
            metadata={
                "actor": actor,
                "alert_id": alert.alert_id,
                "fingerprint": alert.fingerprint,
                "alert_name": alert.alert_name,
                "category": alert.category,
                "severity": alert.severity,
                "status": alert.status,
                "environment": alert.environment,
                "job_name": alert.job_name,
                "strategy_id": alert.strategy_id,
                "recommended_action": alert.recommended_action,
                "note": note,
            },
        )

    @staticmethod
    def _normalize_severity(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALERT_SEVERITIES:
            raise ValueError(f"Unsupported alert severity: {value}")
        return normalized

    @staticmethod
    def _normalize_category(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALERT_CATEGORIES:
            raise ValueError(f"Unsupported alert category: {value}")
        return normalized

    @staticmethod
    def _normalize_status(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALERT_STATUSES:
            raise ValueError(f"Unsupported alert status: {value}")
        return normalized
