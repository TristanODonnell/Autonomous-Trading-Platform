from __future__ import annotations

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.reconciliation_report import (
    ReconciliationReport,
)
from autonomous_trading_platform.storage.sor.models.reconciliation_snapshots import (
    ReconciliationSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class ReconciliationSnapshotRepository(BaseRepository):
    def append_report(self, report: ReconciliationReport) -> None:
        rows = [
            ReconciliationSnapshot(
                run_id=report.run_id,
                reconciled_at=report.reconciled_at,
                check_type=check.check_type.value,
                symbol=check.symbol,
                expected_value=check.expected_value,
                actual_value=check.actual_value,
                delta=check.delta,
                tolerance=check.tolerance,
                severity=check.severity.value,
                status=check.status.value,
                detail=check.detail,
            )
            for check in report.checks
        ]
        self.session.add_all(rows)

    def list_by_run_id(self, run_id: str) -> list[ReconciliationSnapshot]:
        stmt = (
            select(ReconciliationSnapshot)
            .where(ReconciliationSnapshot.run_id == run_id)
            .order_by(ReconciliationSnapshot.reconciled_at)
        )
        return list(self.session.execute(stmt).scalars().all())
