from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class FactorName(StrEnum):
    MARKET_BETA = "market_beta"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    SECTOR = "sector"
    SIZE = "size"
    QUALITY = "quality"
    VALUE = "value"


class FactorExposureLevel(StrEnum):
    SYMBOL = "symbol"
    STRATEGY = "strategy"
    SECTOR = "sector"
    PORTFOLIO = "portfolio"


class FactorExposureInputPosition(BaseModel):
    symbol: str
    weight: float
    strategy_id: str | None = None
    sector: str | None = None
    market_cap: float | None = None
    quality_score: float | None = None
    value_score: float | None = None


class FactorExposureValue(BaseModel):
    factor_name: FactorName
    exposure: float | None
    methodology: str
    observations_used: int
    is_valid: bool
    warnings: list[str] = []


class SymbolFactorExposure(BaseModel):
    symbol: str
    strategy_id: str | None
    sector: str | None
    weight: float
    exposures: dict[str, FactorExposureValue]


class StrategyFactorExposure(BaseModel):
    strategy_id: str
    strategy_weight: float
    symbol_count: int
    exposures: dict[str, float | None]
    top_symbol_contributors: dict[str, list[dict[str, Any]]]


class PortfolioFactorExposure(BaseModel):
    portfolio_id: str | None
    total_weight: float
    symbol_count: int
    strategy_count: int
    exposures: dict[str, float | None]
    sector_exposures: dict[str, float]
    concentration_diagnostics: list[dict[str, Any]]


class FactorExposureSnapshotRecord(BaseModel):
    snapshot_id: str
    run_id: str | None
    portfolio_id: str | None
    computed_at: datetime
    as_of_date: datetime
    lookback_window: int
    benchmark_symbol: str
    benchmark_source: str
    factor_computation_version: str
    factor_methodology: dict[str, str]
    data_lineage: dict[str, Any]
    symbol_exposures: list[SymbolFactorExposure]
    strategy_exposures: list[StrategyFactorExposure]
    portfolio_exposure: PortfolioFactorExposure
    warnings: list[str]
    duration_seconds: float


class FactorExposureRunResult(BaseModel):
    run_id: str
    computed_at: datetime
    snapshots_persisted: int
    portfolio_id: str | None
    benchmark_symbol: str
    lookback_windows: list[int]
    portfolio_exposures: dict[int, dict[str, float | None]]
    concentration_alerts: list[dict[str, Any]]
    drift_alerts: list[dict[str, Any]]
    warnings: list[str]
    duration_seconds: float
