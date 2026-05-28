from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.optimizer_runs import OptimizerRunRow


class OptimizerRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: OptimizerRunRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_by_run_id(self, run_id: str) -> OptimizerRunRow | None:
        return cast(
            OptimizerRunRow | None,
            self._session.scalars(
                select(OptimizerRunRow).where(OptimizerRunRow.run_id == run_id)
            ).first(),
        )

    def get_latest(
        self,
        *,
        objective: str | None = None,
        dry_run: bool | None = None,
    ) -> OptimizerRunRow | None:
        q = select(OptimizerRunRow)
        if objective is not None:
            q = q.where(OptimizerRunRow.objective == objective)
        if dry_run is not None:
            q = q.where(OptimizerRunRow.dry_run == dry_run)
        q = q.order_by(OptimizerRunRow.generated_at.desc()).limit(1)
        return cast(OptimizerRunRow | None, self._session.scalars(q).first())

    def get_history(
        self,
        *,
        since: datetime,
        objective: str | None = None,
        dry_run: bool | None = None,
        limit: int = 100,
    ) -> list[OptimizerRunRow]:
        q = select(OptimizerRunRow).where(OptimizerRunRow.generated_at >= since)
        if objective is not None:
            q = q.where(OptimizerRunRow.objective == objective)
        if dry_run is not None:
            q = q.where(OptimizerRunRow.dry_run == dry_run)
        q = q.order_by(OptimizerRunRow.generated_at.desc()).limit(limit)
        return list(self._session.scalars(q).all())

    def get_by_covariance_snapshot(self, covariance_snapshot_id: str) -> list[OptimizerRunRow]:
        return list(
            self._session.scalars(
                select(OptimizerRunRow)
                .where(OptimizerRunRow.covariance_snapshot_id == covariance_snapshot_id)
                .order_by(OptimizerRunRow.generated_at.desc())
            ).all()
        )
