from __future__ import annotations

import enum
from dataclasses import dataclass


class TrendRegime(enum.StrEnum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"


class VolatilityRegime(enum.StrEnum):
    HIGH_VOLATILITY = "high_volatility"
    NORMAL_VOLATILITY = "normal_volatility"
    LOW_VOLATILITY = "low_volatility"


class LiquidityRegime(enum.StrEnum):
    HIGH_LIQUIDITY = "high_liquidity"
    NORMAL_LIQUIDITY = "normal_liquidity"
    LOW_LIQUIDITY = "low_liquidity"


class MeanReversionRegime(enum.StrEnum):
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    UNDEFINED = "undefined"


class RiskRegime(enum.StrEnum):
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class TrendClassification:
    regime: TrendRegime
    confidence: float
    ma_short: float | None
    ma_long: float | None
    rolling_return_20d: float | None


@dataclass(frozen=True)
class VolatilityClassification:
    regime: VolatilityRegime
    confidence: float
    realized_volatility: float | None
    percentile: float | None
    threshold_high: float
    threshold_low: float


@dataclass(frozen=True)
class LiquidityClassification:
    regime: LiquidityRegime
    confidence: float
    avg_dollar_volume: float | None
    percentile: float | None


@dataclass(frozen=True)
class MeanReversionClassification:
    regime: MeanReversionRegime
    confidence: float
    zscore_std: float | None
    trend_strength: float | None


@dataclass(frozen=True)
class RegimeClassificationResult:
    """Full multi-dimensional regime classification for a single bar."""

    trend: TrendClassification
    volatility: VolatilityClassification
    liquidity: LiquidityClassification
    mean_reversion: MeanReversionClassification
    risk: RiskRegime

    def as_dict(self) -> dict[str, object]:
        return {
            "regime_trend": self.trend.regime.value,
            "regime_trend_confidence": self.trend.confidence,
            "trend_ma_short": self.trend.ma_short,
            "trend_ma_long": self.trend.ma_long,
            "trend_rolling_return_20d": self.trend.rolling_return_20d,
            "regime_volatility": self.volatility.regime.value,
            "regime_volatility_confidence": self.volatility.confidence,
            "volatility_realized": self.volatility.realized_volatility,
            "volatility_percentile": self.volatility.percentile,
            "volatility_threshold_high": self.volatility.threshold_high,
            "volatility_threshold_low": self.volatility.threshold_low,
            "regime_liquidity": self.liquidity.regime.value,
            "regime_liquidity_confidence": self.liquidity.confidence,
            "liquidity_avg_dollar_volume": self.liquidity.avg_dollar_volume,
            "liquidity_percentile": self.liquidity.percentile,
            "regime_mean_reversion": self.mean_reversion.regime.value,
            "regime_mean_reversion_confidence": self.mean_reversion.confidence,
            "mean_reversion_zscore_std": self.mean_reversion.zscore_std,
            "mean_reversion_trend_strength": self.mean_reversion.trend_strength,
            "regime_risk": self.risk.value,
        }
