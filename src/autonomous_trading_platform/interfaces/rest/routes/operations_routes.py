from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_operator_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.operations_service import (
    OperationsService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.operations_schemas import (
    OperationsJobRunsResponse,
    OperationsJobsResponse,
    OperationsRuntimeStateResponse,
)

router = APIRouter(prefix="/operations", tags=["operations"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)
_operator_dependency = Depends(require_operator_or_admin)


@router.get(
    "/jobs",
    response_model=SuccessEnvelope[OperationsJobsResponse],
    status_code=status.HTTP_200_OK,
)
def list_operations_jobs(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationsJobsResponse]:
    service = OperationsService(session=session)
    return success_response(
        data=OperationsJobsResponse(jobs=service.list_jobs()),
        request_id=request_id,
    )


@router.get(
    "/jobs/{job_name}/runs",
    response_model=SuccessEnvelope[OperationsJobRunsResponse],
    status_code=status.HTTP_200_OK,
)
def list_operations_job_runs(
    job_name: str,
    limit: int = Query(default=20, ge=1, le=100),
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationsJobRunsResponse]:
    service = OperationsService(session=session)
    return success_response(
        data=OperationsJobRunsResponse(
            job_name=job_name,
            runs=service.list_job_runs(job_name=job_name, limit=limit),
        ),
        request_id=request_id,
    )


@router.get(
    "/runtime-state",
    response_model=SuccessEnvelope[OperationsRuntimeStateResponse],
    status_code=status.HTTP_200_OK,
)
def get_operations_runtime_state(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationsRuntimeStateResponse]:
    service = OperationsService(session=session)
    return success_response(
        data=OperationsRuntimeStateResponse(**service.get_runtime_state()),
        request_id=request_id,
    )
