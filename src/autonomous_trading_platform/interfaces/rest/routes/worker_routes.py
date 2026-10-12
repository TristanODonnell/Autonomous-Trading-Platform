"""Box A's side of the worker hand-off (pre-launch plan 3.2).

``POST /worker/results``: the research worker posts a result; ``WorkerResultGateService``
accepts or rejects it (switch, kill switch, payload, dataset age, git SHA) and the decision
comes back with HTTP 200 either way, so the worker can record it. Posting the same run id
again returns the stored decision (``duplicate: true``). The ``worker`` role (or admin)
is required to post; operators can list what arrived.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_operator_or_admin,
    require_worker_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.worker_result_gate_service import (
    WorkerResultGateService,
)
from autonomous_trading_platform.contracts.governance.worker_results import (
    WorkerResultDecision,
    WorkerResultRecord,
    WorkerResultSubmission,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.models.worker_result_submissions import (
    WorkerResultSubmissionRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.worker_result_submissions_repository import (
    WorkerResultSubmissionsRepository,
)

router = APIRouter(prefix="/worker", tags=["worker"])
_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


def _record(row: WorkerResultSubmissionRow) -> WorkerResultRecord:
    return WorkerResultRecord(
        run_id=row.run_id,
        result_type=row.result_type,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        rejection_reason=row.rejection_reason,
        worker_host=row.worker_host,
        git_sha=row.git_sha,
        fixture=row.fixture,
        produced_at=row.produced_at,
        dataset_as_of=row.dataset_as_of,
        dataset_versions=list(row.dataset_versions or []),
        received_at=row.received_at,
        applied_at=row.applied_at,
        review_id=row.review_id,
    )


@router.post(
    "/results",
    response_model=SuccessEnvelope[WorkerResultDecision],
    status_code=status.HTTP_200_OK,
)
def submit_result(
    payload: WorkerResultSubmission,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_worker_or_admin),
) -> SuccessEnvelope[WorkerResultDecision]:
    try:
        decision = WorkerResultGateService(session).evaluate(payload, now=datetime.now(UTC))
        session.commit()
    except Exception:
        session.rollback()
        raise
    return success_response(data=decision, request_id=request_id)


@router.get("/results", response_model=SuccessEnvelope[list[WorkerResultRecord]])
def list_results(
    days: int = Query(default=7, ge=1, le=90),
    status_filter: str | None = Query(default=None, alias="status"),
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[list[WorkerResultRecord]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = WorkerResultSubmissionsRepository(session).list_since(since, status=status_filter)
    return success_response(data=[_record(r) for r in rows], request_id=request_id)


@router.get("/results/{run_id}", response_model=SuccessEnvelope[WorkerResultRecord])
def get_result(
    run_id: str,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[WorkerResultRecord]:
    row = WorkerResultSubmissionsRepository(session).get(run_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{run_id} not found")
    return success_response(data=_record(row), request_id=request_id)
