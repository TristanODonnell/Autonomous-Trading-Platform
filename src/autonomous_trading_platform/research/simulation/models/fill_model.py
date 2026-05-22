from __future__ import annotations

import enum
from dataclasses import dataclass


class MarketFillPolicy(enum.StrEnum):
    CURRENT_CLOSE = "current_close"
    NEXT_OPEN = "next_open"


@dataclass(slots=True)
class SimulatedFillModelConfig:
    market_fill_policy: MarketFillPolicy = MarketFillPolicy.CURRENT_CLOSE
    # Number of bars to wait before filling a market order.
    # latency_bars=0 → same-bar close execution (no delay).
    # latency_bars=1 → next-bar open execution (recommended for realism; removes look-ahead bias).
    # latency_bars=N → fill at bar N+latency_bars open.
    latency_bars: int = 0

    def __post_init__(self) -> None:
        if self.latency_bars < 0:
            raise ValueError(f"latency_bars must be >= 0, got {self.latency_bars}")
