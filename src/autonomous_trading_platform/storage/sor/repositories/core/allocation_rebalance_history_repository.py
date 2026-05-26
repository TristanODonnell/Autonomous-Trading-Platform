from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.allocation_rebalance_history import (
    AllocationRebalanceHistory,
)

_STALE_LOCK_WINDOW_HOURS = 4


class AllocationRebalanceHistoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: AllocationRebalanceHistory) -> AllocationRebalanceHistory:
        self._session.add(row)
        self._session.flush()
        return row

    def get_last_completed(self) -> AllocationRebalanceHistory | None:
        return cast(
            AllocationRebalanceHistory | None,
            self._session.scalars(
                select(AllocationRebalanceHistory)
                .where(AllocationRebalanceHistory.status == "completed")
                .order_by(AllocationRebalanceHistory.completed_at.desc())
                .limit(1)
            ).one_or_none(),
        )

    def get_active_lock(self, *, now: datetime) -> AllocationRebalanceHistory | None:
        """Returns a non-stale running row if a concurrent rebalance is in progress."""
        stale_before = now - timedelta(hours=_STALE_LOCK_WINDOW_HOURS)
        return cast(
            AllocationRebalanceHistory | None,
            self._session.scalars(
                select(AllocationRebalanceHistory)
                .where(AllocationRebalanceHistory.status == "running")
                .where(AllocationRebalanceHistory.started_at >= stale_before)
                .order_by(AllocationRebalanceHistory.started_at.desc())
                .limit(1)
            ).one_or_none(),
        )

    def update_status(
        self,
        *,
        rebalance_id: str,
        status: str,
        completed_at: datetime,
        strategies_evaluated: int | None = None,
        allocation_changes_count: int | None = None,
        total_allocation_delta_pct: float | None = None,
        churn_pct: float | None = None,
        skipped_reason: str | None = None,
        result_summary_json: dict | None = None,
    ) -> None:
        row = self._session.get(AllocationRebalanceHistory, rebalance_id)
        if row is None:
            return
        row.status = status
        row.completed_at = completed_at
        if strategies_evaluated is not None:
            row.strategies_evaluated = strategies_evaluated
        if allocation_changes_count is not None:
            row.allocation_changes_count = allocation_changes_count
        if total_allocation_delta_pct is not None:
            row.total_allocation_delta_pct = total_allocation_delta_pct
        if churn_pct is not None:
            row.churn_pct = churn_pct
        if skipped_reason is not None:
            row.skipped_reason = skipped_reason
        if result_summary_json is not None:
            row.result_summary_json = result_summary_json
        self._session.flush()
