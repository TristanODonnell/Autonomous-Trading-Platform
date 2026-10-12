from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.worker_result_submissions import (
    WorkerResultSubmissionRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class WorkerResultSubmissionsRepository(BaseRepository):
    def get(self, run_id: str) -> WorkerResultSubmissionRow | None:
        return cast(
            WorkerResultSubmissionRow | None, self.session.get(WorkerResultSubmissionRow, run_id)
        )

    def insert(self, row: WorkerResultSubmissionRow) -> WorkerResultSubmissionRow:
        self.session.add(row)
        self.session.flush()
        return row

    def latest_accepted(self, result_type: str) -> WorkerResultSubmissionRow | None:
        stmt = (
            select(WorkerResultSubmissionRow)
            .where(WorkerResultSubmissionRow.result_type == result_type)
            .where(WorkerResultSubmissionRow.status.in_(["accepted", "applied"]))
            .order_by(WorkerResultSubmissionRow.received_at.desc())
            .limit(1)
        )
        return cast(WorkerResultSubmissionRow | None, self.session.scalars(stmt).first())

    def accepted_unapplied(self, result_type: str) -> list[WorkerResultSubmissionRow]:
        """Accepted, not yet applied results of one type, newest first."""
        stmt = (
            select(WorkerResultSubmissionRow)
            .where(WorkerResultSubmissionRow.result_type == result_type)
            .where(WorkerResultSubmissionRow.status == "accepted")
            .order_by(
                WorkerResultSubmissionRow.received_at.desc(),
                WorkerResultSubmissionRow.produced_at.desc(),
                WorkerResultSubmissionRow.run_id.desc(),
            )
        )
        return list(self.session.scalars(stmt).all())

    def list_since(
        self, since: datetime, *, status: str | None = None
    ) -> list[WorkerResultSubmissionRow]:
        stmt = select(WorkerResultSubmissionRow).where(
            WorkerResultSubmissionRow.received_at >= since
        )
        if status is not None:
            stmt = stmt.where(WorkerResultSubmissionRow.status == status)
        stmt = stmt.order_by(WorkerResultSubmissionRow.received_at.desc())
        return list(self.session.scalars(stmt).all())

    def mark_applied(self, run_id: str, *, applied_at: datetime, review_id: str | None) -> None:
        row = self.get(run_id)
        if row is None:
            raise KeyError(run_id)
        row.status = "applied"
        row.applied_at = applied_at
        row.review_id = review_id
        self.session.flush()
