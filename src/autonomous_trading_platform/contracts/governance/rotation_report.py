# autonomous_trading_platform/contracts/governance/rotation_report.py
"""
Rotation report (portfolio rotation step 5).

How a portfolio-mode backtest actually did: portfolio performance (from sleeves),
a buy-and-hold benchmark, how much the portfolio rotated (swaps, seat changes,
on-deck moves, admissions, retirements per month), turnover, and each strategy's
contribution and time in each tier.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class PerformanceMetrics(BaseModel):
    start_value: float
    end_value: float
    total_return: float
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    # Daily returns, risk-free rate 0, annualised with sqrt(252).
    sharpe: float | None = None
    # Largest peak-to-trough fall of the equity curve, as a positive fraction.
    max_drawdown: float
    trading_days: int


class RotationActivity(BaseModel):
    month: str  # YYYY-MM
    swaps: int = 0
    seats_added: int = 0
    seats_dropped: int = 0
    on_deck_promotions: int = 0
    on_deck_demotions: int = 0
    bench_admissions: int = 0
    retirements: int = 0
    governance_promotions: int = 0
    governance_rejections: int = 0


class StrategyContribution(BaseModel):
    strategy_id: str
    net_pnl: float
    # Share of the portfolio's total net P&L (None when that total is 0).
    pnl_share: float | None = None
    days_by_tier: dict[str, int] = {}
    final_tier: str | None = None


class RotationReport(BaseModel):
    start_date: date
    end_date: date
    starting_cash: float
    portfolio: PerformanceMetrics | None = None
    benchmark_symbol: str | None = None
    benchmark: PerformanceMetrics | None = None
    activity: list[RotationActivity] = []
    total_swaps: int = 0
    swaps_per_month: float = 0.0
    # Traded notional ÷ average portfolio equity over the run.
    turnover: float | None = None
    contributions: list[StrategyContribution] = []
    warnings: list[str] = []
