from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class OptimizationObjective(StrEnum):
    MINIMUM_VARIANCE = "minimum_variance"
    MAXIMUM_UTILITY = "maximum_utility"
    TARGET_RETURN = "target_return"
    TARGET_VOLATILITY = "target_volatility"


class SolverStatus(StrEnum):
    OPTIMAL = "optimal"
    SUBOPTIMAL = "suboptimal"
    INFEASIBLE = "infeasible"
    FALLBACK_USED = "fallback_used"
    FAILED = "failed"


class FallbackMode(StrEnum):
    NONE = "none"
    PREVIOUS_WEIGHTS = "previous_weights"
    EQUAL_WEIGHTS = "equal_weights"
    RISK_PARITY = "risk_parity"


class OptimizerConstraints(BaseModel):
    """Allocation and risk constraints for the MVO optimizer."""

    global_min_weight: float = 0.0
    global_max_weight: float = 1.0
    per_asset_min: dict[str, float] = {}
    per_asset_max: dict[str, float] = {}
    sector_caps: dict[str, float] = {}
    sector_map: dict[str, str] = {}
    max_turnover: float | None = None
    turnover_penalty: float = 0.0
    long_only: bool = True
    cash_reserve: float = 0.0


class OptimizationResult(BaseModel):
    run_id: str
    objective: OptimizationObjective
    solver_status: SolverStatus
    assets: list[str]
    target_weights: dict[str, float]
    current_weights: dict[str, float]
    expected_return: float | None = None
    expected_volatility: float | None = None
    objective_value: float | None = None
    realized_turnover: float | None = None
    constraints_applied: list[str]
    binding_constraints: list[str]
    infeasibility_reason: str | None = None
    fallback_mode: FallbackMode
    covariance_snapshot_id: str | None = None
    expected_return_source: str
    risk_aversion: float
    iterations_to_convergence: int | None = None
    convergence_achieved: bool
    generated_at: datetime
    duration_seconds: float
    dry_run: bool
    warnings: list[str]
    metadata: dict[str, Any] = {}
