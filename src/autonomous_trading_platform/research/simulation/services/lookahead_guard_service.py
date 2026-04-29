from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class LookaheadGuardService:
    def assert_historical_only(
        self,
        *,
        symbol: str,
        simulation_timestamp: datetime,
        bars: Sequence[Any],
    ) -> None:
        if not bars:
            return

        timestamps = [bar.timestamp for bar in bars]

        max_context_timestamp = max(timestamps)

        if max_context_timestamp >= simulation_timestamp:
            raise ValueError(
                "Lookahead bias violation: "
                f"symbol={symbol}, "
                f"max_context_timestamp={max_context_timestamp}, "
                f"simulation_timestamp={simulation_timestamp}"
            )

    def assert_timeline_strictly_increasing(
        self,
        *,
        timeline: list[datetime],
    ) -> None:
        if timeline != sorted(timeline):
            raise ValueError("Simulation timeline must be strictly chronological.")

        if len(timeline) != len(set(timeline)):
            raise ValueError("Simulation timeline contains duplicate timestamps.")
