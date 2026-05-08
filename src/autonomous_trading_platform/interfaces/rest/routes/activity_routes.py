from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import get_request_id
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.recent_activity_service import (
    RecentActivityService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.activity_schema import (
    RecentActivityResponse,
)

router = APIRouter(prefix="/activity", tags=["activity"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get(
    "/recent",
    response_model=SuccessEnvelope[RecentActivityResponse],
)
def get_recent_activity(
    limit: int = Query(default=20, ge=1, le=100),
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[RecentActivityResponse]:
    service = RecentActivityService(session=session)
    items = service.list_recent_activity(limit=limit)

    return success_response(
        data=RecentActivityResponse(items=items),
        request_id=request_id,
    )
