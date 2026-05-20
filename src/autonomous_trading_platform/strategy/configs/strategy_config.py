from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from autonomous_trading_platform.strategy.catalog import strategy_type_exists


class StrategyConfig(BaseModel):
    type: str

    strategy_id: str

    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type_must_be_cataloged(cls, v: str) -> str:
        if not strategy_type_exists(v):
            from autonomous_trading_platform.strategy.catalog import list_strategy_types

            known = list_strategy_types()
            raise ValueError(f"Unknown strategy type {v!r}. Known types: {known}")
        return v

    @model_validator(mode="after")
    def _parameters_must_be_valid(self) -> StrategyConfig:
        from autonomous_trading_platform.research.config.strategy_parameter_validators import (
            validate_strategy_parameters,
        )

        validate_strategy_parameters(self.type, self.parameters)
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
