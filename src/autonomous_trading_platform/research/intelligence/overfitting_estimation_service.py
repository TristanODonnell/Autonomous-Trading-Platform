"""
Overfitting estimation service (TASK-2.5).

Builds a higher-level, aggregated overfit probability and risk band from
the individual TASK-2.4 heuristic indicators.  Also incorporates regime
profile signals not accessible inside the validation framework.

All estimation is heuristic and deterministic — no opaque ML classifiers.

Risk bands:
  LOW      overfit_probability < 0.30  — unlikely overfit
  MEDIUM   0.30 <= overfit_probability < 0.55  — moderate concern
  HIGH     0.55 <= overfit_probability < 0.75  — significant risk
  CRITICAL overfit_probability >= 0.75  — very high risk
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    _NEUTRAL,
    ResearchFeatureVector,
    _clamp,
)


class OverfitRiskBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_probability(cls, prob: float) -> OverfitRiskBand:
        if prob < 0.30:
            return cls.LOW
        if prob < 0.55:
            return cls.MEDIUM
        if prob < 0.75:
            return cls.HIGH
        return cls.CRITICAL


@dataclass(frozen=True)
class OverfitEstimate:
    """
    Aggregated overfitting estimate for a strategy candidate.

    overfit_probability is a [0,1] risk score.
    risk_band summarises the probability into an actionable category.
    """

    strategy_id: str
    overfit_probability: float
    risk_band: OverfitRiskBand
    warning_summary: list[str]
    dominant_indicator: str | None  # highest-contributing indicator
    indicators: dict[str, float | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "overfit_probability": round(self.overfit_probability, 4),
            "risk_band": self.risk_band.value,
            "warning_summary": self.warning_summary,
            "dominant_indicator": self.dominant_indicator,
            "indicators": {
                k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.indicators.items()
            },
        }


class OverfittingEstimationService:
    """
    Aggregates overfitting indicators from the research feature vector into
    a single probability and risk band.

    This builds on the per-indicator scores already computed by TASK-2.4's
    OverfittingAnalyzer.  The feature vector contains these as normalised
    [0,1] values where *higher = less overfitting risk*.

    We invert them back to risk scores (1 - value) for aggregation.

    Additional regime-level overfit signals from the regime profile:
    - regime_sensitivity_norm: low = regime-concentrated alpha
    - regime_is_robust: 0.0 = regime failure

    Weights reflect severity of overfitting signal type.
    """

    # field_name → (weight_raw, label)
    # These are "goodness" fields; we invert them for risk aggregation.
    _INDICATOR_WEIGHTS: dict[str, tuple[float, str]] = {
        "overfit_train_test_degradation": (0.25, "train/test degradation"),
        "overfit_fold_instability": (0.20, "fold instability"),
        "overfit_mc_instability": (0.15, "MC instability"),
        "overfit_regime_concentration": (0.15, "regime concentration"),
        "overfit_parameter_fragility": (0.10, "parameter fragility"),
        "overfit_narrow_period_alpha": (0.08, "narrow period alpha"),
        "overfit_low_trade_count": (0.07, "low trade count"),
    }

    # Regime-level overfit signals (additional layer)
    _REGIME_INDICATOR_WEIGHTS: dict[str, tuple[float, str]] = {
        "regime_sensitivity_norm": (0.60, "regime sensitivity"),
        "regime_is_robust": (0.40, "regime robustness"),
    }
    _REGIME_BLEND = 0.15  # 15% weight for regime overfit signals in final blend

    def __init__(
        self,
        *,
        regime_blend: float = 0.15,
    ) -> None:
        self._regime_blend = _clamp(regime_blend)

    def estimate(
        self,
        vector: ResearchFeatureVector,
    ) -> OverfitEstimate:
        fv = vector.as_dict()
        warnings: list[str] = []

        # --- Primary overfit signals ---
        primary_risk, primary_weight, primary_breakdown = self._aggregate_indicators(
            fv, vector, self._INDICATOR_WEIGHTS
        )

        # --- Regime-based overfit signals ---
        regime_risk, regime_weight, _ = self._aggregate_indicators(
            fv, vector, self._REGIME_INDICATOR_WEIGHTS
        )

        # --- Blend ---
        if primary_weight > 0 and regime_weight > 0:
            blend_primary = 1.0 - self._regime_blend
            overfit_prob = _clamp(blend_primary * primary_risk + self._regime_blend * regime_risk)
        elif primary_weight > 0:
            overfit_prob = primary_risk
        elif regime_weight > 0:
            overfit_prob = regime_risk
        else:
            overfit_prob = _NEUTRAL

        # --- Dominant indicator ---
        dominant = (
            max(primary_breakdown, key=lambda k: primary_breakdown[k])
            if primary_breakdown
            else None
        )

        # --- Warnings ---
        risk_band = OverfitRiskBand.from_probability(overfit_prob)
        if risk_band == OverfitRiskBand.CRITICAL:
            warnings.append(
                f"CRITICAL overfitting risk ({overfit_prob:.2f}) — strategy very likely overfitted."
            )
        elif risk_band == OverfitRiskBand.HIGH:
            warnings.append(
                f"High overfitting risk ({overfit_prob:.2f}) — review indicators carefully."
            )
        elif risk_band == OverfitRiskBand.MEDIUM:
            warnings.append(f"Moderate overfitting risk ({overfit_prob:.2f}) — monitor closely.")

        if dominant and primary_breakdown.get(dominant, 0) > 0.5:
            warnings.append(
                f"Dominant overfit signal: {dominant} ({primary_breakdown[dominant]:.2f})."
            )

        # --- Full indicator snapshot for reporting ---
        indicators: dict[str, float | None] = {}
        for field_name, (_, label) in {
            **self._INDICATOR_WEIGHTS,
            **self._REGIME_INDICATOR_WEIGHTS,
        }.items():
            val = fv.get(field_name)
            has_data = self._has_data(vector, field_name)
            if has_data and val is not None:
                # Return as risk score (1 - goodness)
                indicators[label] = round(1.0 - val, 4)
            else:
                indicators[label] = None

        return OverfitEstimate(
            strategy_id=vector.strategy_id,
            overfit_probability=overfit_prob,
            risk_band=risk_band,
            warning_summary=warnings,
            dominant_indicator=dominant,
            indicators=indicators,
        )

    def _aggregate_indicators(
        self,
        fv: dict[str, float],
        vector: ResearchFeatureVector,
        specs: dict[str, tuple[float, str]],
    ) -> tuple[float, float, dict[str, float]]:
        """Aggregate risk from 'goodness' fields (invert them first)."""
        total_weight = 0.0
        weighted_risk = 0.0
        breakdown: dict[str, float] = {}

        for field_name, (weight, label) in specs.items():
            if not self._has_data(vector, field_name):
                continue
            goodness = fv.get(field_name, _NEUTRAL)
            risk = _clamp(1.0 - goodness)
            total_weight += weight
            weighted_risk += weight * risk
            breakdown[label] = risk

        if total_weight == 0.0:
            return 0.0, 0.0, {}

        return _clamp(weighted_risk / total_weight), total_weight, breakdown

    def _has_data(self, vector: ResearchFeatureVector, field_name: str) -> bool:
        for f in vector.fields:
            if f.name == field_name:
                return f.has_data
        return False
