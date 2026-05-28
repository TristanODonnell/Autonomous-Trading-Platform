from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class FactorNeutralizationMode(StrEnum):
    OBSERVE_ONLY = "observe_only"
    SOFT_PENALTY = "soft_penalty"
    HARD_CONSTRAINT = "hard_constraint"


class FactorConstraintType(StrEnum):
    TARGET_RANGE = "target_range"
    MAX_ABS_EXPOSURE = "max_abs_exposure"
    SECTOR_CAP = "sector_cap"


class FactorNeutralizationStatus(StrEnum):
    APPLIED = "applied"
    OBSERVED = "observed"
    INFEASIBLE = "infeasible"
    FALLBACK_USED = "fallback_used"
    DISABLED = "disabled"


class FactorNeutralizationConstraint(BaseModel):
    factor_name: str
    constraint_type: FactorConstraintType
    target_min: float | None = None
    target_max: float | None = None
    max_abs_exposure: float | None = None
    penalty_weight: float = 1.0
    hard_limit: bool = False


class FactorNeutralizationConfigPayload(BaseModel):
    enabled: bool
    mode: FactorNeutralizationMode
    constraints: list[FactorNeutralizationConstraint]
    max_iterations: int
    convergence_tolerance: float
    max_turnover: float | None = None


class FactorNeutralizationRequest(BaseModel):
    assets: list[str]
    current_weights: dict[str, float]
    factor_exposures: dict[str, dict[str, float]]
    sector_map: dict[str, str] = {}
    expected_returns: dict[str, float] = {}
    factor_snapshot_id: str | None = None
    covariance_snapshot_id: str | None = None
    optimization_run_id: str | None = None
    portfolio_id: str | None = None


class FactorExposureDecomposition(BaseModel):
    pre_neutralization: dict[str, float | None]
    post_neutralization: dict[str, float | None]
    exposure_reduction: dict[str, float | None]
    residual_exposure: dict[str, float | None]
    constraint_utilization: dict[str, float | None]


class FactorNeutralizationResult(BaseModel):
    run_id: str
    generated_at: datetime
    status: FactorNeutralizationStatus
    mode: FactorNeutralizationMode
    portfolio_id: str | None
    factor_snapshot_id: str | None
    covariance_snapshot_id: str | None
    optimization_run_id: str | None
    target_weights: dict[str, float]
    original_weights: dict[str, float]
    decomposition: FactorExposureDecomposition
    binding_constraints: list[str]
    constraint_violations: list[dict[str, Any]]
    fallback_mode: str | None
    infeasibility_reason: str | None
    duration_seconds: float
    warnings: list[str]
    metadata: dict[str, Any] = {}
