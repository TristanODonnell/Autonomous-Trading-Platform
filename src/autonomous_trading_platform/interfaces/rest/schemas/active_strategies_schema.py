from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


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
