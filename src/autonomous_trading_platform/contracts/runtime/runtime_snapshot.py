from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


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
    datasets: list[DatasetVersionEntry] = []
    recent_activity: list[RecentActivityEntry] = []
    experiments: list[ExperimentEntry] = []
