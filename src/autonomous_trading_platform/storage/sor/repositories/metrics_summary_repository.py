# storage/sor/repositories/metrics_summary_repository.py

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.metrics_summary import (
    MetricsSummary as MetricsSummaryRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class MetricsSummaryRepository(BaseRepository):
    def get_by_metrics_snapshot_id(
        self,
        metrics_snapshot_id: str,
    ) -> MetricsSummaryRow | None:
        stmt = select(MetricsSummaryRow).where(
            MetricsSummaryRow.metrics_snapshot_id == metrics_snapshot_id
        )
        return cast(MetricsSummaryRow | None, self.session.scalars(stmt).one_or_none())

    def get_by_run_id(self, run_id: str) -> MetricsSummaryRow | None:
        stmt = select(MetricsSummaryRow).where(MetricsSummaryRow.run_id == run_id)
        return cast(MetricsSummaryRow | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: MetricsSummaryRow) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[MetricsSummaryRow]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: MetricsSummaryRow) -> MetricsSummaryRow:
        existing = self.get_by_metrics_snapshot_id(row.metrics_snapshot_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in MetricsSummaryRow.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_metrics_snapshot_id(self, metrics_snapshot_id: str) -> None:
        obj = self.get_by_metrics_snapshot_id(metrics_snapshot_id)
        if obj is not None:
            self.session.delete(obj)

    def to_row(self, contract: MetricsSummary) -> MetricsSummaryRow:
        return MetricsSummaryRow(
            metrics_snapshot_id=contract.metrics_snapshot_id,
            run_id=contract.run_id,
            created_at=contract.created_at,
            total_return=contract.total_return,
            sharpe_ratio=contract.sharpe_ratio,
            max_drawdown=contract.max_drawdown,
            trade_count=contract.trade_count,
            winning_trade_count=contract.winning_trade_count,
            losing_trade_count=contract.losing_trade_count,
            volatility=contract.volatility,
            metrics_json=contract.metrics_json,
        )

    def to_contract(self, row: MetricsSummaryRow) -> MetricsSummary:
        return MetricsSummary(
            metrics_snapshot_id=row.metrics_snapshot_id,
            run_id=row.run_id,
            created_at=row.created_at,
            total_return=row.total_return,
            sharpe_ratio=row.sharpe_ratio,
            max_drawdown=row.max_drawdown,
            trade_count=row.trade_count,
            winning_trade_count=row.winning_trade_count,
            losing_trade_count=row.losing_trade_count,
            volatility=row.volatility,
            metrics_json=row.metrics_json,
        )
