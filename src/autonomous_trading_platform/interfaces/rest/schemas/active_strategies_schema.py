from datetime import datetime
from decimal import Decimal
from typing import Literal

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
