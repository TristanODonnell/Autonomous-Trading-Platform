from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.black_litterman_research_runs import (
    BlackLittermanResearchRunRow,
)


class BlackLittermanResearchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: BlackLittermanResearchRunRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_by_run_id(self, run_id: str) -> BlackLittermanResearchRunRow | None:
        return cast(
            BlackLittermanResearchRunRow | None,
            self._session.scalars(
                select(BlackLittermanResearchRunRow).where(
                    BlackLittermanResearchRunRow.run_id == run_id
                )
            ).first(),
        )

    def get_history(
        self,
        *,
        since: datetime,
        limit: int = 100,
    ) -> list[BlackLittermanResearchRunRow]:
        return list(
            self._session.scalars(
                select(BlackLittermanResearchRunRow)
                .where(BlackLittermanResearchRunRow.generated_at >= since)
                .order_by(BlackLittermanResearchRunRow.generated_at.desc())
                .limit(limit)
            ).all()
        )
