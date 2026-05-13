from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.audit_log import AuditLogEvent
from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.storage.sor.models.operator_settings import OperatorSettingsRow
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    DEFAULT_OPERATOR_SETTINGS_ID,
)


class PipelineFailureNotificationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit_logs = AuditLogRepository(session)

    def notify_failure(self, failed_run: RuntimeJobRun) -> None:
        if not self._enabled():
            return

        self._audit_logs.add(
            AuditLogEvent(
                event_id=str(uuid4()),
                run_id=failed_run.correlation_id,
                event_type="PIPELINE_FAILURE_EVENT",
                component="notifications",
                event_timestamp=datetime.now(UTC),
                message=f"Pipeline failure: {failed_run.job_name}",
                metadata={
                    "channel": "notify_pipeline_failures",
                    "job_run_id": failed_run.job_run_id,
                    "job_name": failed_run.job_name,
                    "trigger_type": failed_run.trigger_type,
                    "correlation_id": failed_run.correlation_id,
                    "parent_job_run_id": failed_run.parent_job_run_id,
                    "error_message": failed_run.error_message,
                    "started_at": failed_run.started_at.isoformat(),
                    "completed_at": (
                        failed_run.completed_at.isoformat()
                        if failed_run.completed_at is not None
                        else None
                    ),
                    "duration_ms": failed_run.duration_ms,
                },
            )
        )

    def _enabled(self) -> bool:
        settings = self._session.get(OperatorSettingsRow, DEFAULT_OPERATOR_SETTINGS_ID)
        if settings is None:
            return True

        return bool(settings.notify_pipeline_failures)
