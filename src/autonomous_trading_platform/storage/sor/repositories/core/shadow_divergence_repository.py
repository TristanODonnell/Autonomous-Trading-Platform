from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.shadow_divergences import ShadowDivergenceRow


class ShadowDivergenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: ShadowDivergenceRow) -> None:
        self._session.add(row)
        self._session.flush()

    def insert_many(self, rows: list[ShadowDivergenceRow]) -> None:
        self._session.add_all(rows)
        self._session.flush()

    def list_for_run(
        self,
        shadow_run_id: str,
        *,
        category: str | None = None,
        exceeds_threshold_only: bool = False,
        limit: int = 500,
    ) -> list[ShadowDivergenceRow]:
        q = select(ShadowDivergenceRow).where(ShadowDivergenceRow.shadow_run_id == shadow_run_id)
        if category is not None:
            q = q.where(ShadowDivergenceRow.category == category)
        if exceeds_threshold_only:
            q = q.where(ShadowDivergenceRow.exceeds_threshold.is_(True))
        q = q.order_by(ShadowDivergenceRow.timestamp.asc()).limit(limit)
        return list(self._session.scalars(q).all())

    def count_exceedances(self, shadow_run_id: str) -> int:
        rows = self.list_for_run(shadow_run_id, exceeds_threshold_only=True, limit=10000)
        return len(rows)
