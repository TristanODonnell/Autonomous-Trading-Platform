from __future__ import annotations

import enum
from dataclasses import dataclass


class MarketFillPolicy(enum.StrEnum):
    CURRENT_CLOSE = "current_close"
    NEXT_OPEN = "next_open"


@dataclass(slots=True)
class SimulatedFillModelConfig:
    market_fill_policy: MarketFillPolicy = MarketFillPolicy.CURRENT_CLOSE
