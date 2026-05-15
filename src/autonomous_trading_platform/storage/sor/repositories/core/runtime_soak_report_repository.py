from __future__ import annotations

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakVerificationReport,
)
from autonomous_trading_platform.storage.sor.models.runtime_soak_reports import (
    RuntimeSoakReportRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class RuntimeSoakReportRepository(BaseRepository):
    def append_report(self, report: RuntimeSoakVerificationReport) -> RuntimeSoakReportRow:
        payload = report.model_dump(mode="json")
        row = RuntimeSoakReportRow(
            status=report.status.value,
            environment=report.environment,
            checked_at=report.checked_at,
            window_start=report.window_start,
            window_end=report.window_end,
            failed_checks=[check.model_dump(mode="json") for check in report.failed_checks],
            runtime_metadata=report.summary,
            report_json=payload,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_by_window(
        self,
        *,
        environment: str,
        window_start,
        window_end,
    ) -> list[RuntimeSoakReportRow]:
        stmt = (
            select(RuntimeSoakReportRow)
            .where(
                RuntimeSoakReportRow.environment == environment,
                RuntimeSoakReportRow.window_start >= window_start,
                RuntimeSoakReportRow.window_end <= window_end,
            )
            .order_by(RuntimeSoakReportRow.checked_at.asc())
        )
        return list(self.session.scalars(stmt).all())
