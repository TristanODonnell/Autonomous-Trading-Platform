from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import get_request_id
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.system_health_service import (
    SystemHealthService,
)
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.system_schemas import SystemHealthResponse

router = APIRouter(prefix="/system", tags=["system"])
_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get("/health", response_model=SuccessEnvelope[SystemHealthResponse])
def get_system_health(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[SystemHealthResponse]:
    settings = Settings()
    service = SystemHealthService(session=session, settings=settings)
    result = service.get_health()

    return success_response(
        data=SystemHealthResponse(**result),
        request_id=request_id,
    )
