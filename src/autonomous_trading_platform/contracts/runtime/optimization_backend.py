from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class OptimizationBackendStatus(StrEnum):
    OPTIMAL = "optimal"
    SUBOPTIMAL = "suboptimal"
    INFEASIBLE = "infeasible"
    FALLBACK_USED = "fallback_used"
    FAILED = "failed"


class OptimizationBackendObjectiveType(StrEnum):
    MINIMIZE_VARIANCE = "minimize_variance"
    MAXIMIZE_EXPECTED_RETURN = "maximize_expected_return"
    MAXIMIZE_UTILITY = "maximize_utility"
    MINIMIZE_TRACKING_ERROR = "minimize_tracking_error"
    MINIMIZE_TURNOVER = "minimize_turnover"
    MINIMIZE_FACTOR_EXPOSURE = "minimize_factor_exposure"


class OptimizationBackendConstraintType(StrEnum):
    WEIGHTS_SUM_TO = "weights_sum_to"
    LONG_ONLY = "long_only"
    MIN_WEIGHT = "min_weight"
    MAX_WEIGHT = "max_weight"
    SECTOR_CAP = "sector_cap"
    TURNOVER_LIMIT = "turnover_limit"
    TARGET_RETURN = "target_return"
    FACTOR_EXPOSURE_BOUND = "factor_exposure_bound"
    CASH_RESERVE = "cash_reserve"


class OptimizationFallbackPolicy(StrEnum):
    NONE = "none"
    PREVIOUS_WEIGHTS = "previous_weights"
    EQUAL_WEIGHTS = "equal_weights"
    QUALITY_WEIGHTED = "quality_weighted"
    HOLD_CURRENT = "hold_current"


class OptimizationBackendConfig(BaseModel):
    solver_name: str = "CLARABEL"
    solver_timeout_seconds: float | None = 10.0
    tolerance: float = 1e-7
    max_iterations: int = 10_000
    fallback_solver_order: list[str] = ["CLARABEL", "OSQP", "SCS"]
    deterministic_seed: int | None = None
    fallback_policy: OptimizationFallbackPolicy = OptimizationFallbackPolicy.EQUAL_WEIGHTS
    covariance_regularization: float = 1e-8
    diagonal_jitter: float = 1e-8
    repair_psd: bool = True
    allow_numpy_fallback: bool = True


class OptimizationObjectiveTerm(BaseModel):
    objective_type: OptimizationBackendObjectiveType
    weight: float = 1.0
    risk_aversion: float = 1.0
    benchmark_weights: dict[str, float] = {}
    factor_exposures: dict[str, dict[str, float]] = {}


class OptimizationConstraintTerm(BaseModel):
    constraint_type: OptimizationBackendConstraintType
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    per_asset: dict[str, float] = {}
    sector_map: dict[str, str] = {}
    sector_caps: dict[str, float] = {}
    factor_name: str | None = None
    factor_exposures: dict[str, dict[str, float]] = {}


class OptimizationProblem(BaseModel):
    problem_type: str
    assets: list[str]
    covariance_matrix: dict[str, dict[str, float]] | None = None
    expected_returns: dict[str, float] = {}
    current_weights: dict[str, float] = {}
    quality_scores: dict[str, float] = {}
    objectives: list[OptimizationObjectiveTerm]
    constraints: list[OptimizationConstraintTerm]
    dry_run: bool = True
    covariance_snapshot_id: str | None = None
    factor_snapshot_id: str | None = None
    expected_return_source: str | None = None
    metadata: dict[str, Any] = {}


class OptimizationBackendResult(BaseModel):
    optimizer_run_id: str
    generated_at: datetime
    backend_name: str
    solver_used: str | None
    solver_status: OptimizationBackendStatus
    problem_type: str
    objective_summary: list[str]
    constraint_summary: list[str]
    assets: list[str]
    target_weights: dict[str, float]
    fallback_used: bool
    fallback_policy: OptimizationFallbackPolicy
    infeasibility_reason: str | None = None
    violated_constraints: list[str] = []
    binding_constraints: list[str] = []
    covariance_snapshot_id: str | None = None
    factor_snapshot_id: str | None = None
    expected_return_source: str | None = None
    input_hash: str
    repairs_applied: list[str] = []
    condition_number: float | None = None
    duration_seconds: float
    metadata: dict[str, Any] = {}
