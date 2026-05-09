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


class StrategyControlStateResponse(BaseModel):
    strategy_id: str
    enabled: bool
    status: Literal["live", "paper", "off"]
    reason: str | None
    updated_by: str | None
    updated_at: datetime | None


class ControlsStateResponse(BaseModel):
    kill_switch_active: bool
    trading_enabled: bool
    trading_paused: bool
    trading_mode: Literal["simulation", "paper", "live"]
    reason: str | None
    updated_by: str | None
    updated_at: datetime
    strategies: list[StrategyControlStateResponse]
