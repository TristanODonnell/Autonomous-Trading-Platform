from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

RiskTolerance = Literal["low", "medium", "high"]
RebalanceFrequency = Literal["daily", "weekly", "monthly"]


SlippageModel = Literal["fixed", "volume_based", "spread"]
TransactionCostModel = Literal["per_share", "per_trade", "notional_pct"]


class OperatorSettingsResponse(BaseModel):
    risk_tolerance: RiskTolerance
    max_drawdown_limit: Decimal
    max_strategy_drawdown: Decimal
    rebalance_frequency: RebalanceFrequency
    auto_promote_enabled: bool
    per_strategy_cap: Decimal
    target_portfolio_volatility: Decimal
    slippage_model: SlippageModel
    transaction_cost_model: TransactionCostModel


class OperatorSettingsUpdateRequest(BaseModel):
    risk_tolerance: RiskTolerance | None = None
    max_drawdown_limit: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    max_strategy_drawdown: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    rebalance_frequency: RebalanceFrequency | None = None
    auto_promote_enabled: bool | None = None
    per_strategy_cap: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("1"))
    target_portfolio_volatility: Decimal | None = Field(
        default=None, gt=Decimal("0"), le=Decimal("1")
    )
    slippage_model: SlippageModel | None = None
    transaction_cost_model: TransactionCostModel | None = None
    reason: str | None = Field(default=None, max_length=500)


class RiskProfileResponse(BaseModel):
    summary: str
    bullets: list[str]


class StrategyDrawdownOverrideResponse(BaseModel):
    strategy_id: str
    max_drawdown_allowed: Decimal
    updated_by: str
    reason: str


class AssetPositionCapResponse(BaseModel):
    asset: str
    max_position_size_usd: Decimal


class CostModelConfigurationResponse(BaseModel):
    fixed_commission: Decimal
    per_share_commission: Decimal
    default_half_spread: Decimal
    extra_slippage_bps: Decimal
    slippage_rate: Decimal


class AdvancedSettingsResponse(BaseModel):
    read_only: bool
    per_strategy_max_drawdown_overrides: list[StrategyDrawdownOverrideResponse]
    position_size_caps_per_asset: list[AssetPositionCapResponse]
    cost_model_configuration: CostModelConfigurationResponse
    metadata: dict[str, Any] = Field(default_factory=dict)
