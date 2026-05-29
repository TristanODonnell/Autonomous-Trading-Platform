from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class DrawdownGovernanceState(StrEnum):
    """Ladder rungs — severity ascending."""

    NORMAL = "normal"
    WARNING = "warning"
    PROBATION = "probation"
    SUSPENDED = "suspended"
    BREACHED = "breached"


# Ordered severity for transition logic (higher = worse)
_LADDER_SEVERITY: dict[str, int] = {
    DrawdownGovernanceState.NORMAL: 0,
    DrawdownGovernanceState.WARNING: 1,
    DrawdownGovernanceState.PROBATION: 2,
    DrawdownGovernanceState.SUSPENDED: 3,
    DrawdownGovernanceState.BREACHED: 4,
}

_LADDER_STATE_BY_RANK: dict[int, str] = {v: k for k, v in _LADDER_SEVERITY.items()}


def ladder_severity(state: str) -> int:
    return _LADDER_SEVERITY.get(state, 0)


def ladder_state_one_step_better(state: str) -> str:
    """Return the rung one level below current (recovery is one step at a time)."""
    r = ladder_severity(state)
    return _LADDER_STATE_BY_RANK.get(max(0, r - 1), DrawdownGovernanceState.NORMAL)


# Allocation scalars applied at each rung
LADDER_ALLOCATION_SCALARS: dict[str, float] = {
    DrawdownGovernanceState.NORMAL: 1.00,
    DrawdownGovernanceState.WARNING: 0.50,
    DrawdownGovernanceState.PROBATION: 0.25,
    DrawdownGovernanceState.SUSPENDED: 0.00,
    DrawdownGovernanceState.BREACHED: 0.00,
}

# Health-status suggestions mapped from ladder state
# (advisory only — health lifecycle remains independent)
LADDER_HEALTH_SUGGESTIONS: dict[str, str] = {
    DrawdownGovernanceState.NORMAL: "healthy",
    DrawdownGovernanceState.WARNING: "watch",
    DrawdownGovernanceState.PROBATION: "degrading",
    DrawdownGovernanceState.SUSPENDED: "critical",
    DrawdownGovernanceState.BREACHED: "suspended",
}


@dataclass(frozen=True)
class DrawdownGovernanceLadderConfig:
    """
    Configurable thresholds and behaviour for the per-strategy drawdown
    governance ladder.  All threshold values are expressed as fractions of
    max_drawdown_allowed (i.e. drawdown_utilization).

    Allocation scalars define how much of the base allocation to preserve
    at each rung.  Hysteresis offsets prevent immediate recovery oscillation
    by requiring utilization to fall below (threshold − hysteresis) before
    a downward transition is allowed.
    """

    enabled: bool = True
    # observe: emit events only; enforce: mutate state and apply scalars
    mode: str = "observe"

    # -----------------------------------------------------------------------
    # Ladder thresholds (drawdown_utilization)
    # -----------------------------------------------------------------------
    warning_threshold: float = 0.50
    probation_threshold: float = 0.75
    suspension_threshold: float = 0.90
    breach_threshold: float = 1.00

    # -----------------------------------------------------------------------
    # Allocation scalars at each rung (0.0–1.0)
    # -----------------------------------------------------------------------
    normal_allocation_scalar: float = 1.00
    warning_allocation_scalar: float = 0.50
    probation_allocation_scalar: float = 0.25
    suspended_allocation_scalar: float = 0.00
    breached_allocation_scalar: float = 0.00

    # -----------------------------------------------------------------------
    # Hysteresis: recovery requires utilization < threshold - hysteresis_band
    # Prevents immediate re-escalation after a single good bar
    # -----------------------------------------------------------------------
    hysteresis_band: float = 0.05

    # -----------------------------------------------------------------------
    # Anti-flapping: cooldown after each rung change (hours)
    # -----------------------------------------------------------------------
    cooldown_after_warning_hours: float = 6.0
    cooldown_after_probation_hours: float = 12.0
    cooldown_after_suspension_hours: float = 24.0
    cooldown_after_breach_hours: float = 48.0

    # -----------------------------------------------------------------------
    # Minimum observation cycles before any escalation from NORMAL
    # -----------------------------------------------------------------------
    min_observation_cycles: int = 2

    # -----------------------------------------------------------------------
    # Governance integration
    # -----------------------------------------------------------------------
    # Require operator acknowledgement before recovering from BREACHED
    breach_requires_operator_ack: bool = True
    # Optionally trigger auto-demotion on BREACHED
    auto_demote_on_breach: bool = False

    def allocation_scalar_for(self, state: str) -> float:
        match state:
            case DrawdownGovernanceState.WARNING:
                return self.warning_allocation_scalar
            case DrawdownGovernanceState.PROBATION:
                return self.probation_allocation_scalar
            case DrawdownGovernanceState.SUSPENDED:
                return self.suspended_allocation_scalar
            case DrawdownGovernanceState.BREACHED:
                return self.breached_allocation_scalar
            case _:  # NORMAL
                return self.normal_allocation_scalar

    def cooldown_hours_for(self, state: str) -> float:
        match state:
            case DrawdownGovernanceState.WARNING:
                return self.cooldown_after_warning_hours
            case DrawdownGovernanceState.PROBATION:
                return self.cooldown_after_probation_hours
            case DrawdownGovernanceState.SUSPENDED:
                return self.cooldown_after_suspension_hours
            case DrawdownGovernanceState.BREACHED:
                return self.cooldown_after_breach_hours
            case _:
                return 0.0


