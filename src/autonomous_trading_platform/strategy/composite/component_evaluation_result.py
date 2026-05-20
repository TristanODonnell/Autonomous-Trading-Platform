from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autonomous_trading_platform.contracts.common.enums import SignalDirection


class ComponentEvaluationResult(BaseModel):
    """Structured result emitted by composite orchestration steps."""

    model_config = ConfigDict(frozen=True)

    component_id: str
    component_name: str
    component_type: str
    passed: bool | None = None
    direction: SignalDirection | None = None
    confidence: float = 0.0
    reason: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    parameters: dict[str, Any] = Field(default_factory=dict)
