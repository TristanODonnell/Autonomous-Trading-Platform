from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from autonomous_trading_platform.contracts.shadow.shadow_run import (
    ShadowModeType,
)


class StartShadowRunRequest(BaseModel):
    simulation_run_id: UUID
    shadow_mode_type: ShadowModeType = ShadowModeType.OBSERVE_ONLY
    strategy_id: str
    live_run_id: UUID | None = None
    covariance_snapshot_ref: str | None = None
    factor_snapshot_ref: str | None = None
    allocation_config_hash: str | None = None
    optimizer_run_ids: list[str] = Field(default_factory=list)
    required_passing_cycles: int = 5
    metadata: dict[str, Any] | None = None


class ShadowRunManifestResponse(BaseModel):
    shadow_run_id: UUID
    live_run_id: UUID | None
    simulation_run_id: UUID
    strategy_id: str
    shadow_mode_type: str
    status: str
    validation_status: str
    total_divergences: int
    threshold_exceedances: int
    promotion_eligible: bool
    config_hash: str | None
    covariance_snapshot_ref: str | None
    factor_snapshot_ref: str | None
    allocation_config_hash: str | None
    optimizer_run_ids: list[str]
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: float | None


class ShadowValidationSummaryResponse(BaseModel):
    shadow_run_id: UUID
    live_run_id: UUID | None
    simulation_run_id: UUID
    strategy_id: str
    shadow_mode_type: str
    validation_status: str
    total_divergences: int
    threshold_exceedances: int
    promotion_eligible: bool
    divergence_by_category: dict[str, int]
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: float | None


class ShadowDivergenceResponse(BaseModel):
    divergence_id: str
    shadow_run_id: str
    timestamp: datetime
    divergence_type: str
    category: str
    metric_name: str
    symbol: str | None
    strategy_id: str | None
    sim_value: float | None
    live_value: float | None
    divergence_magnitude: float
    threshold: float
    exceeds_threshold: bool
    details: dict[str, Any]


class ShadowDivergenceListResponse(BaseModel):
    divergences: list[ShadowDivergenceResponse]
    total: int
    shadow_run_id: str


class ShadowRunListResponse(BaseModel):
    runs: list[ShadowRunManifestResponse]
    total: int


class PromotionEligibilityResponse(BaseModel):
    shadow_run_id: UUID
    strategy_id: str
    promotion_eligible: bool
    validation_status: str
    threshold_exceedances: int
