from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogEvent(BaseModel):
    event_id: str
    run_id: str | None = None
    event_type: str
    component: str
    event_timestamp: datetime
    message: str
    metadata: dict[str, Any] | None = None
