from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class LookaheadGuardService:
    """
    Prevents simulation strategies from seeing bars that should not be visible
    at the current simulation timestamp.

    Current rule:
    - context bars must be strictly before simulation_timestamp
    - current bar is not visible
    - future bars are never visible
    """

    def assert_historical_only(
        self,
        *,
        symbol: str,
        simulation_timestamp: datetime,
        bars: Sequence[Any],
    ) -> None:
        if not bars:
            return

        timestamps: list[datetime] = []

        for bar in bars:
            timestamp = getattr(bar, "timestamp", None)

            if timestamp is None:
                raise ValueError(
                    "Lookahead guard cannot validate bar without timestamp: "
                    f"symbol={symbol}, simulation_timestamp={simulation_timestamp}"
                )

            timestamps.append(timestamp)

        max_context_timestamp = max(timestamps)

        if max_context_timestamp >= simulation_timestamp:
            raise ValueError(
                "Lookahead bias violation: strategy context contains current/future bars. "
                f"symbol={symbol}, "
                f"max_context_timestamp={max_context_timestamp}, "
                f"simulation_timestamp={simulation_timestamp}"
            )

    def filter_historical_only(
        self,
        *,
        simulation_timestamp: datetime,
        bars: Sequence[Any],
    ) -> list[Any]:
        """
        Returns only bars strictly before the simulation timestamp.
        """
        return [
            bar
            for bar in bars
            if getattr(bar, "timestamp", None) is not None and bar.timestamp < simulation_timestamp
        ]

    def assert_timeline_strictly_increasing(
        self,
        *,
        timeline: Sequence[datetime],
    ) -> None:
        if not timeline:
            return

        for previous_timestamp, current_timestamp in zip(timeline, timeline[1:], strict=False):
            if current_timestamp <= previous_timestamp:
                raise ValueError(
                    "Simulation timeline must be strictly increasing. "
                    f"previous_timestamp={previous_timestamp}, "
                    f"current_timestamp={current_timestamp}"
                )
