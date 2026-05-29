from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class HealthLifecycleConfig:
    """
    All configurable thresholds, anti-flapping windows, and allocation penalties
    for StrategyHealthLifecycleService.

    Governance state (approved/rejected/retired) is never read or written here.
    This config governs only operational health classification.
    """

    lifecycle_enabled: bool = True
    # observe: log only; alert: audit + log; enforce: mutate state + block allocation
    mode: str = "observe"

    # -----------------------------------------------------------------------
    # Watch thresholds  (early warning — metrics declining but not yet broken)
    # -----------------------------------------------------------------------
    watch_drawdown_utilization: float = 0.30  # fraction of max_drawdown consumed
    watch_quality_decline_cycles: int = 3  # consecutive quality score declines
    watch_sharpe_threshold: float = 0.50  # rolling Sharpe below this
    watch_quality_score_threshold: float = 0.55
    watch_win_rate_threshold: float = 0.45
    watch_consecutive_negative_periods: int = 3  # consecutive loss periods

    # -----------------------------------------------------------------------
    # Degrading thresholds  (sustained deterioration)
    # -----------------------------------------------------------------------
    degrading_drawdown_utilization: float = 0.55
    degrading_quality_decline_cycles: int = 5
    degrading_sharpe_threshold: float = 0.00
    degrading_quality_score_threshold: float = 0.40
    degrading_win_rate_threshold: float = 0.38
    degrading_consecutive_negative_periods: int = 5

    # -----------------------------------------------------------------------
    # Critical thresholds  (severe deterioration, near operational limits)
    # -----------------------------------------------------------------------
    critical_drawdown_utilization: float = 0.85
    critical_quality_decline_cycles: int = 8
    critical_sharpe_threshold: float = -0.50
    critical_quality_score_threshold: float = 0.25
    critical_win_rate_threshold: float = 0.28
    critical_consecutive_negative_periods: int = 7

    # -----------------------------------------------------------------------
    # Suspension (operational risk containment, not governance revocation)
    # -----------------------------------------------------------------------
    auto_suspend_enabled: bool = False
    # How many consecutive CRITICAL evaluation cycles before auto-suspend fires
    suspended_consecutive_critical_cycles: int = 3

    # -----------------------------------------------------------------------
    # Anti-flapping
    # -----------------------------------------------------------------------
    # Minimum evaluation cycles before any escalation from HEALTHY is allowed
    min_observation_cycles: int = 3
    # Cooldown windows: after transitioning TO a state, recovery is blocked for N hours
    cooldown_after_watch_hours: float = 6.0
    cooldown_after_degrading_hours: float = 12.0
    cooldown_after_critical_hours: float = 24.0
    cooldown_after_suspension_hours: float = 48.0

    # -----------------------------------------------------------------------
    # Allocation penalties (fraction of allocation to REMOVE, 0.0–1.0)
    # healthy → 0.0 (no impact)
    # watch   → 0.20 (mild reduction)
    # degrading → 0.50
    # critical  → 0.80
    # suspended → 1.0 (zero allocation)
    # -----------------------------------------------------------------------
    watch_allocation_penalty: float = 0.20
    degrading_allocation_penalty: float = 0.50
    critical_allocation_penalty: float = 0.80
    suspended_allocation_penalty: float = 1.00

    # -----------------------------------------------------------------------
    # Optional governance demotion hooks (must be explicitly enabled)
    # -----------------------------------------------------------------------
    auto_demote_on_critical: bool = False
    auto_demote_after_suspended_cycles: int = 5


@dataclass(frozen=True)
class LifecycleEvalMetrics:
    """Raw metrics collected for a single strategy evaluation."""

    quality_score: float | None
    consecutive_quality_decline: int
    consecutive_negative_periods: int  # consecutive cycles below quality threshold
    rolling_sharpe: float | None
    win_rate: float | None
    realized_drawdown: float | None
    max_drawdown_allowed: float | None
    drawdown_utilization: float | None  # realized_drawdown / max_drawdown_allowed


@dataclass(frozen=True)
class LifecycleTransitionEvent:
    """Single state transition record (for API and persistence)."""

    transition_id: str
    strategy_id: str
    from_status: str | None
    to_status: str
    transition_reason: str
    triggered_by: str  # "system" or "operator:<actor>"
    triggering_metrics: dict[str, Any] | None
    drawdown_utilization: float | None
    quality_score: float | None
    allocation_penalty_after: float
    cooldown_expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class LifecycleStrategyResult:
    """Evaluation outcome for a single strategy."""

    strategy_id: str
    governance_state: str
    previous_health_status: str | None
    new_health_status: str
    transition_reason: str
    did_transition: bool
    transition_blocked_by_cooldown: bool
    allocation_penalty: float
    allocation_scalar: float  # 1.0 - allocation_penalty
    consecutive_critical_count: int
    cooldown_expires_at: datetime | None
    operator_review_required: bool
    auto_suspended: bool
    skipped: bool
    skip_reason: str | None
    eval_metrics: LifecycleEvalMetrics | None
    evaluated_at: datetime


@dataclass(frozen=True)
class LifecycleRunResult:
    """Outcome of a full run_strategy_health_lifecycle_cycle call."""

    run_id: str
    mode: str
    lifecycle_enabled: bool
    strategies_evaluated: int
    transitions: list[dict[str, Any]]
    alerts_emitted: int
    suspensions: int
    skipped_count: int
    results: list[LifecycleStrategyResult]
    audit_event_type: str
    duration_seconds: float


class StrategyHealthLifecycleSummary(BaseModel):
    """Per-strategy lifecycle summary for API responses."""

    strategy_id: str
    governance_state: str
    health_status: str
    health_reason: str | None
    allocation_penalty: float
    allocation_scalar: float
    operator_review_required: bool
    suspended_at: datetime | None
    suspension_reason: str | None
    consecutive_critical_count: int
    cooldown_expires_at: datetime | None
    last_transition_at: datetime | None
    last_evaluated_at: datetime | None
    latest_quality_score: float | None
    realized_drawdown: float | None


class StrategyHealthTransitionRecord(BaseModel):
    """Single transition record for audit trail API responses."""

    transition_id: str
    strategy_id: str
    from_status: str | None
    to_status: str
    transition_reason: str
    triggered_by: str
    drawdown_utilization: float | None
    quality_score: float | None
    allocation_penalty_after: float
    created_at: datetime
