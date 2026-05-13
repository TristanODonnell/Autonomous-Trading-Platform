from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns

logger = logging.getLogger(__name__)

_RESCUED_ERROR = "rescued: orphan running job detected on startup"


class OrphanJobRecoveryService:
    """
    Closes out RUNNING job records left behind by a previous process crash.

    Call ``rescue_orphan_running_jobs`` once at soak-loop startup (before any
    jobs run) so that stale ``runtime_job_runs`` entries in status=running are
    transitioned to status=failed.  This ensures:

    - ``_check_stale_running_state`` in RuntimeSoakVerificationService never
      flags records from the prior run as currently stuck.
    - ``_check_concurrent_running_jobs`` cannot produce false positives from
      a crashed predecessor process.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def rescue_orphan_running_jobs(self, *, cutoff: datetime) -> list[RuntimeJobRuns]:
        """
        Mark all RUNNING job records started before *cutoff* as failed.

        Returns the list of rescued rows.  The caller is responsible for
        committing the session.
        """
        stmt = (
            select(RuntimeJobRuns)
            .where(
                RuntimeJobRuns.status == "running",
                RuntimeJobRuns.started_at < cutoff,
            )
            .order_by(RuntimeJobRuns.started_at.asc())
        )
        stale = list(self._session.scalars(stmt).all())

        if not stale:
            return []

        now = datetime.now(UTC)
        for row in stale:
            duration_ms = int((now - row.started_at).total_seconds() * 1000)
            row.status = "failed"
            row.completed_at = now
            row.duration_ms = duration_ms
            row.error_message = _RESCUED_ERROR

        self._session.flush()

        logger.warning(
            "orphan_job_recovery.rescued_stale_running_jobs",
            extra={
                "count": len(stale),
                "job_names": [row.job_name for row in stale],
            },
        )
        return stale
