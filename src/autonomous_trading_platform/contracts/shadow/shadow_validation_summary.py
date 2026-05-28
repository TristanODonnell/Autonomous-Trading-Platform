from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.shadow.comparison_results import (
    AllocationComparisonResult,
    ExecutionComparisonResult,
    FeatureComparisonResult,
    OptimizerComparisonResult,
    OutcomeComparisonResult,
    RiskComparisonResult,
    RuntimeComparisonResult,
    SignalComparisonResult,
)
from autonomous_trading_platform.contracts.shadow.shadow_run import (
    ShadowModeType,
    ShadowValidationStatus,
)


@dataclass(frozen=True)
class ShadowValidationSummary:
    shadow_run_id: UUID
    simulation_run_id: UUID
    shadow_mode_type: ShadowModeType
    strategy_id: str
    validation_status: ShadowValidationStatus
    total_divergences: int
    threshold_exceedances: int
    promotion_eligible: bool
    started_at: UTCDateTime
    divergence_by_category: dict[str, int] = field(default_factory=dict)
    signal_comparison: list[SignalComparisonResult] = field(default_factory=list)
    allocation_comparison: list[AllocationComparisonResult] = field(default_factory=list)
    risk_comparison: list[RiskComparisonResult] = field(default_factory=list)
    execution_comparison: list[ExecutionComparisonResult] = field(default_factory=list)
    feature_comparison: list[FeatureComparisonResult] = field(default_factory=list)
    optimizer_comparison: list[OptimizerComparisonResult] = field(default_factory=list)
    runtime_comparison: list[RuntimeComparisonResult] = field(default_factory=list)
    outcome_comparison: OutcomeComparisonResult | None = None
    live_run_id: UUID | None = None
    completed_at: UTCDateTime | None = None
    duration_seconds: float | None = None
