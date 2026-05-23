from __future__ import annotations

import enum
from dataclasses import dataclass, field


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
    # Maximum fraction of bar volume that may be filled in a single bar (R-04).
    # None → no cap (legacy behavior, unlimited liquidity assumption).
    # 0.10 → at most 10% of bar volume per bar; remainder carries forward to future bars.
    # Must be > 0 and <= 1.0 when set.
    max_volume_participation_rate: float | None = field(default=None)
    # Probability that executable quantity results in only a partial fill (F-01).
    # None → disabled; all executable quantity fills deterministically (R-04 behavior).
    # 0.30 → 30% chance that execution only partially completes; remainder carries forward.
    # Applied AFTER participation cap computation. Must be > 0 and <= 1.0 when set.
    partial_fill_probability: float | None = field(default=None)
    # Fill-size fraction bounds when partial_fill_probability triggers a partial fill.
    # Actual fill = floor(executable_qty * sampled_fraction),
    # where sampled_fraction ∈ [partial_fill_min_fraction, partial_fill_max_fraction].
    # Must satisfy 0 < min_fraction < max_fraction <= 1.0.
    partial_fill_min_fraction: float = field(default=0.10)
    partial_fill_max_fraction: float = field(default=0.75)

    def __post_init__(self) -> None:
        if self.latency_bars < 0:
            raise ValueError(f"latency_bars must be >= 0, got {self.latency_bars}")
        if self.max_volume_participation_rate is not None:
            if self.max_volume_participation_rate <= 0:
                raise ValueError(
                    f"max_volume_participation_rate must be > 0, got {self.max_volume_participation_rate}"
                )
            if self.max_volume_participation_rate > 1.0:
                raise ValueError(
                    f"max_volume_participation_rate must be <= 1.0, got {self.max_volume_participation_rate}"
                )
        if self.partial_fill_probability is not None and (
            self.partial_fill_probability <= 0 or self.partial_fill_probability > 1.0
        ):
            raise ValueError(
                f"partial_fill_probability must be > 0 and <= 1.0, got {self.partial_fill_probability}"
            )
        if self.partial_fill_min_fraction <= 0:
            raise ValueError(
                f"partial_fill_min_fraction must be > 0, got {self.partial_fill_min_fraction}"
            )
        if self.partial_fill_max_fraction > 1.0:
            raise ValueError(
                f"partial_fill_max_fraction must be <= 1.0, got {self.partial_fill_max_fraction}"
            )
        if self.partial_fill_min_fraction >= self.partial_fill_max_fraction:
            raise ValueError(
                f"partial_fill_min_fraction ({self.partial_fill_min_fraction}) must be "
                f"< partial_fill_max_fraction ({self.partial_fill_max_fraction})"
            )
