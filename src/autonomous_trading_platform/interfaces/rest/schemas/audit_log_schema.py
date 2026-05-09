from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditLogEventResponse(BaseModel):
    event_id: str
    action_type: str
    description: str
    user: str | None
    timestamp: datetime
    strategy_id: str | None
    rationale: str | None


class AuditLogPaginationResponse(BaseModel):
    page: int
    page_size: int
    total: int


class AuditLogListResponse(BaseModel):
    events: list[AuditLogEventResponse]
    pagination: AuditLogPaginationResponse
