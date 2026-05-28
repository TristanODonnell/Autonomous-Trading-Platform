from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.factor_neutralization_runs import (
    FactorNeutralizationRunRow,
)


class FactorNeutralizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: FactorNeutralizationRunRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_by_run_id(self, run_id: str) -> FactorNeutralizationRunRow | None:
        return cast(
            FactorNeutralizationRunRow | None, self._session.get(FactorNeutralizationRunRow, run_id)
        )

    def get_latest(
        self,
        *,
        portfolio_id: str | None = None,
        mode: str | None = None,
    ) -> FactorNeutralizationRunRow | None:
        q = select(FactorNeutralizationRunRow)
        if portfolio_id is not None:
            q = q.where(FactorNeutralizationRunRow.portfolio_id == portfolio_id)
        if mode is not None:
            q = q.where(FactorNeutralizationRunRow.mode == mode)
        q = q.order_by(FactorNeutralizationRunRow.generated_at.desc()).limit(1)
        return cast(FactorNeutralizationRunRow | None, self._session.scalars(q).first())

    def get_history(
        self,
        *,
        since: datetime,
        portfolio_id: str | None = None,
        mode: str | None = None,
        limit: int = 100,
    ) -> list[FactorNeutralizationRunRow]:
        q = select(FactorNeutralizationRunRow).where(
            FactorNeutralizationRunRow.generated_at >= since
        )
        if portfolio_id is not None:
            q = q.where(FactorNeutralizationRunRow.portfolio_id == portfolio_id)
        if mode is not None:
            q = q.where(FactorNeutralizationRunRow.mode == mode)
        q = q.order_by(FactorNeutralizationRunRow.generated_at.desc()).limit(limit)
        return list(self._session.scalars(q).all())
