from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class StrategyHealthStatus(StrEnum):
    HEALTHY = "healthy"
    WATCH = "watch"
    DEGRADING = "degrading"
    CRITICAL = "critical"
    # Operational risk containment: allocation halted, operator review required.
    # Does NOT imply governance revocation.
    SUSPENDED = "suspended"


class StrategyQualityScoreRecord(BaseModel):
    score_id: str
    strategy_id: str
    rebalance_run_id: str | None
    quality_score: float
    live_score: float | None
    backtest_score: float | None
    blended_score: float | None
    alpha_weight: float | None
    score_components: dict | None
    data_window: dict | None
    governance_state: str | None
    computed_at: datetime


class StrategyHealthStateRecord(BaseModel):
    health_id: str
    strategy_id: str
    health_status: StrategyHealthStatus
    previous_health_status: StrategyHealthStatus | None
    health_reason: str | None
    consecutive_decline_count: int
    quality_score_trend: str | None
    latest_quality_score: float | None
    realized_drawdown: float | None
    last_health_evaluated_at: datetime | None
    last_transition_at: datetime | None
    evaluation_count: int
    last_rebalance_run_id: str | None


class StrategyHealthSummary(BaseModel):
    """Per-strategy health summary for API responses."""

    strategy_id: str
    governance_state: str
    health_status: StrategyHealthStatus
    health_reason: str | None
    latest_quality_score: float | None
    quality_score_trend: str | None
    consecutive_decline_count: int
    realized_drawdown: float | None
    last_health_evaluated_at: datetime | None
    # Lifecycle fields (populated when StrategyHealthLifecycleService has run)
    allocation_penalty: float | None = None
    operator_review_required: bool = False
    suspended_at: datetime | None = None
