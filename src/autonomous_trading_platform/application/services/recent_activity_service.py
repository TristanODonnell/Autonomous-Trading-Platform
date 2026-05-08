from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.repositories.queries.recent_activity_repository import (
    RecentActivityRepository,
)


class RecentActivityService:
    def __init__(
        self,
        session: Session,
        repository: RecentActivityRepository | None = None,
    ) -> None:
        self._repository = repository or RecentActivityRepository(session)

    def list_recent_activity(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 100)

        return [
            {
                "event_type": item.event_type,
                "description": item.description,
                "timestamp": item.timestamp,
                "severity": item.severity,
            }
            for item in self._repository.list_recent_activity(limit=safe_limit)
        ]
