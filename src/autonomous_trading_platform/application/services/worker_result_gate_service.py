"""Accept or refuse a result the worker hands to Box A (pre-launch plan 3.1).

Order of checks, first failure wins and is recorded:

1. idempotency: a run_id already decided returns that decision (``duplicate=True``)
2. the ``worker_results`` automation switch and the kill switch (AutomationGateService)
3. the payload is not empty
4. ``dataset_as_of`` is present and no older than ``worker_result_max_age_days`` sessions
   ago (calendar days; the worker pins to Box A's publish index, which exists per session)
5. ``git_sha`` matches this box's ``GIT_SHA`` when ``worker_result_require_matching_git_sha``
6. a typed payload (``bench_resims``) parses as its schema

Every submission, accepted or rejected, is persisted in ``worker_result_submissions`` with
the reason, so the dashboard can show what was refused and why. Applying an accepted
result is the hand-off's job (3.2/3.3); this service only decides.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.automation_gate_service import (
    Automation,
    AutomationGateService,
)
from autonomous_trading_platform.contracts.governance.worker_results import (
    BenchResimsPayload,
    WorkerResultDecision,
    WorkerResultRejection,
    WorkerResultStatus,
    WorkerResultSubmission,
    WorkerResultType,
)
from autonomous_trading_platform.observability import metrics as _metrics
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.worker_result_submissions import (
    WorkerResultSubmissionRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.worker_result_submissions_repository import (
    WorkerResultSubmissionsRepository,
)

logger = get_logger(__name__)


class WorkerResultGateService:
    def __init__(
        self,
        session: Session,
        *,
        gate: AutomationGateService | None = None,
        settings_repo: OperatorSettingsRepository | None = None,
        submissions_repo: WorkerResultSubmissionsRepository | None = None,
        local_git_sha: str | None = None,
    ) -> None:
        self._gate = gate or AutomationGateService(session)
        self._settings = settings_repo or OperatorSettingsRepository(session)
        self._submissions = submissions_repo or WorkerResultSubmissionsRepository(session)
        self._local_git_sha = local_git_sha if local_git_sha is not None else os.getenv("GIT_SHA")

    def evaluate(
        self, submission: WorkerResultSubmission, *, now: datetime | None = None
    ) -> WorkerResultDecision:
        now = now or datetime.now(UTC)
        existing = self._submissions.get(submission.run_id)
        if existing is not None:
            return WorkerResultDecision(
                run_id=existing.run_id,
                result_type=submission.result_type,
                status=WorkerResultStatus(existing.status),
                reason=existing.rejection_reason,
                duplicate=True,
                received_at=existing.received_at,
                dataset_age_days=_age_days(existing.dataset_as_of, now),
            )

        age = _age_days(submission.dataset_as_of, now)
        reason = self._reject_reason(submission, age)
        status = WorkerResultStatus.REJECTED if reason else WorkerResultStatus.ACCEPTED
        self._submissions.insert(
            WorkerResultSubmissionRow(
                run_id=submission.run_id,
                result_type=submission.result_type.value,
                status=status.value,
                rejection_reason=reason.value if reason else None,
                worker_host=submission.worker_host,
                git_sha=submission.git_sha,
                fixture=submission.fixture,
                produced_at=submission.produced_at,
                dataset_as_of=submission.dataset_as_of,
                dataset_versions=list(submission.dataset_versions),
                payload=dict(submission.payload),
                received_at=now,
            )
        )
        _metrics.worker_results_total.add(
            1,
            {
                "result_type": submission.result_type.value,
                "outcome": status.value,
                "reason": reason.value if reason else "accepted",
            },
        )
        logger.info(
            "worker_result.decided",
            extra={
                "run_id": submission.run_id,
                "result_type": submission.result_type.value,
                "status": status.value,
                "reason": reason.value if reason else None,
                "dataset_as_of": submission.dataset_as_of.isoformat()
                if submission.dataset_as_of
                else None,
                "dataset_age_days": age,
                "git_sha": submission.git_sha,
            },
        )
        return WorkerResultDecision(
            run_id=submission.run_id,
            result_type=submission.result_type,
            status=status,
            reason=reason.value if reason else None,
            received_at=now,
            dataset_age_days=age,
        )

    # ----------------------------------------------------------------- private

    def _reject_reason(
        self, submission: WorkerResultSubmission, age_days: int | None
    ) -> WorkerResultRejection | None:
        gate = self._gate.check(Automation.WORKER_RESULTS)
        if not gate.allowed:
            if gate.kill_switch_enabled:
                return WorkerResultRejection.KILL_SWITCH
            return WorkerResultRejection.AUTOMATION_DISABLED
        if not submission.payload:
            return WorkerResultRejection.EMPTY_PAYLOAD
        if submission.result_type is WorkerResultType.BENCH_RESIMS:
            try:
                BenchResimsPayload.model_validate(submission.payload)
            except ValidationError as exc:
                logger.warning(
                    "worker_result.invalid_payload",
                    extra={"run_id": submission.run_id, "errors": exc.error_count()},
                )
                return WorkerResultRejection.INVALID_PAYLOAD
        settings = self._settings.get_or_create_default()
        if submission.dataset_as_of is None:
            return WorkerResultRejection.MISSING_DATASET_AS_OF
        max_age = int(getattr(settings, "worker_result_max_age_days", 3))
        if age_days is None or age_days > max_age:
            return WorkerResultRejection.STALE_DATASET
        if bool(getattr(settings, "worker_result_require_matching_git_sha", True)):
            if not submission.git_sha:
                return WorkerResultRejection.MISSING_GIT_SHA
            if self._local_git_sha and not _same_sha(submission.git_sha, self._local_git_sha):
                return WorkerResultRejection.GIT_SHA_MISMATCH
        return None


def _age_days(as_of: date | None, now: datetime) -> int | None:
    if as_of is None:
        return None
    return (now.date() - as_of).days


def _same_sha(a: str, b: str) -> bool:
    """Full and short SHAs compare equal when one is a prefix of the other (>= 7 chars)."""
    a, b = a.strip().lower(), b.strip().lower()
    if len(a) < 7 or len(b) < 7:
        return a == b
    return a.startswith(b) or b.startswith(a)
