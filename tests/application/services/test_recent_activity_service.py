from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.recent_activity_service import (
    RecentActivityService,
)
from autonomous_trading_platform.storage.sor.repositories.queries.recent_activity_repository import (
    RecentActivityItem,
    RecentActivityRepository,
)


class FakeRecentActivityRepository:
    def __init__(self, items: list[RecentActivityItem]) -> None:
        self.items = items
        self.limit: int | None = None

    def list_recent_activity(self, *, limit: int = 20) -> list[RecentActivityItem]:
        self.limit = limit
        return self.items


def test_returns_recent_activity_feed_items() -> None:
    timestamp = datetime(2026, 5, 8, 12, 30, tzinfo=UTC)
    repository = FakeRecentActivityRepository(
        items=[
            RecentActivityItem(
                event_type="operator_action",
                description="Operator paused trading",
                timestamp=timestamp,
                severity="warning",
            )
        ]
    )
    service = RecentActivityService(
        session=cast(Session, None),
        repository=cast(RecentActivityRepository, repository),
    )

    result = service.list_recent_activity(limit=200)

    assert result == [
        {
            "event_type": "operator_action",
            "description": "Operator paused trading",
            "timestamp": timestamp,
            "severity": "warning",
        }
    ]
    assert repository.limit == 100
