from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.repositories.cash_snapshot_repository import (
    CashSnapshotRepository,
)


class EquityCurveReader(Protocol):
    def list_since(self, start_timestamp: datetime) -> list[CashSnapshot]: ...


def _resolve_start(period: str) -> datetime:
    now = datetime.now(UTC)

    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "1w":
        return now - timedelta(days=7)
    if period == "1m":
        return now - timedelta(days=30)
    if period == "3m":
        return now - timedelta(days=90)
    if period == "ytd":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    raise ValueError(f"Invalid period: {period}")


class PortfolioEquityCurveService:
    def __init__(
        self,
        session: Session,
        repo: EquityCurveReader | None = None,
    ) -> None:
        self.repo = repo or CashSnapshotRepository(session=session)

    def get_equity_curve(self, period: str) -> dict[str, Any]:
        start = _resolve_start(period)
        snapshots = self.repo.list_since(start)

        points = [
            {
                "timestamp": snapshot.timestamp,
                "value": snapshot.equity,
            }
            for snapshot in snapshots
            if snapshot.equity is not None
        ]

        return {
            "period": period,
            "points": points,
        }
