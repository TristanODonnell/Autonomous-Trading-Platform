from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import get_request_id
from autonomous_trading_platform.api.envelope import (
    SuccessEnvelope,
    success_response,
)
from autonomous_trading_platform.application.services.active_strategies_service import (
    ActiveStrategiesService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.active_strategies_schema import (
    ActiveStrategiesResponse,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get(
    "/active",
    response_model=SuccessEnvelope[ActiveStrategiesResponse],
)
def get_active_strategies(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[ActiveStrategiesResponse]:
    service = ActiveStrategiesService(session=session)
    result = service.list_active_strategies()

    return success_response(
        data=ActiveStrategiesResponse(strategies=result),
        request_id=request_id,
    )
