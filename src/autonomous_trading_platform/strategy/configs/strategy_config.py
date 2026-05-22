from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from autonomous_trading_platform.strategy.registry import get_registry


class StrategyConfig(BaseModel):
    type: str

    strategy_id: str

    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type_must_be_registered(cls, v: str) -> str:
        registry = get_registry()
        if not registry.strategy_exists(v):
            known = registry.list_strategy_types()
            raise ValueError(f"Unknown strategy type {v!r}. Known types: {known}")
        return v

    @model_validator(mode="after")
    def _parameters_must_be_valid(self) -> StrategyConfig:
        self.parameters = get_registry().normalize_parameters(self.type, self.parameters)
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
