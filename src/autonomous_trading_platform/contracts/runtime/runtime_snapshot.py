from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class PortfolioSummarySnapshot(BaseModel):
    current_portfolio_value: Decimal
    cash_balance: Decimal
    invested_capital: Decimal
    open_positions: int
    todays_pnl_amount: Decimal
    todays_pnl_percent: Decimal
    total_pnl_amount: Decimal
    total_pnl_percent: Decimal


class PortfolioHoldingEntry(BaseModel):
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    strategy_id: str


class PortfolioAllocationItem(BaseModel):
    name: str
    percent_of_portfolio: Decimal
    allocated_capital: Decimal


class PortfolioPeriodReturn(BaseModel):
    period: str
    return_percent: Decimal


class PortfolioPerformanceSnapshot(BaseModel):
    total_return: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    max_drawdown: Decimal
    volatility: Decimal
    by_period: list[PortfolioPeriodReturn] = []


class PortfolioRiskSnapshot(BaseModel):
    portfolio_volatility: Decimal
    beta: Decimal
    value_at_risk_1d_95: Decimal
    current_drawdown: Decimal
    average_pairwise_correlation: Decimal


class PortfolioSnapshot(BaseModel):
    summary: PortfolioSummarySnapshot | None = None
    holdings: list[PortfolioHoldingEntry] = []
    allocation_by_strategy: list[PortfolioAllocationItem] = []
    performance: PortfolioPerformanceSnapshot | None = None
    risk: PortfolioRiskSnapshot | None = None


class OperatorControlsSnapshot(BaseModel):
    trading_enabled: bool
    trading_paused: bool
    kill_switch_active: bool
    trading_mode: str


class OperatorSettingsSnapshot(BaseModel):
    max_portfolio_drawdown: float
    max_strategy_drawdown: float
    risk_tolerance: str
    per_strategy_cap: float
    target_portfolio_volatility: float
    min_sharpe_for_promotion: float
    min_paper_trading_period_days: int
    rebalance_frequency: str
    auto_demote_on_breach: bool
    auto_promote_enabled: bool
    slippage_model: str
    transaction_cost_model: str
    notify_drawdown_alerts: bool
    notify_strategy_promotion_events: bool
    notify_pipeline_failures: bool


class StrategyControlEntry(BaseModel):
    strategy_id: str
    enabled: bool
    pause_reason: str | None = None


class StrategyAllocationEntry(BaseModel):
    strategy_id: str
    override_active: bool
    override_amount: Decimal | None = None
    override_reason: str | None = None
    total_portfolio_capital: Decimal | None = None


class AggregateAllocationSnapshot(BaseModel):
    aggregate_allocation_pct: Decimal
    max_total_strategy_allocation_pct: Decimal
    remaining_allocation_capacity_pct: Decimal
    allocation_utilization: Decimal
    allocation_budget_exceeded: bool


class DatasetVersionEntry(BaseModel):
    dataset_name: str
    version_id: str | None = None


class RecentActivityEntry(BaseModel):
    timestamp: datetime
    action_type: str
    actor: str | None = None
    details: str | None = None


class ExperimentEntry(BaseModel):
    experiment_id: str
    experiment_name: str
    status: str
    created_at: datetime
    total_strategies: int = 0
    strategies_passed_filters: int = 0


class RuntimeSnapshot(BaseModel):
    snapshot_timestamp: datetime
    operator_controls: OperatorControlsSnapshot | None = None
    operator_settings: OperatorSettingsSnapshot | None = None
    strategy_controls: list[StrategyControlEntry] = []
    strategy_allocations: list[StrategyAllocationEntry] = []
    aggregate_allocation: AggregateAllocationSnapshot | None = None
    datasets: list[DatasetVersionEntry] = []
    recent_activity: list[RecentActivityEntry] = []
    experiments: list[ExperimentEntry] = []
    portfolio: PortfolioSnapshot | None = None
