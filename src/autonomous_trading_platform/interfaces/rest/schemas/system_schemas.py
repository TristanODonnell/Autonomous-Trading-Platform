from __future__ import annotations

from pydantic import BaseModel


class SystemHealthResponse(BaseModel):
    status: str  # "healthy" | "degraded" | "critical"
    trading_mode: str  # "simulation" | "paper" | "live"
    active_strategy_count: int
    alerts: list[str]
