from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.shadow_comparison_snapshots import (
    ShadowComparisonSnapshotRow,
)


class ShadowComparisonSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: ShadowComparisonSnapshotRow) -> None:
        self._session.add(row)
        self._session.flush()

    def insert_many(self, rows: list[ShadowComparisonSnapshotRow]) -> None:
        self._session.add_all(rows)
        self._session.flush()

    def list_for_run(
        self,
        shadow_run_id: str,
        *,
        category: str | None = None,
        limit: int = 1000,
    ) -> list[ShadowComparisonSnapshotRow]:
        q = select(ShadowComparisonSnapshotRow).where(
            ShadowComparisonSnapshotRow.shadow_run_id == shadow_run_id
        )
        if category is not None:
            q = q.where(ShadowComparisonSnapshotRow.comparison_category == category)
        q = q.order_by(ShadowComparisonSnapshotRow.bar_timestamp.asc()).limit(limit)
        return list(self._session.scalars(q).all())
