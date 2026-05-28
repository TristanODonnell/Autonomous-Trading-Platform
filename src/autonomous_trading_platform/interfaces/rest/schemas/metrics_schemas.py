from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class MetricLineageMetadataResponse(BaseModel):
    metric_lineage_type: str
    environment: str | None
    calculation_version: str | None
    generated_at: datetime
    lookback_window_days: int | None
    source_run_ids: list[str]
    source_strategy_ids: list[str]


class ResearchMetricsResponse(BaseModel):
    metrics_snapshot_id: str
    strategy_id: str | None
    run_id: str
    created_at: datetime
    metric_lineage_type: str
    lineage: MetricLineageMetadataResponse

    total_return: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None
    trade_count: int | None
    winning_trade_count: int | None
    losing_trade_count: int | None
    volatility: float | None
    win_rate: float | None
    metrics_json: dict[str, Any] | None


class LiveMetricsResponse(BaseModel):
    snapshot_id: str
    strategy_id: str
    run_id: str | None
    computed_at: datetime
    metric_lineage_type: str
    lineage: MetricLineageMetadataResponse

    window_days: int | None
    window_trades: int | None
    realized_return: float | None
    rolling_sharpe: float | None
    realized_drawdown: float | None
    realized_volatility: float | None
    live_win_rate: float | None
    trade_count: int | None
    winning_trade_count: int | None
    days_live: int | None
    days_since_profitable_day: int | None


class BlendedMetricsResponse(BaseModel):
    blended_snapshot_id: str
    strategy_id: str
    computed_at: datetime
    rebalance_run_id: str | None
    metric_lineage_type: str
    lineage: MetricLineageMetadataResponse

    alpha: float
    days_live: int | None
    trade_count: int | None
    research_score: float
    live_score: float
    blended_score: float
    research_metrics_snapshot_id: str | None
    live_metrics_snapshot_id: str | None


class BlendedMetricsHistoryResponse(BaseModel):
    strategy_id: str
    snapshots: list[BlendedMetricsResponse]
    total: int


class MetricLineageSummaryResponse(BaseModel):
    strategy_id: str
    has_research_metrics: bool
    has_live_metrics: bool
    has_blended_metrics: bool
    current_alpha: float | None
    current_blended_score: float | None
    research_metric_lineage_type: str | None
    live_metric_lineage_type: str | None
    metric_lineage_type_answer: str
