from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class CorrelationSnapshotType(StrEnum):
    SYMBOL = "symbol"
    STRATEGY = "strategy"
    SECTOR = "sector"


class CorrelatedPair(BaseModel):
    asset_a: str
    asset_b: str
    correlation: float
    abs_correlation: float


class ClusterInfo(BaseModel):
    cluster_id: int
    members: list[str]
    avg_intra_cluster_correlation: float


class CorrelationSnapshotRecord(BaseModel):
    snapshot_id: str
    snapshot_type: CorrelationSnapshotType
    lookback_window: int
    computed_at: datetime
    as_of_date: datetime
    asset_universe: list[str]
    regime_label: str | None = None
    asset_count: int
    observations_used: int
    average_pairwise_correlation: float
    max_pairwise_correlation: float
    min_pairwise_correlation: float
    correlation_matrix: dict[str, Any]
    top_correlated_pairs: list[CorrelatedPair]
    clusters: list[ClusterInfo]
    cluster_count: int
    is_numerically_stable: bool
    condition_number: float | None = None
    warnings: list[str]
    computation_duration_seconds: float
    run_id: str | None = None


class CovarianceSnapshotRecord(BaseModel):
    snapshot_id: str
    snapshot_type: CorrelationSnapshotType
    lookback_window: int
    computed_at: datetime
    as_of_date: datetime
    asset_universe: list[str]
    asset_count: int
    observations_used: int
    annualization_factor: int
    covariance_matrix: dict[str, Any]
    matrix_hash: str | None = None
    is_positive_definite: bool
    condition_number: float | None = None
    warnings: list[str]
    computation_duration_seconds: float
    run_id: str | None = None


class CorrelationMonitoringRunResult(BaseModel):
    run_id: str
    computed_at: datetime
    snapshots_persisted: int
    symbol_snapshot_count: int
    strategy_snapshot_count: int
    sector_snapshot_count: int
    covariance_snapshot_count: int
    duration_seconds: float
    top_correlated_symbol_pairs: list[CorrelatedPair]
    top_correlated_strategy_pairs: list[CorrelatedPair]
    hidden_cluster_detected: bool
    cluster_count: int
    average_portfolio_correlation: float | None
    max_strategy_correlation: float | None
    max_symbol_correlation: float | None
    warnings: list[str]
