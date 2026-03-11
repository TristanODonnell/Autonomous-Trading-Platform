from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow


def insert_audit_log(session: Session, audit_log: AuditLogEvent) -> None:
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

    session.add(row)
    session.commit()
