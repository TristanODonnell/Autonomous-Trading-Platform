from datetime import datetime
from uuid import uuid4

from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository):
    def add(self, audit_log: AuditLogEvent) -> None:
        """Insert an audit log event into the database."""

        row = AuditLogRow(
            event_id=audit_log.event_id,
            run_id=audit_log.run_id,
            event_type=audit_log.event_type,
            component=audit_log.component,
            event_timestamp=audit_log.event_timestamp,
            message=audit_log.message,
            event_metadata=audit_log.metadata,
        )

        self.session.add(row)

    def list_by_run_id(self, run_id):
        return (
            self.session.query(AuditLogRow)
            .filter(AuditLogRow.run_id == run_id)
            .order_by(AuditLogRow.event_timestamp.asc())
            .all()
        )

    def record_operator_action(
        self,
        *,
        action: str,
        actor: str,
        reason: str,
        occurred_at: datetime,
        metadata: dict,
    ) -> None:
        self.add(
            AuditLogEvent(
                event_id=str(uuid4()),
                run_id=None,
                event_type=action,
                component="controls",
                event_timestamp=occurred_at,
                message=f"{action} by {actor}: {reason}",
                metadata={
                    **metadata,
                    "actor": actor,
                    "reason": reason,
                },
            )
        )
