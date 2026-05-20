from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext


class ComponentExecutionContext(BaseModel):
    """Per-symbol composite execution state for one completed bar."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    strategy_context: StrategyContext
    closes: list[float]
    volumes: list[float] | None = None
    returns: list[float] = Field(default_factory=list)
    indicator_outputs: dict[str, Any] = Field(default_factory=dict)
    indicator_cache: dict[tuple[str, int], Any] = Field(default_factory=dict)
