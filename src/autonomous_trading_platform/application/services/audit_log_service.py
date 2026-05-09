from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)


@dataclass(frozen=True)
class AuditLogEventResult:
    event_id: str
    action_type: str
    description: str
    user: str | None
    timestamp: datetime
    strategy_id: str | None
    rationale: str | None


@dataclass(frozen=True)
class AuditLogListResult:
    events: list[AuditLogEventResult]
    total: int


class AuditLogService:
    def __init__(
        self,
        *,
        session: Session,
        audit_log_repo: AuditLogRepository | None = None,
    ) -> None:
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)

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
    ) -> AuditLogListResult:
        rows, total = self._audit_log_repo.list_events(
            page=page,
            page_size=page_size,
            action_type=action_type,
            strategy_id=strategy_id,
            user_id=user_id,
            from_date=from_date,
            to_date=to_date,
        )

        events = [
            AuditLogEventResult(
                event_id=row.event_id,
                action_type=row.event_type,
                description=row.message,
                user=(row.event_metadata or {}).get("actor"),
                timestamp=row.event_timestamp,
                strategy_id=(row.event_metadata or {}).get("strategy_id"),
                rationale=(row.event_metadata or {}).get("reason"),
            )
            for row in rows
        ]

        return AuditLogListResult(events=events, total=total)
