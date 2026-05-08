from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RecentActivityItemResponse(BaseModel):
    event_type: str
    description: str
    timestamp: datetime
    severity: Literal["info", "warning", "error", "critical"]


class RecentActivityResponse(BaseModel):
    items: list[RecentActivityItemResponse]
