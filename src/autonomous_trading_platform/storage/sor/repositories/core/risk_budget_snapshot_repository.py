from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.risk_budget_snapshots import (
    RiskBudgetSnapshotRow,
)


class RiskBudgetSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: RiskBudgetSnapshotRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_latest(self, *, mode: str | None = None) -> RiskBudgetSnapshotRow | None:
        q = select(RiskBudgetSnapshotRow)
        if mode is not None:
            q = q.where(RiskBudgetSnapshotRow.mode == mode)
        q = q.order_by(RiskBudgetSnapshotRow.computed_at.desc()).limit(1)
        return cast(RiskBudgetSnapshotRow | None, self._session.scalars(q).first())

    def get_history(
        self,
        *,
        mode: str | None = None,
        since: datetime,
        limit: int = 100,
    ) -> list[RiskBudgetSnapshotRow]:
        q = select(RiskBudgetSnapshotRow).where(RiskBudgetSnapshotRow.computed_at >= since)
        if mode is not None:
            q = q.where(RiskBudgetSnapshotRow.mode == mode)
        q = q.order_by(RiskBudgetSnapshotRow.computed_at.desc()).limit(limit)
        return list(self._session.scalars(q).all())

    def get_for_run(self, run_id: str) -> list[RiskBudgetSnapshotRow]:
        return list(
            self._session.scalars(
                select(RiskBudgetSnapshotRow)
                .where(RiskBudgetSnapshotRow.run_id == run_id)
                .order_by(RiskBudgetSnapshotRow.computed_at.asc())
            ).all()
        )

    def get_by_covariance_snapshot(
        self, covariance_snapshot_id: str
    ) -> list[RiskBudgetSnapshotRow]:
        return list(
            self._session.scalars(
                select(RiskBudgetSnapshotRow)
                .where(RiskBudgetSnapshotRow.covariance_snapshot_id == covariance_snapshot_id)
                .order_by(RiskBudgetSnapshotRow.computed_at.desc())
            ).all()
        )
