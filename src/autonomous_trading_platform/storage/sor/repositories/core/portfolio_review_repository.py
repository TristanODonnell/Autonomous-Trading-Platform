from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.portfolio_reviews import (
    PortfolioReviewDecisionRow,
    PortfolioReviewRow,
    PortfolioScorecardRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class PortfolioReviewRepository(BaseRepository):
    def insert_review(self, row: PortfolioReviewRow) -> None:
        self.session.add(row)
        self.session.flush()

    def insert_scorecard(self, row: PortfolioScorecardRow) -> None:
        self.session.add(row)
        self.session.flush()

    def insert_decision(self, row: PortfolioReviewDecisionRow) -> None:
        self.session.add(row)
        self.session.flush()

    def get_review(self, review_id: str) -> PortfolioReviewRow | None:
        row: PortfolioReviewRow | None = self.session.get(PortfolioReviewRow, review_id)
        return row

    def recent_reviews(
        self, *, before: datetime | None = None, limit: int = 10
    ) -> list[PortfolioReviewRow]:
        """Most recent reviews first, optionally strictly before `before`."""
        query = select(PortfolioReviewRow)
        if before is not None:
            query = query.where(PortfolioReviewRow.reviewed_at < before)
        return list(
            self.session.scalars(
                query.order_by(PortfolioReviewRow.reviewed_at.desc()).limit(limit)
            ).all()
        )

    def last_swap_eligible(self, *, before: datetime | None = None) -> PortfolioReviewRow | None:
        query = select(PortfolioReviewRow).where(PortfolioReviewRow.swap_eligible.is_(True))
        if before is not None:
            query = query.where(PortfolioReviewRow.reviewed_at < before)
        row: PortfolioReviewRow | None = self.session.scalars(
            query.order_by(PortfolioReviewRow.reviewed_at.desc()).limit(1)
        ).one_or_none()
        return row

    def scorecards(self, review_id: str) -> list[PortfolioScorecardRow]:
        return list(
            self.session.scalars(
                select(PortfolioScorecardRow)
                .where(PortfolioScorecardRow.review_id == review_id)
                .order_by(PortfolioScorecardRow.rank, PortfolioScorecardRow.strategy_id)
            ).all()
        )

    def latest_scorecards(self) -> list[PortfolioScorecardRow]:
        """Scorecards of the most recent review (empty when there is none)."""
        latest = self.recent_reviews(limit=1)
        return self.scorecards(latest[0].review_id) if latest else []

    def decisions(self, review_id: str) -> list[PortfolioReviewDecisionRow]:
        return list(
            self.session.scalars(
                select(PortfolioReviewDecisionRow)
                .where(PortfolioReviewDecisionRow.review_id == review_id)
                .order_by(
                    PortfolioReviewDecisionRow.decision_type,
                    PortfolioReviewDecisionRow.strategy_id,
                )
            ).all()
        )

    def decisions_for_reviews(self, review_ids: list[str]) -> list[PortfolioReviewDecisionRow]:
        if not review_ids:
            return []
        return list(
            self.session.scalars(
                select(PortfolioReviewDecisionRow).where(
                    PortfolioReviewDecisionRow.review_id.in_(review_ids)
                )
            ).all()
        )
