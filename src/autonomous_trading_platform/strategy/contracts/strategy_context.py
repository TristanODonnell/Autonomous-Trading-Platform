from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
