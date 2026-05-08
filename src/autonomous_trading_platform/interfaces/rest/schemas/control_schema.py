from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class KillSwitchRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class KillSwitchResponse(BaseModel):
    status: Literal["halted"]
    kill_switch_active: bool
    canceled_order_count: int
    reason: str
    triggered_by: str
    triggered_at: datetime


class RuntimeControlActionRequest(BaseModel):
    rationale: str = Field(min_length=1, max_length=500)


class RuntimeControlActionResponse(BaseModel):
    status: Literal["paused", "resumed"]
    trading_paused: bool
    rationale: str
    updated_by: str
    updated_at: datetime
