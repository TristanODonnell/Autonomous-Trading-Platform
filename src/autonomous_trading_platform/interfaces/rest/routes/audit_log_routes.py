from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_operator_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.audit_log_service import AuditLogService
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.audit_log_schema import (
    AuditLogEventResponse,
    AuditLogListResponse,
    AuditLogPaginationResponse,
)

router = APIRouter(prefix="/audit-log", tags=["audit-log"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get(
    "",
    response_model=SuccessEnvelope[AuditLogListResponse],
)
def get_audit_log(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    action_type: Annotated[str | None, Query()] = None,
    strategy_id: Annotated[str | None, Query()] = None,
    user_id: Annotated[str | None, Query()] = None,
    from_date: Annotated[datetime | None, Query()] = None,
    to_date: Annotated[datetime | None, Query()] = None,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[AuditLogListResponse]:
    service = AuditLogService(session=session)
    result = service.list_events(
        page=page,
        page_size=page_size,
        action_type=action_type,
        strategy_id=strategy_id,
        user_id=user_id,
        from_date=from_date,
        to_date=to_date,
    )

    return success_response(
        data=AuditLogListResponse(
            events=[
                AuditLogEventResponse(
                    event_id=e.event_id,
                    action_type=e.action_type,
                    description=e.description,
                    user=e.user,
                    timestamp=e.timestamp,
                    strategy_id=e.strategy_id,
                    rationale=e.rationale,
                )
                for e in result.events
            ],
            pagination=AuditLogPaginationResponse(
                page=page,
                page_size=page_size,
                total=result.total,
            ),
        ),
        request_id=request_id,
    )
