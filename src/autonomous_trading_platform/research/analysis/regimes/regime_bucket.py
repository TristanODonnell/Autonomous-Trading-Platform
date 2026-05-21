from __future__ import annotations

from dataclasses import dataclass

REGIME_DIMENSIONS: tuple[str, ...] = (
    "trend",
    "volatility",
    "liquidity",
    "mean_reversion",
    "risk",
)

REGIME_COLUMN_MAP: dict[str, str] = {
    "trend": "regime_trend",
    "volatility": "regime_volatility",
    "liquidity": "regime_liquidity",
    "mean_reversion": "regime_mean_reversion",
    "risk": "regime_risk",
}

REGIME_LABELS: dict[str, tuple[str, ...]] = {
    "trend": ("bull", "bear", "sideways"),
    "volatility": ("high_volatility", "normal_volatility", "low_volatility"),
    "liquidity": ("high_liquidity", "normal_liquidity", "low_liquidity"),
    "mean_reversion": ("trending", "mean_reverting", "undefined"),
    "risk": ("risk_on", "risk_off", "neutral"),
}


@dataclass(frozen=True)
class RegimeBucket:
    dimension: str
    label: str

    def __post_init__(self) -> None:
        if self.dimension not in REGIME_LABELS:
            raise ValueError(f"Unknown regime dimension: {self.dimension!r}")

    @property
    def column_name(self) -> str:
        return REGIME_COLUMN_MAP[self.dimension]

    @property
    def display_name(self) -> str:
        return f"{self.dimension}:{self.label}"

    @classmethod
    def all_for_dimension(cls, dimension: str) -> list[RegimeBucket]:
        if dimension not in REGIME_LABELS:
            raise ValueError(f"Unknown regime dimension: {dimension!r}")
        return [cls(dimension=dimension, label=label) for label in REGIME_LABELS[dimension]]

    @classmethod
    def all(cls) -> list[RegimeBucket]:
        return [
            cls(dimension=dim, label=label)
            for dim, labels in REGIME_LABELS.items()
            for label in labels
        ]
