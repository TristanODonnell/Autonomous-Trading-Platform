from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.portfolio_memberships import (
    PortfolioMembershipRow,
    PortfolioMembershipTransitionRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class PortfolioMembershipRepository(BaseRepository):
    def get(self, strategy_id: str) -> PortfolioMembershipRow | None:
        row: PortfolioMembershipRow | None = self.session.get(PortfolioMembershipRow, strategy_id)
        return row

    def get_all(self) -> list[PortfolioMembershipRow]:
        return list(
            self.session.scalars(
                select(PortfolioMembershipRow).order_by(PortfolioMembershipRow.strategy_id)
            ).all()
        )

    def get_by_statuses(self, statuses: Iterable[str]) -> list[PortfolioMembershipRow]:
        return list(
            self.session.scalars(
                select(PortfolioMembershipRow)
                .where(PortfolioMembershipRow.status.in_(list(statuses)))
                .order_by(PortfolioMembershipRow.strategy_id)
            ).all()
        )

    def save(self, row: PortfolioMembershipRow) -> None:
        existing = self.get(row.strategy_id)
        if existing is None:
            self.session.add(row)
        else:
            existing.status = row.status
            existing.since = row.since
            existing.reason = row.reason
            existing.quality_score = row.quality_score
            existing.updated_by = row.updated_by
            existing.updated_at = row.updated_at
        self.session.flush()

    def insert_transition(self, row: PortfolioMembershipTransitionRow) -> None:
        self.session.add(row)
        self.session.flush()

    def get_transitions(self, strategy_id: str) -> list[PortfolioMembershipTransitionRow]:
        return list(
            self.session.scalars(
                select(PortfolioMembershipTransitionRow)
                .where(PortfolioMembershipTransitionRow.strategy_id == strategy_id)
                .order_by(PortfolioMembershipTransitionRow.created_at)
            ).all()
        )
