from __future__ import annotations

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class BenchEvaluationRepository(BaseRepository):
    def insert(self, row: BenchEvaluationRow) -> None:
        self.session.add(row)
        self.session.flush()

    def get_review(self, review_id: str) -> list[BenchEvaluationRow]:
        return list(
            self.session.scalars(
                select(BenchEvaluationRow)
                .where(BenchEvaluationRow.review_id == review_id)
                .order_by(BenchEvaluationRow.strategy_id)
            ).all()
        )

    def latest_for_strategy(self, strategy_id: str) -> BenchEvaluationRow | None:
        row: BenchEvaluationRow | None = self.session.scalars(
            select(BenchEvaluationRow)
            .where(BenchEvaluationRow.strategy_id == strategy_id)
            .order_by(BenchEvaluationRow.reviewed_at.desc())
            .limit(1)
        ).one_or_none()
        return row

    def reviewed_strategy_ids(self) -> set[str]:
        return set(self.session.scalars(select(BenchEvaluationRow.strategy_id).distinct()).all())
