from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.shadow.divergence import DivergenceRecord


@dataclass(frozen=True)
class SignalComparisonResult:
    shadow_run_id: UUID
    bar_timestamp: UTCDateTime
    symbol: str
    strategy_id: str
    direction_match: bool
    sim_direction: str | None = None
    live_direction: str | None = None
    sim_confidence: float | None = None
    live_confidence: float | None = None
    confidence_drift: float | None = None
    timing_drift_seconds: float | None = None
    divergences: list[DivergenceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class AllocationComparisonResult:
    shadow_run_id: UUID
    bar_timestamp: UTCDateTime
    symbol: str
    sim_weight: float
    live_weight: float
    weight_drift: float
    exceeds_threshold: bool
    divergences: list[DivergenceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class RiskComparisonResult:
    shadow_run_id: UUID
    bar_timestamp: UTCDateTime
    divergence_type: str
    sim_exposure: float | None = None
    live_exposure: float | None = None
    exposure_drift: float | None = None
    symbol: str | None = None
    sector: str | None = None
    factor: str | None = None
    divergences: list[DivergenceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionComparisonResult:
    shadow_run_id: UUID
    bar_timestamp: UTCDateTime
    symbol: str
    slippage_drift_bps: float | None = None
    latency_drift_ms: float | None = None
    sim_fill_price: float | None = None
    live_fill_price: float | None = None
    sim_qty: float | None = None
    live_qty: float | None = None
    order_rejected_sim: bool = False
    order_rejected_live: bool = False
    divergences: list[DivergenceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class FeatureComparisonResult:
    shadow_run_id: UUID
    bar_timestamp: UTCDateTime
    feature_name: str
    freshness_match: bool
    drift_pct: float | None = None
    sim_value: float | None = None
    live_value: float | None = None
    symbol: str | None = None
    divergences: list[DivergenceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class OptimizerComparisonResult:
    shadow_run_id: UUID
    bar_timestamp: UTCDateTime
    max_weight_drift: float
    constraint_activation_match: bool
    sim_weights: dict[str, float] = field(default_factory=dict)
    live_weights: dict[str, float] = field(default_factory=dict)
    covariance_frobenius_delta: float | None = None
    sim_objective_value: float | None = None
    live_objective_value: float | None = None
    sim_expected_volatility: float | None = None
    live_expected_volatility: float | None = None
    divergences: list[DivergenceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeComparisonResult:
    shadow_run_id: UUID
    bar_timestamp: UTCDateTime
    sequence_match: bool
    timing_drift_seconds: float | None = None
    sim_cycle_duration_seconds: float | None = None
    live_cycle_duration_seconds: float | None = None
    stale_data_detected: bool = False
    sync_mismatch: bool = False
    divergences: list[DivergenceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class OutcomeComparisonResult:
    shadow_run_id: UUID
    sim_total_return: float | None = None
    live_total_return: float | None = None
    sim_sharpe: float | None = None
    live_sharpe: float | None = None
    sim_max_drawdown: float | None = None
    live_max_drawdown: float | None = None
    sim_volatility: float | None = None
    live_volatility: float | None = None
    sim_turnover: float | None = None
    live_turnover: float | None = None
    pnl_drift: float | None = None
    sharpe_drift: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)
