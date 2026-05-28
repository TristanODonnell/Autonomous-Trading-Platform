from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class DivergenceType(enum.StrEnum):
    EXPECTED_EXECUTION_DRIFT = "expected_execution_drift"
    DATA_DRIFT = "data_drift"
    FEATURE_DRIFT = "feature_drift"
    ALLOCATION_DRIFT = "allocation_drift"
    OPTIMIZER_DRIFT = "optimizer_drift"
    COVARIANCE_INSTABILITY = "covariance_instability"
    ORCHESTRATION_TIMING_DRIFT = "orchestration_timing_drift"
    EXECUTION_DEGRADATION = "execution_degradation"
    RISK_DIVERGENCE = "risk_divergence"
    SIGNAL_DRIFT = "signal_drift"
    OUTCOME_DIVERGENCE = "outcome_divergence"


class DivergenceCategory(enum.StrEnum):
    SIGNALS = "signals"
    FEATURES = "features"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    EXECUTION = "execution"
    RUNTIME = "runtime"
    OUTCOMES = "outcomes"
    OPTIMIZER = "optimizer"


@dataclass(frozen=True)
class DivergenceThresholds:
    max_weight_drift_pct: float = 0.02
    max_beta_drift: float = 0.10
    max_execution_slippage_bps: float = 15.0
    max_signal_timing_drift_seconds: float = 5.0
    max_feature_drift_pct: float = 0.05
    max_factor_exposure_drift: float = 0.05
    max_optimizer_weight_drift: float = 0.02
    max_orchestration_timing_drift_seconds: float = 30.0
    max_sharpe_drift: float = 0.20
    max_sector_exposure_drift: float = 0.03
    max_covariance_frobenius_delta: float = 0.10


@dataclass(frozen=True)
class DivergenceRecord:
    divergence_id: UUID
    shadow_run_id: UUID
    timestamp: UTCDateTime
    divergence_type: DivergenceType
    category: DivergenceCategory
    metric_name: str
    divergence_magnitude: float
    threshold: float
    exceeds_threshold: bool
    symbol: str | None = None
    strategy_id: str | None = None
    sim_value: float | None = None
    live_value: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
