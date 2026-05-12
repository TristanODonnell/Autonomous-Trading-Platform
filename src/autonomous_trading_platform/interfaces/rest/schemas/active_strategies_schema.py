from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActiveStrategyResponse(BaseModel):
    strategy_id: str
    display_name: str
    strategy_type: str
    status: Literal["live", "paper", "off"]
    todays_return: Decimal
    trade_count_today: int
    allocated_capital: Decimal
    enabled: bool


class ActiveStrategiesResponse(BaseModel):
    strategies: list[ActiveStrategyResponse]


class StrategyAllocationUpdateRequest(BaseModel):
    allocation_pct: Decimal = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=500)


class StrategyAllocationUpdateResponse(BaseModel):
    strategy_id: str
    allocation_pct: Decimal
    allocated_capital: Decimal
    total_portfolio_capital: Decimal
    reason: str
    updated_by: str
    updated_at: datetime


class StrategyAllocationStateResponse(BaseModel):
    strategy_id: str
    display_name: str
    allocation_pct: Decimal | None
    allocated_capital: Decimal | None
    total_portfolio_capital: Decimal
    is_overridden: bool
    overridden_by: str | None
    reason: str | None
    updated_at: datetime | None


class StrategyAllocationsListResponse(BaseModel):
    strategies: list[StrategyAllocationStateResponse]


class StrategyEnabledUpdateRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)


class StrategyEnabledUpdateResponse(BaseModel):
    strategy_id: str
    enabled: bool
    status: Literal["live", "paper", "off"]
    reason: str
    updated_by: str
    updated_at: datetime


class StrategyGovernanceTransitionRequest(BaseModel):
    to_state: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class StrategyGovernanceTransitionResponse(BaseModel):
    strategy_id: str
    from_state: str
    to_state: str
    reason: str
    updated_by: str
    updated_at: datetime


StrategyStatus = Literal["live", "paper", "research", "off"]


class StrategyListItemResponse(BaseModel):
    strategy_id: str
    display_name: str
    strategy_type: str
    status: StrategyStatus
    current_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    composite_score: float


class StrategyListResponse(BaseModel):
    strategies: list[StrategyListItemResponse]


class StrategyMetricsResponse(BaseModel):
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    consistency_score: float


class StrategyDeploymentHistoryItemResponse(BaseModel):
    from_state: str | None
    to_state: str | None
    transitioned_at: datetime
    reason: str | None
    updated_by: str | None


class StrategyDetailResponse(StrategyListItemResponse):
    approval_status: str
    configuration_summary: str
    configuration: dict[str, Any]
    metrics: StrategyMetricsResponse
    deployment_history: list[StrategyDeploymentHistoryItemResponse]


class StrategyCompareRequest(BaseModel):
    strategy_ids: list[str] = Field(min_length=2, max_length=5)


class StrategyComparisonRowResponse(BaseModel):
    strategy_id: str
    display_name: str
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    consistency_score: float


class StrategyComparisonMetricMetadataResponse(BaseModel):
    best_strategy_id: str
    worst_strategy_id: str


class StrategyCompareResponse(BaseModel):
    rows: list[StrategyComparisonRowResponse]
    metadata: dict[str, StrategyComparisonMetricMetadataResponse]


class StrategyEquityCurvePointResponse(BaseModel):
    timestamp: datetime
    value: float
    drawdown: float


class StrategyEquityCurveResponse(BaseModel):
    strategy_id: str
    run_id: str | None
    points: list[StrategyEquityCurvePointResponse]


StrategyType = Literal["momentum", "mean_reversion", "breakout", "pairs"]
RiskLevel = Literal["low", "medium", "high"]
TimeHorizon = Literal["1w", "1m", "3m", "1y"]

ExperimentType = Literal["backtest", "parameter_sweep", "ab_comparison", "rolling_window"]
ExperimentStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class ExperimentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    experiment_type: ExperimentType
    symbols: list[str] = Field(min_length=1, max_length=50)
    start_date: str
    end_date: str
    price_basis: Literal["RAW", "ADJUSTED"] = "RAW"
    strategy_count: int = Field(default=100, ge=1, le=10000)
    parameter_ranges: dict[str, Any] = Field(default_factory=dict)


class ExperimentCreateResponse(BaseModel):
    experiment_id: str
    status: ExperimentStatus


class ExperimentListItemResponse(BaseModel):
    experiment_id: str
    experiment_name: str
    experiment_type: ExperimentType
    status: ExperimentStatus
    created_at: datetime
    symbols: list[str]
    start_date: str | None
    end_date: str | None
    dataset_version: str | None
    total_strategies: int
    strategies_passed_filters: int
    best_sharpe: float | None
    best_return: float | None
    progress: int | None


class ExperimentListResponse(BaseModel):
    experiments: list[ExperimentListItemResponse]


class ExperimentStrategyItemResponse(BaseModel):
    strategy_id: str
    sharpe_ratio: float
    total_return: float
    max_drawdown: float
    simulation_stage: str | None
    governance_state: str
    composite_score: float
    status: str


class ExperimentStrategiesResponse(BaseModel):
    experiment_id: str
    strategies: list[ExperimentStrategyItemResponse]


class ExperimentDetailResponse(ExperimentListItemResponse):
    strategies: list[ExperimentStrategyItemResponse]
    parameter_ranges: dict[str, Any] | None
    price_basis: str | None