@dataclass(frozen=True)
class DrawdownGovernanceLadderEvaluation:
    """Per-strategy evaluation result from one ladder evaluation cycle."""

    strategy_id: str

    realized_drawdown: float
    max_drawdown_allowed: float
    drawdown_utilization: float  # realized / max

    previous_state: str
    new_state: str

    allocation_scalar: float
    suggested_health_status: str  # advisory; health lifecycle remains independent

    did_transition: bool
    transition_reason: str
    transition_blocked_by_cooldown: bool

    cooldown_expires_at: datetime | None
    operator_ack_required: bool  # True if BREACHED + breach_requires_operator_ack

    # Governance integration
    auto_demotion_triggered: bool

    skipped: bool
    skip_reason: str | None

    evaluated_at: datetime


@dataclass(frozen=True)
class DrawdownGovernanceLadderRunResult:
    """Result of a full ladder evaluation run across all monitorable strategies."""

    run_id: str
    mode: str
    enabled: bool

    strategies_evaluated: int
    transitions: list[dict[str, Any]]
    warnings_triggered: int
    probations_triggered: int
    suspensions_triggered: int
    breaches_triggered: int
    recoveries_triggered: int
    skipped_count: int

    results: list[DrawdownGovernanceLadderEvaluation]
    duration_seconds: float


# ---------------------------------------------------------------------------
# Pydantic response models (API layer)
# ---------------------------------------------------------------------------


class DrawdownGovernanceLadderSummary(BaseModel):
    """Per-strategy ladder state for API responses."""

    strategy_id: str
    ladder_state: str
    drawdown_utilization: float | None
    realized_drawdown: float | None
    max_drawdown_allowed: float | None
    allocation_scalar: float
    suggested_health_status: str
    operator_ack_required: bool
    cooldown_expires_at: datetime | None
    last_transition_at: datetime | None
    last_evaluated_at: datetime | None
    evaluation_count: int


class DrawdownGovernanceLadderTransitionRecord(BaseModel):
    """Single transition record for the audit trail API."""

    transition_id: str
    strategy_id: str
    from_state: str | None
    to_state: str
    transition_reason: str
    triggered_by: str
    drawdown_utilization: float | None
    allocation_scalar_after: float | None
    cooldown_expires_at: datetime | None
    created_at: datetime


class DrawdownGovernanceLadderConfigResponse(BaseModel):
    """Serialised ladder configuration for operator introspection."""

    enabled: bool
    mode: str
    warning_threshold: float
    probation_threshold: float
    suspension_threshold: float
    breach_threshold: float
    normal_allocation_scalar: float
    warning_allocation_scalar: float
    probation_allocation_scalar: float
    suspended_allocation_scalar: float
    breached_allocation_scalar: float
    hysteresis_band: float
    breach_requires_operator_ack: bool
    auto_demote_on_breach: bool
