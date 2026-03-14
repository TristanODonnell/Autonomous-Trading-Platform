from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class TickerLifecycleEventType(StrEnum):
    RENAME = "rename"
    DELISTING = "delisting"
    MERGER = "merger"
    SUCCESSOR = "successor"


class TickerLifecycleEvent(BaseModel):
    event_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    event_type: TickerLifecycleEventType
    effective_at: datetime
    successor_symbol: str | None = None
    source: str = Field(min_length=1)
    notes: str | None = None
