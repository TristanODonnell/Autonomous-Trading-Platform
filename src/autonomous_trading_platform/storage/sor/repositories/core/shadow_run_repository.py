from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.shadow_runs import ShadowRunRow


class ShadowRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: ShadowRunRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get(self, shadow_run_id: str) -> ShadowRunRow | None:
        return self._session.get(ShadowRunRow, shadow_run_id)  # type: ignore[no-any-return]

    def update(self, row: ShadowRunRow) -> None:
        self._session.flush()

    def list_for_strategy(
        self,
        strategy_id: str,
        *,
        status: str | None = None,
        validation_status: str | None = None,
        shadow_mode_type: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[ShadowRunRow]:
        q = select(ShadowRunRow).where(ShadowRunRow.strategy_id == strategy_id)
        if status is not None:
            q = q.where(ShadowRunRow.status == status)
        if validation_status is not None:
            q = q.where(ShadowRunRow.validation_status == validation_status)
        if shadow_mode_type is not None:
            q = q.where(ShadowRunRow.shadow_mode_type == shadow_mode_type)
        if since is not None:
            q = q.where(ShadowRunRow.started_at >= since)
        q = q.order_by(ShadowRunRow.started_at.desc()).limit(limit)
        return list(self._session.scalars(q).all())

    def list_all(
        self,
        *,
        status: str | None = None,
        validation_status: str | None = None,
        limit: int = 100,
    ) -> list[ShadowRunRow]:
        q = select(ShadowRunRow)
        if status is not None:
            q = q.where(ShadowRunRow.status == status)
        if validation_status is not None:
            q = q.where(ShadowRunRow.validation_status == validation_status)
        q = q.order_by(ShadowRunRow.started_at.desc()).limit(limit)
        return list(self._session.scalars(q).all())

    def count_passing_cycles(self, strategy_id: str) -> int:
        q = (
            select(ShadowRunRow)
            .where(ShadowRunRow.strategy_id == strategy_id)
            .where(ShadowRunRow.validation_status == "passed")
        )
        return len(list(self._session.scalars(q).all()))
