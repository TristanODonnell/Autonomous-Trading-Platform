from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.detailed_health import (
    DetailedSystemHealthReport,
    HealthStatus,
    ServiceHealthReport,
)

from .broker_health_service import BrokerHealthService
from .control_state_health_service import ControlStateHealthService
from .data_pipeline_health_service import DataPipelineHealthService
from .job_health_service import JobHealthService
from .market_session_health_service import MarketSessionHealthService
from .otel_health_service import OtelHealthService


def _aggregate_status(reports: list[ServiceHealthReport]) -> HealthStatus:
    if any(r.status == HealthStatus.CRITICAL for r in reports):
        return HealthStatus.CRITICAL
    if any(r.status == HealthStatus.DEGRADED for r in reports):
        return HealthStatus.DEGRADED
    return HealthStatus.OK


class DetailedSystemHealthService:
    """
    TASK-617 — aggregates all health services into a single report.

    Consumed by GET /api/v1/system/health/detailed.
    """

    def __init__(
        self,
        session: Session,
        *,
        broker_client: Any = None,
        environment: str | None = None,
    ) -> None:
        self._session = session
        self._broker_client = broker_client
        self._environment = environment or os.getenv("RATP_ENVIRONMENT", "unknown")

    def get_health(self) -> DetailedSystemHealthReport:
        otel_report = OtelHealthService().check()
        job_report = JobHealthService(self._session).check()
        data_report = DataPipelineHealthService(self._session).check()
        market_session_report = MarketSessionHealthService().check()
        broker_report = BrokerHealthService(
            self._session,
            broker_client=self._broker_client,
        ).check()
        control_report = ControlStateHealthService(self._session).check()

        reports = [
            otel_report,
            job_report,
            data_report,
            market_session_report,
            broker_report,
            control_report,
        ]
        overall = _aggregate_status(reports)

        all_checks = [c for r in reports for c in r.checks]
        summary: dict[str, int] = {
            "total_checks": len(all_checks),
            "ok": sum(1 for c in all_checks if c.status == HealthStatus.OK),
            "degraded": sum(1 for c in all_checks if c.status == HealthStatus.DEGRADED),
            "critical": sum(1 for c in all_checks if c.status == HealthStatus.CRITICAL),
        }

        return DetailedSystemHealthReport(
            status=overall,
            checked_at=datetime.now(UTC),
            environment=self._environment,
            services=reports,
            summary=summary,
        )
