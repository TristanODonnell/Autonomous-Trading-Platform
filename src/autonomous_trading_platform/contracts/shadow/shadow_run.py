from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class ShadowModeType(enum.StrEnum):
    OBSERVE_ONLY = "observe_only"
    VALIDATION_REQUIRED = "validation_required"
    CANARY_SHADOW = "canary_shadow"
    OPTIMIZER_SHADOW = "optimizer_shadow"
    PROMOTION_SHADOW = "promotion_shadow"
    PRODUCTION_DRIFT_MONITORING = "production_drift_monitoring"


class ShadowRunStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ShadowValidationStatus(enum.StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class ShadowRunRequest(BaseModel):
    shadow_run_id: UUID | None = None
    live_run_id: UUID | None = None
    simulation_run_id: UUID
    shadow_mode_type: ShadowModeType = ShadowModeType.OBSERVE_ONLY
    strategy_id: str
    covariance_snapshot_ref: str | None = None
    factor_snapshot_ref: str | None = None
    allocation_config_hash: str | None = None
    optimizer_run_ids: list[str] = Field(default_factory=list)
    required_passing_cycles: int = 5
    metadata: dict[str, Any] | None = None


class ShadowRunManifest(BaseModel):
    shadow_run_id: UUID
    live_run_id: UUID | None = None
    simulation_run_id: UUID
    shadow_mode_type: ShadowModeType
    strategy_id: str
    status: ShadowRunStatus
    validation_status: ShadowValidationStatus
    total_divergences: int
    threshold_exceedances: int
    promotion_eligible: bool
    config_hash: str | None = None
    covariance_snapshot_ref: str | None = None
    factor_snapshot_ref: str | None = None
    allocation_config_hash: str | None = None
    optimizer_run_ids: list[str] = Field(default_factory=list)
    started_at: UTCDateTime
    completed_at: UTCDateTime | None = None
    duration_seconds: float | None = None
    metadata: dict[str, Any] | None = None
