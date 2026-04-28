from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrategyPositionSnapshot(BaseModel):
    quantity: float = 0.0
    average_entry_price: float | None = None
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class StrategyContext(BaseModel):
    """
    Internal strategy evaluation input for one symbol at one completed bar.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    run_id: UUID
    symbol: str

    evaluation_timestamp: datetime
    bar_timestamp: datetime

    bars: list[Any] = Field(default_factory=list)

    features: dict[str, Any] = Field(default_factory=dict)
    position: StrategyPositionSnapshot = Field(default_factory=StrategyPositionSnapshot)
    state: dict[str, Any] = Field(default_factory=dict)
