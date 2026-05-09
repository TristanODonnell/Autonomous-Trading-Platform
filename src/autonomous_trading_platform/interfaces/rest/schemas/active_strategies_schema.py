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
    allocated_capital: Decimal = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class StrategyAllocationUpdateResponse(BaseModel):
    strategy_id: str
    allocated_capital: Decimal
    total_portfolio_capital: Decimal
    reason: str
    updated_by: str
    updated_at: datetime


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
ExperimentStatus = Literal["queued", "running", "complete", "failed"]


class ExperimentCreateRequest(BaseModel):
    strategy_type: StrategyType
    risk_level: RiskLevel
    time_horizon: TimeHorizon


class ExperimentCreateResponse(BaseModel):
    experiment_id: str
    status: ExperimentStatus


class ExperimentListItemResponse(BaseModel):
    experiment_id: str
    created_at: datetime
    status: ExperimentStatus
    strategy_type: str
    risk_level: str
    time_horizon: str
    result_summary: dict[str, Any] | None


class ExperimentListResponse(BaseModel):
    experiments: list[ExperimentListItemResponse]


class ExperimentRunResultResponse(BaseModel):
    run_id: str
    strategy_id: str
    status: str
    metrics: StrategyMetricsResponse
    composite_score: float


class ExperimentDetailResponse(ExperimentListItemResponse):
    mapping: dict[str, Any] | None
    simulation_results: list[ExperimentRunResultResponse]
    ranked_strategy_outputs: list[ExperimentRunResultResponse]
    filtering_summary: dict[str, int]
