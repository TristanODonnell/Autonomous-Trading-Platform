from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class AllocationMode(StrEnum):
    EQUAL_CAPITAL = "equal_capital"
    EQUAL_RISK_CONTRIBUTION = "equal_risk_contribution"
    FIXED_RISK_BUDGETS = "fixed_risk_budgets"
    INVERSE_VOLATILITY = "inverse_volatility"


class FallbackReason(StrEnum):
    NO_COVARIANCE_AVAILABLE = "no_covariance_available"
    UNSTABLE_COVARIANCE = "unstable_covariance"
    SINGULAR_COVARIANCE = "singular_covariance"
    INSUFFICIENT_HISTORY = "insufficient_history"
    OPTIMIZATION_FAILED = "optimization_failed"
    NOT_POSITIVE_DEFINITE = "not_positive_definite"


class StrategyRiskContribution(BaseModel):
    strategy_id: str
    capital_weight: float
    marginal_risk_contribution: float
    total_risk_contribution: float
    pct_of_portfolio_variance: float
    realized_volatility: float | None = None
    target_risk_budget: float
    risk_budget_utilization: float


class RiskBudgetingRunResult(BaseModel):
    run_id: str
    computed_at: datetime
    mode: AllocationMode
    strategy_count: int
    portfolio_volatility_estimate: float | None
    diversification_ratio: float | None
    risk_contributions: list[StrategyRiskContribution]
    recommended_weights: dict[str, float]
    covariance_snapshot_id: str | None
    covariance_lookback_window: int | None
    fallback_used: bool
    fallback_reason: FallbackReason | None
    iterations_to_convergence: int | None
    convergence_achieved: bool
    duration_seconds: float
    warnings: list[str]

    @property
    def max_risk_concentration(self) -> float | None:
        if not self.risk_contributions:
            return None
        return max(rc.pct_of_portfolio_variance for rc in self.risk_contributions)

    @property
    def hidden_concentration_detected(self) -> bool:
        if not self.risk_contributions:
            return False
        n = len(self.risk_contributions)
        equal_share = 1.0 / n if n > 0 else 1.0
        return any(
            rc.pct_of_portfolio_variance > equal_share * 2.0 for rc in self.risk_contributions
        )


class RiskBudgetSnapshotRecord(BaseModel):
    snapshot_id: str
    run_id: str | None
    computed_at: datetime
    mode: AllocationMode
    strategy_universe: list[str]
    strategy_count: int
    target_risk_budgets: dict[str, float]
    recommended_weights: dict[str, float]
    realized_risk_contributions: dict[str, float]
    portfolio_volatility_estimate: float | None
    diversification_ratio: float | None
    covariance_snapshot_id: str | None
    covariance_lookback_window: int | None
    fallback_used: bool
    fallback_reason: str | None
    convergence_achieved: bool
    iterations_to_convergence: int | None
    hidden_concentration_detected: bool
    warnings: list[str]
    duration_seconds: float
    metadata: dict[str, Any]
