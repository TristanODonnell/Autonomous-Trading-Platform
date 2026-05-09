from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select

from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository):
    def list_events(
        self,
        *,
        page: int,
        page_size: int,
        action_type: str | None = None,
        strategy_id: str | None = None,
        user_id: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> tuple[list[AuditLogRow], int]:
        stmt = select(AuditLogRow)

        if action_type is not None:
            stmt = stmt.where(AuditLogRow.event_type == action_type)
        if from_date is not None:
            stmt = stmt.where(AuditLogRow.event_timestamp >= from_date)
        if to_date is not None:
            stmt = stmt.where(AuditLogRow.event_timestamp <= to_date)
        if user_id is not None:
            stmt = stmt.where(AuditLogRow.metadata_["actor"].as_string() == user_id)
        if strategy_id is not None:
            stmt = stmt.where(AuditLogRow.metadata_["strategy_id"].as_string() == strategy_id)

        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

        rows = list(
            self.session.scalars(
                stmt.order_by(AuditLogRow.event_timestamp.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )

        return rows, total

    def add(self, audit_log: AuditLogEvent) -> None:
        """Insert an audit log event into the database."""

        row = AuditLogRow(
            event_id=audit_log.event_id,
            run_id=audit_log.run_id,
            event_type=audit_log.event_type,
            component=audit_log.component,
            event_timestamp=audit_log.event_timestamp,
            message=audit_log.message,
            metadata_=audit_log.metadata,
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
        component: str = "controls",
    ) -> None:
        self.add(
            AuditLogEvent(
                event_id=str(uuid4()),
                run_id=None,
                event_type=action,
                component=component,
                event_timestamp=occurred_at,
                message=f"{action} by {actor}: {reason}",
                metadata={
                    **metadata,
                    "actor": actor,
                    "reason": reason,
                },
            )
        )
