"""Operations domain replay hook (P1 — read-only health snapshot)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.operational_alert_service import (
    OperationalAlertService,
)
from autonomous_trading_platform.application.services.operations_service import (
    OperationsService,
)
from autonomous_trading_platform.application.services.system_health_service import (
    SystemHealthService,
)
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    OperationsReplayResult,
    OperationsSummary,
    PlatformReplayContext,
)


def snapshot_operations_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
) -> OperationsReplayResult:
    """Read system health, job statuses, and active alerts."""
    base = dict(
        domain="operations",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    warnings: list[str] = []
    system_health_status: str | None = None
    active_alerts = 0
    critical_alerts = 0

    try:
        settings = Settings()
        health = SystemHealthService(session=session, settings=settings).get_health()
        system_health_status = health.get("status")
    except Exception as exc:
        warnings.append(f"System health check failed: {exc}")

    try:
        alerts = OperationalAlertService(session=session).list_alerts(
            status="active",
            limit=500,
        )
        active_alerts = len(alerts)
        critical_alerts = sum(1 for a in alerts if getattr(a, "severity", "") == "critical")
    except Exception as exc:
        warnings.append(f"Alert list failed: {exc}")

    return OperationsReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        system_health_status=system_health_status,
        active_alerts_count=active_alerts,
        critical_alerts_count=critical_alerts,
        summary={
            "system_health_status": system_health_status,
            "active_alerts_count": active_alerts,
            "critical_alerts_count": critical_alerts,
            "timestamp": timestamp.isoformat(),
        },
    )


def build_operations_summary(*, session: Session) -> OperationsSummary:
    """Read current operations state for the platform artifact bundle."""
    system_health_status: str | None = None
    active_alerts = 0
    critical_alerts = 0
    jobs_failed = 0

    try:
        settings = Settings()
        health = SystemHealthService(session=session, settings=settings).get_health()
        system_health_status = health.get("status")
    except Exception:
        pass

    try:
        alerts = OperationalAlertService(session=session).list_alerts(status="active", limit=500)
        active_alerts = len(alerts)
        critical_alerts = sum(1 for a in alerts if getattr(a, "severity", "") == "critical")
    except Exception:
        pass

    try:
        jobs = OperationsService(session=session).list_jobs()
        jobs_failed = sum(1 for j in jobs if j.get("latest_status") == "failed")
    except Exception:
        pass

    return OperationsSummary(
        system_health_status=system_health_status,
        active_alerts_count=active_alerts,
        critical_alerts_count=critical_alerts,
        jobs_failed_last_cycle=jobs_failed,
    )
