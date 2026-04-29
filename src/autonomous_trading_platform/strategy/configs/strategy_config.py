from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class StrategyConfig(BaseModel):
    type: Literal[
        "stub",
        "intentional_loser",
        "moving_average_crossover",
        "mean_reversion",
        "momentum",
        "factor_based",
    ]

    strategy_id: str

    parameters: dict[str, Any] = Field(default_factory=dict)

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
