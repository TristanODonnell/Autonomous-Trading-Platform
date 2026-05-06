# autonomous_trading_platform/portfolio/models.py

from __future__ import annotations

from pydantic import BaseModel

from autonomous_trading_platform.governance.models.governance_state import GovernanceState


class AllocationResult(BaseModel):
    """
    The resolved allocation for a single strategy after applying the active
    policy and any active override.

    This is the output of PortfolioEngine.get_allocation() and is what the
    execution layer consumes to know how much capital a strategy may use.
    """

    strategy_id: str
    approval_status: GovernanceState
    max_pct_of_capital: float
    max_position_size_usd: float | None
    max_drawdown_allowed: float
    allocated_capital_usd: float
    override_applied: bool
    override_id: str | None = None
    policy_id: str


class PromotionEligibility(BaseModel):
    """
    Result of checking whether a strategy is eligible for promotion
    to the next governance state.
    """

    strategy_id: str
    from_state: GovernanceState
    to_state: GovernanceState
    is_eligible: bool

    # Per-criterion results — key is the rule field name, value is pass/fail + detail
    criteria_results: dict[str, CriterionResult]

    @property
    def failed_criteria(self) -> list[str]:
        return [k for k, v in self.criteria_results.items() if not v.passed]


class CriterionResult(BaseModel):
    """Pass/fail result for a single promotion criterion."""

    passed: bool
    required: float | int | None  # threshold value from the rule
    actual: float | int | None  # value from the strategy's metrics
    detail: str  # human-readable explanation
