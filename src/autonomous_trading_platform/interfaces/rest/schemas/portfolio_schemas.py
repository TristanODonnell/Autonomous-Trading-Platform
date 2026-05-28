from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class PortfolioSummaryResponse(BaseModel):
    current_portfolio_value: Decimal
    todays_pnl_amount: Decimal
    todays_pnl_percent: Decimal
    total_pnl_amount: Decimal
    total_pnl_percent: Decimal
    cash_balance: Decimal


PortfolioEquityCurvePeriod = Literal["today", "1w", "1m", "3m", "ytd"]


class PortfolioEquityCurvePoint(BaseModel):
    timestamp: datetime
    value: Decimal


class PortfolioDrawdownPoint(BaseModel):
    timestamp: datetime
    value: Decimal


class PortfolioEquityCurveResponse(BaseModel):
    period: PortfolioEquityCurvePeriod
    points: list[PortfolioEquityCurvePoint]
    drawdown_points: list[PortfolioDrawdownPoint] = []


class PortfolioPerformanceResponse(BaseModel):
    total_return: Decimal
    cagr: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    max_drawdown: Decimal
    volatility: Decimal
    win_rate: Decimal


class PortfolioHoldingResponse(BaseModel):
    symbol: str
    company_name: str
    market_value: Decimal
    quantity: Decimal
    average_entry_price: Decimal
    current_price: Decimal
    todays_change_percent: Decimal
    todays_change_absolute: Decimal
    strategy_id: str


class PortfolioHoldingsResponse(BaseModel):
    holdings: list[PortfolioHoldingResponse]


class PortfolioAllocationItemResponse(BaseModel):
    name: str
    allocated_capital: Decimal
    percent_of_portfolio: Decimal


class PortfolioAllocationResponse(BaseModel):
    by_strategy: list[PortfolioAllocationItemResponse]
    by_asset: list[PortfolioAllocationItemResponse]


class PortfolioRiskResponse(BaseModel):
    portfolio_volatility: Decimal
    beta: Decimal
    value_at_risk_1d_95: Decimal
    average_pairwise_correlation: Decimal
    current_drawdown: Decimal


PortfolioPerformancePeriod = Literal["1M", "3M", "6M", "YTD", "1Y"]


class PortfolioPeriodReturnResponse(BaseModel):
    period: PortfolioPerformancePeriod
    return_percent: Decimal


class PortfolioPerformanceByPeriodResponse(BaseModel):
    periods: list[PortfolioPeriodReturnResponse]


class FactorExposureSnapshotResponse(BaseModel):
    snapshot_id: str
    run_id: str | None
    portfolio_id: str | None
    computed_at: datetime
    as_of_date: datetime
    lookback_window: int
    benchmark_symbol: str
    benchmark_source: str
    factor_computation_version: str
    portfolio_exposures: dict
    strategy_exposures: list
    symbol_exposures: list
    sector_exposures: dict
    concentration_diagnostics: list
    warnings: list
    factor_methodology: dict
    data_lineage: dict
    duration_seconds: float


class FactorExposureHistoryResponse(BaseModel):
    snapshots: list[FactorExposureSnapshotResponse]


class StrategyFactorExposureHistoryResponse(BaseModel):
    exposures: list[dict]
