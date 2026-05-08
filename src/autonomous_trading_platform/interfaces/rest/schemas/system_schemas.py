from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SystemHealthResponse(BaseModel):
    status: str  # "healthy" | "degraded" | "critical"
    trading_mode: str  # "simulation" | "paper" | "live"
    active_strategy_count: int
    alerts: list[str]


class TradingModeUpdateRequest(BaseModel):
    mode: Literal["simulation", "paper", "live"]
    rationale: str | None = None


class TradingModeUpdateResponse(BaseModel):
    mode: Literal["simulation", "paper", "live"]
    previous_mode: Literal["simulation", "paper", "live"]
    rationale: str
    updated_by: str
    updated_at: datetime
