from __future__ import annotations

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.rebalance_runs import UniverseRebalanceRun
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class UniverseRebalanceRepository(BaseRepository):
    def insert_run(self, row: UniverseRebalanceRun) -> None:
        self.session.add(row)

    def get_by_run_id(self, run_id: str) -> UniverseRebalanceRun | None:
        stmt = select(UniverseRebalanceRun).where(UniverseRebalanceRun.rebalance_run_id == run_id)
        return cast(UniverseRebalanceRun | None, self.session.execute(stmt).scalar_one_or_none())

    def get_runs_for_candidate(self, candidate_version_id: str) -> list[UniverseRebalanceRun]:
        stmt = (
            select(UniverseRebalanceRun)
            .where(UniverseRebalanceRun.candidate_universe_version_id == candidate_version_id)
            .order_by(UniverseRebalanceRun.created_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_latest_run(self) -> UniverseRebalanceRun | None:
        stmt = (
            select(UniverseRebalanceRun).order_by(UniverseRebalanceRun.created_at.desc()).limit(1)
        )
        return cast(UniverseRebalanceRun | None, self.session.execute(stmt).scalars().first())

    def get_recent(self, limit: int = 20) -> list[UniverseRebalanceRun]:
        stmt = (
            select(UniverseRebalanceRun)
            .order_by(UniverseRebalanceRun.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_runs_for_proposed(self, proposed_version_id: str) -> list[UniverseRebalanceRun]:
        stmt = (
            select(UniverseRebalanceRun)
            .where(UniverseRebalanceRun.proposed_universe_version_id == proposed_version_id)
            .order_by(UniverseRebalanceRun.created_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())
